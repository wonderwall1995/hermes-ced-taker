"""로컬 설정. 볼트 위치를 환경 변수 또는 로컬 설정 파일에서 찾는다.

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


def _vault_from_file(config_path):
    try:
        raw = Path(config_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise ConfigError(CONFIG_UNREADABLE) from None
    try:
        value = json.loads(raw).get("vault")
    except (ValueError, AttributeError):
        raise ConfigError(CONFIG_UNREADABLE) from None
    return value if isinstance(value, str) and value.strip() else None


def resolve_vault(env=None, config_path=None):
    """볼트 폴더를 돌려준다. 환경 변수가 설정 파일보다 우선한다."""
    env = os.environ if env is None else env
    value = env.get(ENV_VAULT, "").strip() or None
    if value is None:
        path = config_path or env.get(ENV_CONFIG) or default_config_path()
        value = _vault_from_file(path)
    if value is None:
        raise ConfigError(VAULT_NOT_CONFIGURED)
    vault = Path(value).expanduser()
    if not vault.is_dir():
        raise ConfigError(VAULT_NOT_FOUND)
    return vault
