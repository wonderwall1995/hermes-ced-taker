"""참고 문서 공급자 계약의 공급자 쪽.

다른 프로그램이 명령으로 이 프로그램을 실행하고, 표준 입력으로 JSON을 주면, 표준 출력으로 JSON을 돌려준다.
호출하는 쪽이 누구인지 모른다. 약속의 예는 contracts/ 에 있다(원본은 hermes-controller).

요청: {"version": 1, "repo": "owner/name", "title": "...", "description": "...", "diff": "..."}
응답: {"version": 1, "docs": [{"id", "title", "text"}], "notices": ["..."]}

- 기본은 모든 저장소가 볼트의 모든 CED 제품을 참조한다. 설정(repos)에 적힌 저장소만 적힌 제품들로 제한한다.
- 볼트 설정이 없으면 저장소와 상관없이 오류이다.
- 같은 컬럼명이 둘 이상의 제품에 걸리면 둘 다 돌려주고 notices로 경고한다.
- 오류는 ProviderError로 알리고, 호출하는 쪽에는 종료 코드 1과 사유 코드만 보인다. 본문은 담지 않는다.
"""

import json
import os

import pathlib

from .config import ConfigError, default_db_path, resolve_products, resolve_vault
from .funnel import explain, overlap_notice, sync_notice
from .state import scan_products
from .store import Store
from .sync import product_dir, sync_product

VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024 * 1024  # 호출하는 쪽의 diff 상한(수십만 자)보다 넉넉하다.

BAD_REQUEST = "bad_request"
REQUEST_TOO_LARGE = "request_too_large"
UNSUPPORTED_VERSION = "unsupported_version"

NO_PRODUCT_FOLDER_NOTICE = "설정에 적힌 제품의 CED 폴더를 찾지 못함."
NO_PRODUCTS_NOTICE = "볼트에 CED 제품이 없어 해설을 만들지 못함."


class ProviderError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def empty_response(notices=()):
    return {"version": VERSION, "docs": [], "notices": list(notices)}


def parse_request(raw):
    """표준 입력의 바이트 또는 문자열을 요청 dict로 바꾼다. 형식이 틀리면 ProviderError."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ProviderError(REQUEST_TOO_LARGE)
    try:
        request = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ProviderError(BAD_REQUEST) from None
    if not isinstance(request, dict) or not isinstance(request.get("version"), int) or isinstance(
        request.get("version"), bool
    ):
        raise ProviderError(BAD_REQUEST)
    if request["version"] != VERSION:
        raise ProviderError(UNSUPPORTED_VERSION)
    repo = request.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise ProviderError(BAD_REQUEST)
    cleaned = {"repo": repo}
    for key in ("title", "description", "diff"):
        value = request.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ProviderError(BAD_REQUEST)
        cleaned[key] = value
    return cleaned


def provide(request, env=None, config_path=None, db_path=None, now=None, sleep=None):
    """요청 하나를 처리해 응답 dict를 돌려준다.

    ConfigError, StoreError는 그대로 올린다(호출하는 쪽에는 오류로 보인다). 문서 단위 문제는 notices로 알린다.
    """
    env = os.environ if env is None else env
    limit = resolve_products(request["repo"], env, config_path)  # None이면 제한 없음(모든 제품)
    vault = resolve_vault(env, config_path)  # 모든 저장소가 대상이라 볼트 설정은 늘 필요하다

    notices = []
    if limit is None:
        dirs = scan_products(pathlib.Path(vault) / "CED")
        if not dirs:
            return empty_response([NO_PRODUCTS_NOTICE])
    else:
        dirs = [d for d in (product_dir(vault, name) for name in limit) if d is not None]
        if len(dirs) < len(limit):
            notices.append(NO_PRODUCT_FOLDER_NOTICE)
        if not dirs:
            return empty_response(notices)

    db = Store.open(db_path or default_db_path(env))
    try:
        kwargs = {} if sleep is None else {"sleep": sleep}
        reports = [sync_product(db, d, now, **kwargs) for d in dirs]
        # PR 제목도 사람이 쓴 설명의 일부로 본다.
        description = "\n".join(part for part in (request["title"], request["description"]) if part)
        result = explain(db, [d.name for d in dirs], request["diff"], description, now=now)
    finally:
        db.close()

    notices += [n for n in (sync_notice(reports), overlap_notice(result.overlaps)) if n]
    return {"version": VERSION, "docs": list(result.docs), "notices": notices}


__all__ = ["ProviderError", "ConfigError", "parse_request", "provide", "empty_response"]
