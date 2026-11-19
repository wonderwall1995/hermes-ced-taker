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


class ResolveProductsTest(unittest.TestCase):
    """repos는 제한용이다. 기본(None)은 모든 제품을 참조한다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = pathlib.Path(tmp.name) / "config.json"

    def write(self, data):
        self.config.write_text(json.dumps(data), encoding="utf-8")

    def resolve(self, repo, env=None):
        return config.resolve_products(repo, env=env if env is not None else {}, config_path=self.config)

    def test_a_string_limits_to_one_product(self):
        self.write({"repos": {"acme/shop": "products"}})
        self.assertEqual(self.resolve("acme/shop"), ["products"])

    def test_a_list_limits_to_those_products_in_order(self):
        self.write({"repos": {"acme/shop": ["b", "a", "c"]}})
        self.assertEqual(self.resolve("acme/shop"), ["b", "a", "c"])

    def test_duplicates_and_spaces_are_cleaned(self):
        self.write({"repos": {"acme/shop": [" a ", "b", "a"]}})
        self.assertEqual(self.resolve("acme/shop"), ["a", "b"])

    def test_repo_name_ignores_case_and_spaces(self):
        self.write({"repos": {"Acme/Shop": " products "}})
        self.assertEqual(self.resolve(" acme/SHOP "), ["products"])

    def test_a_repo_that_is_not_listed_has_no_limit(self):
        self.write({"repos": {"acme/shop": "products"}})
        self.assertIsNone(self.resolve("acme/other"))

    def test_no_repos_no_file_or_no_vault_only_means_no_limit(self):
        self.assertIsNone(self.resolve("acme/shop"))  # 파일 없음
        self.write({"vault": "/x"})
        self.assertIsNone(self.resolve("acme/shop"))
        self.write({"repos": {}})
        self.assertIsNone(self.resolve("acme/shop"))

    def test_bad_repo_argument_has_no_limit(self):
        self.write({"repos": {"acme/shop": "products"}})
        for bad in (None, "", 5):
            self.assertIsNone(self.resolve(bad))

    def test_any_malformed_entry_is_a_config_error_even_for_other_repos(self):
        bad_values = [[], "", "  ", [""], ["a", " "], 5, None, ["a", 5], {"x": "y"}]
        for value in bad_values:
            with self.subTest(value=value):
                self.write({"repos": {"acme/shop": ["ok"], "a/b": value}})
                with self.assertRaises(ConfigError) as ctx:
                    self.resolve("acme/shop")
                self.assertEqual(ctx.exception.reason, config.CONFIG_UNREADABLE)

    def test_repos_that_is_not_an_object_is_a_config_error(self):
        self.write({"repos": ["acme/shop"]})
        with self.assertRaises(ConfigError):
            self.resolve("acme/shop")

    def test_config_path_from_env(self):
        self.write({"repos": {"acme/shop": "products"}})
        self.assertEqual(config.resolve_products("acme/shop", env={config.ENV_CONFIG: str(self.config)}), ["products"])


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
