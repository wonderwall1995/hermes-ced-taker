"""참고 문서 공급자 계약의 공급자 쪽.

다른 프로그램이 명령으로 이 프로그램을 실행하고, 표준 입력으로 JSON을 주면, 표준 출력으로 JSON을 돌려준다.
호출하는 쪽이 누구인지 모른다. 계약은 docs/11-19-provider-design.md 에 있다.

요청: {"version": 1, "repo": "owner/name", "title": "...", "description": "...", "diff": "..."}
응답: {"version": 1, "docs": [{"id", "title", "text"}], "notices": ["..."]}

- repo가 어떤 제품에도 연결돼 있지 않으면 볼트와 DB를 열지 않고 빈 응답을 돌려준다. 이것은 오류가 아니다.
- 오류는 ProviderError로 알리고, 호출하는 쪽에는 종료 코드 1과 사유 코드만 보인다. 본문은 담지 않는다.
"""

import json
import os

from .config import ConfigError, default_db_path, resolve_product, resolve_vault
from .funnel import explain, sync_notice
from .store import Store
from .sync import product_dir, sync_product

VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024 * 1024  # 호출하는 쪽의 diff 상한(수십만 자)보다 넉넉하다.

BAD_REQUEST = "bad_request"
REQUEST_TOO_LARGE = "request_too_large"
UNSUPPORTED_VERSION = "unsupported_version"

NO_PRODUCT_FOLDER_NOTICE = "연결된 제품의 CED 폴더를 찾지 못해 해설을 만들지 못함."


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
    product = resolve_product(request["repo"], env, config_path)
    if product is None:
        return empty_response()

    vault = resolve_vault(env, config_path)
    pdir = product_dir(vault, product)
    if pdir is None:
        return empty_response([NO_PRODUCT_FOLDER_NOTICE])

    db = Store.open(db_path or default_db_path(env))
    try:
        kwargs = {} if sleep is None else {"sleep": sleep}
        report = sync_product(db, pdir, now, **kwargs)
        # PR 제목도 사람이 쓴 설명의 일부로 본다.
        description = "\n".join(part for part in (request["title"], request["description"]) if part)
        result = explain(db, product, request["diff"], description, now=now)
    finally:
        db.close()

    notice = sync_notice(report)
    return {"version": VERSION, "docs": list(result.docs), "notices": [notice] if notice else []}


__all__ = ["ProviderError", "ConfigError", "parse_request", "provide", "empty_response"]
