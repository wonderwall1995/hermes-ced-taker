"""상태 파일 읽기와 해시 검증.

제품 폴더의 .ced-state.json을 읽고, 거기에 적힌 .md 파일만 SHA-256으로 검증한다.
- 상태 파일에 없는 파일은 보지 않는다.
- 제품 폴더 밖을 가리키거나 일반 파일이 아닌 항목은 열지 않는다.
- 파일은 한 번만 읽는다. 해시를 낸 바이트를 그대로 넘겨서, 해시와 내용이 어긋나지 않게 한다.
- 상태 파일이 깨졌으면 잠시 기다려 다시 읽는다. 계산 도중 상태가 바뀌면 처음부터 다시 한다.
이 모듈은 원문을 수정하지 않고, 로그에는 사유 코드만 남긴다.
"""

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path

STATE_FILE = ".ced-state.json"
SUPPORTED_VERSION = 1

# 파일 검사 결과
MATCH = "match"  # 해시가 같다. data에 읽은 바이트가 있다.
MISMATCH = "mismatch"  # 해시가 다르다. 수정본이거나 전송 중이다.
MISSING = "missing"  # 상태 파일에는 있는데 실제 파일이 없다.
UNSAFE = "unsafe"  # 안전하지 않은 이름이거나 일반 파일이 아니다. 열지 않았다.

# 상태 파일 오류 사유 코드
STATE_MISSING = "state_missing"
STATE_UNREADABLE = "state_unreadable"
STATE_BAD_FORMAT = "state_bad_format"
STATE_UNSUPPORTED_VERSION = "state_unsupported_version"
STATE_CHANGING = "state_changing"

_PERMANENT = (STATE_MISSING, STATE_UNSUPPORTED_VERSION)  # 기다려도 나아지지 않는 오류
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class StateError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class FileCheck:
    name: str
    status: str
    expected: str
    actual: str = None
    data: bytes = None


@dataclass(frozen=True)
class ProductCheck:
    product: str
    files: tuple


def is_safe_name(name):
    """제품 폴더 바로 아래의 .md 파일 이름만 허용한다. 경로 구분자, 숨김 파일, 상위 경로는 거부한다."""
    return (
        isinstance(name, str)
        and len(name) > len(".md")
        and name.endswith(".md")
        and not name.startswith(".")
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name
    )


def _load_state(path):
    """상태 파일을 한 번 읽어 {파일명: 해시}를 돌려준다."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        raise StateError(STATE_MISSING) from None
    except OSError:
        raise StateError(STATE_UNREADABLE) from None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:  # JSON 오류와 UTF-8 오류 모두 포함
        raise StateError(STATE_BAD_FORMAT) from None
    if not isinstance(doc, dict) or not isinstance(doc.get("version"), int):
        raise StateError(STATE_BAD_FORMAT)
    if doc["version"] != SUPPORTED_VERSION:
        raise StateError(STATE_UNSUPPORTED_VERSION)
    files = doc.get("files")
    if not isinstance(files, dict):
        raise StateError(STATE_BAD_FORMAT)
    for name, sha in files.items():
        if not isinstance(sha, str) or not _SHA256.match(sha):
            raise StateError(STATE_BAD_FORMAT)
    return dict(files)


def read_state(product_dir, retries=3, wait=0.5, sleep=time.sleep):
    """상태 파일을 읽는다. 깨졌거나 쓰기 중이면 wait초 간격으로 최대 retries번 다시 읽는다."""
    path = Path(product_dir) / STATE_FILE
    attempt = 0
    while True:
        try:
            return _load_state(path)
        except StateError as err:
            if err.reason in _PERMANENT or attempt >= retries:
                raise
            attempt += 1
            sleep(wait)


def _check_file(product_dir, name, expected):
    if not is_safe_name(name):
        return FileCheck(name, UNSAFE, expected)
    path = os.path.join(product_dir, name)
    try:
        # 심볼릭 링크는 따라가지 않는다. O_NONBLOCK은 FIFO를 열 때 멈추지 않게 한다.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return FileCheck(name, MISSING, expected)
    except OSError:
        return FileCheck(name, UNSAFE, expected)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return FileCheck(name, UNSAFE, expected)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read()
    finally:
        os.close(fd)
    actual = hashlib.sha256(data).hexdigest()
    if actual == expected:
        return FileCheck(name, MATCH, expected, actual, data)
    return FileCheck(name, MISMATCH, expected, actual)


def verify_product(product_dir, retries=3, wait=0.5, recheck=2, sleep=time.sleep):
    """제품 하나를 검증한다. 상태 파일은 계산 전후로 읽어서 같을 때만 결과를 인정한다.

    다르면 최대 recheck번 처음부터 다시 하고, 그래도 다르면 StateError(STATE_CHANGING)를 낸다.
    """
    product_dir = Path(product_dir)
    for _ in range(recheck + 1):
        before = read_state(product_dir, retries, wait, sleep)
        files = tuple(_check_file(str(product_dir), name, before[name]) for name in sorted(before))
        if read_state(product_dir, retries, wait, sleep) == before:
            return ProductCheck(product_dir.name, files)
    raise StateError(STATE_CHANGING)


def scan_products(ced_dir):
    """CED 폴더에서 상태 파일이 있는 제품 폴더를 이름순으로 돌려준다. 숨김 폴더와 링크는 건너뛴다."""
    ced_dir = Path(ced_dir)
    if not ced_dir.is_dir():
        return []
    return [
        p
        for p in sorted(ced_dir.iterdir())
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".") and (p / STATE_FILE).is_file()
    ]
