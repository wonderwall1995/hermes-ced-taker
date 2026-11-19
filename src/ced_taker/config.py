"""로컬 설정. 볼트 위치와 저장소별 제품 제한을 환경 변수 또는 로컬 설정 파일에서 찾는다.

개인 경로는 저장소에 적지 않는다. 설정 파일은 Git에서 제외된 local/ 폴더에 둔다.
오류에는 사유 코드만 담고 경로는 담지 않는다.
"""

import json
import os
from pathlib import Path

ENV_VAULT = "CED_TAKER_VAULT"
ENV_CONFIG = "CED_TAKER_CONFIG"
ENV_DB = "CED_TAKER_DB"

VAULT_NOT_CONFIGURED = "vault_not_configured"
VAULT_NOT_FOUND = "vault_not_found"
CONFIG_UNREADABLE = "config_unreadable"


class ConfigError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def default_config_path():
    """저장소 안의 local/config.json (Git 제외)."""
    return Path(__file__).resolve().parents[2] / "local" / "config.json"


def default_db_path(env=None):
    """DB 위치. CED_TAKER_DB가 있으면 그것, 없으면 Hermes 홈 아래의 ced-taker 폴더."""
    env = os.environ if env is None else env
    explicit = env.get(ENV_DB, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = env.get("HERMES_HOME", "").strip() or "~/.hermes"
    return Path(home).expanduser() / "ced-taker" / "ced.db"


def _load_file(config_path):
    """설정 파일을 읽어 dict로 돌려준다. 파일이 없으면 빈 dict, 읽을 수 없거나 객체가 아니면 ConfigError."""
    try:
        raw = Path(config_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError):
        raise ConfigError(CONFIG_UNREADABLE) from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise ConfigError(CONFIG_UNREADABLE) from None
    if not isinstance(data, dict):
        raise ConfigError(CONFIG_UNREADABLE)
    return data


def _config_path(env, config_path):
    return config_path or env.get(ENV_CONFIG) or default_config_path()


def _vault_from_file(config_path):
    value = _load_file(config_path).get("vault")
    return value if isinstance(value, str) and value.strip() else None


def resolve_vault(env=None, config_path=None):
    """볼트 폴더를 돌려준다. 환경 변수가 설정 파일보다 우선한다."""
    env = os.environ if env is None else env
    value = env.get(ENV_VAULT, "").strip() or None
    if value is None:
        value = _vault_from_file(_config_path(env, config_path))
    if value is None:
        raise ConfigError(VAULT_NOT_CONFIGURED)
    vault = Path(value).expanduser()
    if not vault.is_dir():
        raise ConfigError(VAULT_NOT_FOUND)
    return vault


def _product_list(value):
    """설정 값(문자열 또는 문자열 목록)을 제품 이름 목록으로 바꾼다. 비었거나 형식이 틀리면 ConfigError."""
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items or not all(isinstance(v, str) and v.strip() for v in items):
        raise ConfigError(CONFIG_UNREADABLE)
    return list(dict.fromkeys(v.strip() for v in items))  # 순서를 지키며 중복을 없앤다


def resolve_products(repo, env=None, config_path=None):
    """저장소가 참조할 제품 목록을 돌려준다. None은 제한이 없다는 뜻이다(볼트의 모든 제품).

    기본은 모든 저장소가 모든 CED 제품을 참조한다. 설정 파일의 "repos"는 그 기본을 제한하는 데만 쓴다.
    {"owner/name": "제품"} 또는 {"owner/name": ["제품 A", "제품 B"]}. 저장소 이름은 대소문자와 앞뒤 공백을 무시한다.
    "repos" 안의 항목이 하나라도 틀리면(비어 있거나 형식이 다르면) 어느 저장소에 대해서든 ConfigError이다.
    """
    env = os.environ if env is None else env
    repos = _load_file(_config_path(env, config_path)).get("repos", {})
    if not isinstance(repos, dict):
        raise ConfigError(CONFIG_UNREADABLE)
    limits = {}
    for name, value in repos.items():
        if not isinstance(name, str):
            raise ConfigError(CONFIG_UNREADABLE)
        limits[name.strip().lower()] = _product_list(value)
    wanted = repo.strip().lower() if isinstance(repo, str) else ""
    return limits.get(wanted)
