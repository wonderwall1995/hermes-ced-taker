"""로컬 설정(config.py) 시험."""

import json
import pathlib
import tempfile
import unittest

from ced_taker import config
from ced_taker.config import ConfigError, resolve_vault


class ResolveVaultTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.config = self.tmp / "config.json"

    def reason(self, **kwargs):
        with self.assertRaises(ConfigError) as ctx:
            resolve_vault(**kwargs)
        return ctx.exception.reason

    def test_env_variable(self):
        self.assertEqual(resolve_vault(env={config.ENV_VAULT: str(self.vault)}, config_path=self.config), self.vault)

    def test_config_file(self):
        self.config.write_text(json.dumps({"vault": str(self.vault)}), encoding="utf-8")
        self.assertEqual(resolve_vault(env={}, config_path=self.config), self.vault)

    def test_env_wins_over_file(self):
        other = self.tmp / "other"
        other.mkdir()
        self.config.write_text(json.dumps({"vault": str(other)}), encoding="utf-8")
        self.assertEqual(resolve_vault(env={config.ENV_VAULT: str(self.vault)}, config_path=self.config), self.vault)

    def test_config_path_from_env(self):
        self.config.write_text(json.dumps({"vault": str(self.vault)}), encoding="utf-8")
        self.assertEqual(resolve_vault(env={config.ENV_CONFIG: str(self.config)}), self.vault)

    def test_blank_env_falls_back_to_file(self):
        self.config.write_text(json.dumps({"vault": str(self.vault)}), encoding="utf-8")
        self.assertEqual(resolve_vault(env={config.ENV_VAULT: "  "}, config_path=self.config), self.vault)

    def test_not_configured(self):
        self.assertEqual(self.reason(env={}, config_path=self.config), config.VAULT_NOT_CONFIGURED)

    def test_vault_folder_does_not_exist(self):
        env = {config.ENV_VAULT: str(self.tmp / "nope")}
        self.assertEqual(self.reason(env=env, config_path=self.config), config.VAULT_NOT_FOUND)

    def test_broken_config_file(self):
        for text in ("{", "[]", '{"vault": 5}'):
            with self.subTest(text=text):
                self.config.write_text(text, encoding="utf-8")
                expected = config.VAULT_NOT_CONFIGURED if text == '{"vault": 5}' else config.CONFIG_UNREADABLE
                self.assertEqual(self.reason(env={}, config_path=self.config), expected)

    def test_error_never_contains_path(self):
        env = {config.ENV_VAULT: str(self.tmp / "nope")}
        with self.assertRaises(ConfigError) as ctx:
            resolve_vault(env=env)
        self.assertNotIn(str(self.tmp), str(ctx.exception))


class DefaultDbPathTest(unittest.TestCase):
    def test_explicit_db_wins(self):
        env = {config.ENV_DB: "/tmp/x/ced.db", "HERMES_HOME": "/tmp/h"}
        self.assertEqual(config.default_db_path(env), pathlib.Path("/tmp/x/ced.db"))

    def test_under_hermes_home(self):
        self.assertEqual(config.default_db_path({"HERMES_HOME": "/tmp/h"}), pathlib.Path("/tmp/h/ced-taker/ced.db"))

    def test_default_is_dot_hermes_in_home(self):
        expected = pathlib.Path("~/.hermes").expanduser() / "ced-taker" / "ced.db"
        self.assertEqual(config.default_db_path({}), expected)


if __name__ == "__main__":
    unittest.main()
