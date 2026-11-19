"""로컬 설정. 볼트 위치와 저장소-제품 연결을 환경 변수 또는 로컬 설정 파일에서 찾는다.

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


def resolve_product(repo, env=None, config_path=None):
    """저장소 이름(owner/name)에 연결된 제품 이름을 돌려준다. 연결이 없으면 None.

    연결은 설정 파일의 "repos" 객체({"owner/name": "제품"})에 적는다. 저장소 이름은 대소문자를 구분하지 않는다.
    """
    env = os.environ if env is None else env
    repos = _load_file(_config_path(env, config_path)).get("repos", {})
    if not isinstance(repos, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in repos.items()):
        raise ConfigError(CONFIG_UNREADABLE)
    wanted = repo.strip().lower() if isinstance(repo, str) else ""
    for name, product in repos.items():
        if name.strip().lower() == wanted and product.strip():
            return product.strip()
    return None
