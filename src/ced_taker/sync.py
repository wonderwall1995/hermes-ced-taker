"""동기화: 볼트의 CED 변화를 DB에 반영한다.

한 제품 폴더에 대해 다음을 한다.
1. 상태 파일을 읽고 해시를 검증한다 (state.py).
2. 해시가 맞는 문서만 파싱해서 저장한다 (parser.py, store.py).
3. 맞지 않거나 깨진 문서는 저장하지 않고, 이전에 정상으로 들어온 내용을 그대로 둔다.
4. 파일이 없어졌거나 상태 파일에서 빠진 문서는 비활성으로 표시한다. 지우지 않는다.
볼트의 원문은 수정하지 않는다. 이력에는 해시와 사유 코드만 남기고 본문은 남기지 않는다.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from . import state
from .parser import ParseError, parse_document
from .state import StateError, scan_products, verify_product

# 이력에 쓰는 결과 이름 (store의 NEW 등과 같은 칸에 들어간다)
PARSE_ERROR = "parse_error"
PRODUCT_MISMATCH = "product_mismatch"
STATE_ERROR = "state_error"
BAD_ENCODING = "bad_encoding"
NOT_IN_STATE = "not_in_state"

_NAME_LIMIT = 200  # 안전하지 않은 파일명이 이력을 부풀리지 않게 자른다


@dataclass(frozen=True)
class Issue:
    """저장하지 못했거나 주의가 필요한 파일. name은 상태 파일에 적힌 이름이다."""

    name: str
    outcome: str
    reason: str = None


@dataclass(frozen=True)
class SyncReport:
    product: str
    counts: dict  # 결과(new, updated, unchanged, reactivated, inactive)별 개수
    issues: tuple
    state_error: str = None  # 제품 단위 오류의 사유 코드. 있으면 DB는 건드리지 않았다.


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _bump(counts, key):
    counts[key] = counts.get(key, 0) + 1


def sync_product(db, product_dir, now=None, sleep=time.sleep):
    """제품 폴더 하나를 동기화한다. 폴더 이름이 곧 제품 이름이다."""
    now = now or utc_now()
    product = Path(product_dir).name
    counts, issues = {}, []

    try:
        check = verify_product(product_dir, sleep=sleep)
    except StateError as err:
        db.record_event(product, None, None, STATE_ERROR, err.reason, now)
        return SyncReport(product, counts, (), err.reason)

    listed = set()
    for item in check.files:
        if item.status == state.UNSAFE:
            name = item.name[:_NAME_LIMIT]
            db.record_event(product, name, None, state.UNSAFE, None, now)
            issues.append(Issue(name, state.UNSAFE))
            continue

        name = item.name[: -len(".md")]
        listed.add(name)

        if item.status == state.MISMATCH:
            db.record_event(product, name, item.actual, state.MISMATCH, None, now)
            issues.append(Issue(item.name, state.MISMATCH))
        elif item.status == state.MISSING:
            if db.mark_inactive(product, name, state.MISSING, now):
                _bump(counts, "inactive")
            elif db.get_note(product, name) is None:  # 한 번도 들어온 적 없는 파일
                db.record_event(product, name, None, state.MISSING, None, now)
            issues.append(Issue(item.name, state.MISSING))
        else:
            _store_match(db, product, name, item, now, counts, issues)

    for name in sorted(db.active_names(product) - listed):
        if db.mark_inactive(product, name, NOT_IN_STATE, now):
            _bump(counts, "inactive")
            issues.append(Issue(name + ".md", "inactive", NOT_IN_STATE))

    return SyncReport(product, counts, tuple(issues), None)


def _store_match(db, product, name, item, now, counts, issues):
    """해시가 맞는 문서 하나를 파싱해 저장한다. 실패하면 이전 내용을 그대로 둔다."""
    try:
        note = parse_document(item.data.decode("utf-8"), name)
    except UnicodeDecodeError:
        reason = BAD_ENCODING
    except ParseError as err:
        reason = str(err)
    else:
        if note.product != product:  # 다른 제품의 이름 공간을 오염시키지 않는다
            db.record_event(product, name, item.actual, PRODUCT_MISMATCH, None, now)
            issues.append(Issue(item.name, PRODUCT_MISMATCH))
            return
        _bump(counts, db.upsert_note(note, item.actual, now))
        return
    db.record_event(product, name, item.actual, PARSE_ERROR, reason, now)
    issues.append(Issue(item.name, PARSE_ERROR, reason))


def sync_vault(db, vault, now=None, sleep=time.sleep):
    """볼트의 CED 폴더에 있는 모든 제품을 동기화한다."""
    return [sync_product(db, p, now, sleep) for p in scan_products(Path(vault) / "CED")]
