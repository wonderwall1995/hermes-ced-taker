"""참고 문서 공급자(provider.py)와 provide 명령 시험. 시험 자료는 모두 가짜다."""

import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from ced_taker import cli, provider
from ced_taker.config import ConfigError
from ced_taker.provider import ProviderError, parse_request
from tests.test_funnel import make_diff
from tests.test_sync import VaultCase, key_doc, sha

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
T1 = "2026-01-01T00:00:00Z"


def request(**kw):
    base = {"version": 1, "repo": "acme/shop", "title": "", "description": "", "diff": ""}
    base.update(kw)
    return json.dumps(base)


class ParseRequestTest(unittest.TestCase):
    def reason(self, raw):
        with self.assertRaises(ProviderError) as ctx:
            parse_request(raw)
        return ctx.exception.reason

    def test_valid_request(self):
        got = parse_request(request(title="t", description="d", diff="x"))
        self.assertEqual(got, {"repo": "acme/shop", "title": "t", "description": "d", "diff": "x"})

    def test_optional_fields_default_to_empty(self):
        got = parse_request(json.dumps({"version": 1, "repo": "acme/shop"}))
        self.assertEqual((got["title"], got["description"], got["diff"]), ("", "", ""))
        got = parse_request(json.dumps({"version": 1, "repo": "acme/shop", "diff": None}))
        self.assertEqual(got["diff"], "")

    def test_bytes_and_text_are_both_accepted(self):
        self.assertEqual(parse_request(request().encode())["repo"], "acme/shop")

    def test_unknown_extra_fields_are_ignored(self):
        self.assertEqual(parse_request(request(future_field=[1, 2]))["repo"], "acme/shop")

    def test_broken_requests(self):
        for raw in ("", "{", "[]", "5", '{"repo": "a/b"}', '{"version": "1", "repo": "a/b"}',
                    '{"version": true, "repo": "a/b"}', '{"version": 1}', '{"version": 1, "repo": ""}',
                    '{"version": 1, "repo": 5}', '{"version": 1, "repo": "a/b", "diff": 5}',
                    '{"version": 1, "repo": "a/b", "title": ["x"]}'):
            with self.subTest(raw=raw):
                self.assertEqual(self.reason(raw), provider.BAD_REQUEST)

    def test_invalid_utf8(self):
        self.assertEqual(self.reason(b"\xff\xfe"), provider.BAD_REQUEST)

    def test_other_version_is_refused(self):
        self.assertEqual(self.reason(request(version=2)), provider.UNSUPPORTED_VERSION)

    def test_too_large(self):
        self.assertEqual(self.reason(request(diff="x" * (provider.MAX_REQUEST_BYTES + 1))), provider.REQUEST_TOO_LARGE)

    def test_error_never_contains_the_content(self):
        with self.assertRaises(ProviderError) as ctx:
            parse_request('{"version": 1, "repo": "a/b", "diff": 5, "title": "비밀 제목"}')
        self.assertNotIn("비밀", str(ctx.exception))


class ProviderCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.vault.parent / "config.json"
        self.db_path = self.vault.parent / "ced.db"
        self.write_config({"repos": {"acme/shop": "demo"}})
        self.env = {
            "CED_TAKER_VAULT": str(self.vault),
            "CED_TAKER_DB": str(self.db_path),
            "CED_TAKER_CONFIG": str(self.cfg),
            "HERMES_HOME": str(self.vault.parent / "hermes_home"),  # 시험이 실제 홈을 건드리지 않게 한다
        }

    def write_config(self, data):
        self.cfg.write_text(json.dumps(data), encoding="utf-8")

    def add_product(self, name, files):
        """볼트에 제품 폴더를 하나 더 만든다. files는 {파일명: 문서 텍스트}."""
        folder = self.vault / "CED" / name
        folder.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for filename, text in files.items():
            data = text.encode("utf-8")
            (folder / filename).write_bytes(data)
            hashes[filename] = sha(data)
        (folder / ".ced-state.json").write_text(json.dumps({"version": 1, "files": hashes}), encoding="utf-8")
        return folder

    def provide(self, **kw):
        return provider.provide(parse_request(request(**kw)), env=self.env, now=T1, sleep=lambda s: None)


class ProvideTest(ProviderCase):
    def setUp(self):
        super().setUp()
        self.put("amount.md", key_doc(body="주문 금액의 뜻"))
        self.write_state()

    def test_matched_note_becomes_one_document(self):
        diff = make_diff(("a.py", ["+amount = 1"]))
        got = self.provide(diff=diff)
        self.assertEqual(got["version"], 1)
        self.assertEqual(got["notices"], [])
        (doc,) = got["docs"]
        self.assertEqual((doc["id"], doc["title"]), ("ced:amount", "CED 해설: amount"))
        self.assertIn("걸림: 바뀐 줄 1회", doc["text"])
        self.assertIn("위치: orders.amount", doc["text"])
        self.assertIn("주문 금액의 뜻", doc["text"])
        self.assertEqual(set(doc), {"id", "title", "text"})

    def test_nothing_matched_gives_empty_docs(self):
        got = self.provide(diff=make_diff(("a.py", ["+other = 1"])))
        self.assertEqual(got, {"version": 1, "docs": [], "notices": []})

    def test_title_and_description_count_as_description(self):
        self.assertEqual(len(self.provide(title="amount 를 고침")["docs"]), 1)
        self.assertEqual(len(self.provide(description="amount 를 고침")["docs"]), 1)

    def test_documents_never_contain_diff_content(self):
        diff = make_diff(("secret/path.py", ["+amount = hunter2_password"]))
        dump = json.dumps(self.provide(diff=diff), ensure_ascii=False)
        self.assertNotIn("hunter2_password", dump)
        self.assertNotIn("secret/path.py", dump)

    def test_column_named_summary_does_not_collide_with_the_summary_document(self):
        self.put("summary.md", key_doc(body="요약 컬럼"))
        self.write_state()
        got = self.provide(diff=make_diff(("a.py", ["+summary = 1"])))
        self.assertEqual([d["id"] for d in got["docs"]], ["ced:summary"])

    def test_sync_problem_is_reported_in_notices_and_old_content_is_used(self):
        self.provide(diff="")  # 먼저 정상 문서를 DB에 넣는다
        (self.product / "amount.md").write_text("상태 파일과 해시가 어긋난 내용", encoding="utf-8")
        got = self.provide(diff=make_diff(("a.py", ["+amount = 1"])))
        self.assertEqual(len(got["docs"]), 1)
        self.assertIn("주문 금액의 뜻", got["docs"][0]["text"])
        (notice,) = got["notices"]
        self.assertIn("(1개)", notice)
        self.assertNotIn("amount.md", notice)

    def test_sync_problem_is_reported_even_when_nothing_matched(self):
        self.provide(diff="")
        (self.product / "amount.md").write_text("바뀜", encoding="utf-8")
        got = self.provide(diff=make_diff(("a.py", ["+other = 1"])))
        self.assertEqual(got["docs"], [])
        self.assertEqual(len(got["notices"]), 1)

    def test_mapped_product_without_a_folder(self):
        self.write_config({"repos": {"acme/shop": "missing_product"}})
        got = self.provide(diff=make_diff(("a.py", ["+amount = 1"])))
        self.assertEqual((got["docs"], got["notices"]), ([], [provider.NO_PRODUCT_FOLDER_NOTICE]))

    def test_repo_name_matching_ignores_case(self):
        self.assertEqual(len(self.provide(repo="ACME/Shop", diff=make_diff(("a.py", ["+amount"])))["docs"]), 1)


class DefaultIsEveryProductTest(ProviderCase):
    """기본은 모든 저장소가 모든 CED 제품을 참조한다. repos는 제한용이다."""

    DIFF = make_diff(("a.py", ["+amount = cost"]))

    def setUp(self):
        super().setUp()
        self.put("amount.md", key_doc(body="제품 데모의 금액"))
        self.write_state()
        self.add_product("other", {"cost.md": key_doc(product="other", body="제품 other의 비용")})

    def ids(self, **kw):
        return sorted(d["id"] for d in self.provide(**kw)["docs"])

    def test_a_repo_that_is_not_listed_sees_every_product(self):
        self.assertEqual(self.ids(repo="acme/other", diff=self.DIFF), ["ced:demo:amount", "ced:other:cost"])

    def test_no_repos_setting_at_all_also_means_every_product(self):
        self.write_config({})
        self.assertEqual(self.ids(diff=self.DIFF), ["ced:demo:amount", "ced:other:cost"])

    def test_a_listed_repo_is_limited_to_its_products(self):
        self.write_config({"repos": {"acme/shop": "demo"}})
        self.assertEqual(self.ids(diff=self.DIFF), ["ced:amount"])

    def test_the_limit_may_be_a_list(self):
        self.write_config({"repos": {"acme/shop": ["demo", "other"]}})
        self.assertEqual(self.ids(diff=self.DIFF), ["ced:demo:amount", "ced:other:cost"])

    def test_limit_to_another_product(self):
        self.write_config({"repos": {"acme/shop": ["other"]}})
        self.assertEqual(self.ids(diff=self.DIFF), ["ced:cost"])

    def test_ids_carry_the_product_only_when_there_are_several_in_scope(self):
        self.write_config({"repos": {"acme/shop": "demo"}})
        self.assertEqual(self.ids(diff=self.DIFF), ["ced:amount"])  # 범위가 제품 하나
        self.write_config({})
        self.assertEqual(self.ids(diff=self.DIFF), ["ced:demo:amount", "ced:other:cost"])  # 범위가 둘

    def test_a_limited_product_without_a_folder_gives_a_notice_and_the_rest_still_work(self):
        self.write_config({"repos": {"acme/shop": ["demo", "gone"]}})
        got = self.provide(diff=self.DIFF)
        self.assertEqual([d["id"] for d in got["docs"]], ["ced:amount"])
        self.assertIn(provider.NO_PRODUCT_FOLDER_NOTICE, got["notices"])

    def test_same_column_in_two_products_shows_both_and_warns_in_the_notices(self):
        self.add_product("third", {"amount.md": key_doc(product="third", body="제품 third의 금액")})
        got = self.provide(repo="acme/other", diff=make_diff(("a.py", ["+amount = 1"])))  # 제한 없는 저장소
        self.assertEqual(sorted(d["id"] for d in got["docs"]), ["ced:demo:amount", "ced:third:amount"])
        (notice,) = got["notices"]
        self.assertIn("현재 의도와 벗어난 상태", notice)
        self.assertIn("(컬럼: amount)", notice)

    def test_product_names_stay_out_of_titles_and_headers(self):
        self.add_product("third", {"amount.md": key_doc(product="third", body="제품 third의 금액")})
        for doc in self.provide(repo="acme/other", diff=make_diff(("a.py", ["+amount = 1"])))["docs"]:
            header = doc["title"] + doc["text"].split("\n\n")[0]
            for product in ("demo", "third", "other"):
                self.assertNotIn(product, header)

    def test_empty_vault_gives_a_notice_not_an_error(self):
        import shutil

        shutil.rmtree(self.vault / "CED")
        (self.vault / "CED").mkdir()
        self.assertEqual(self.provide(repo="acme/other", diff=self.DIFF),
                         {"version": 1, "docs": [], "notices": [provider.NO_PRODUCTS_NOTICE]})
        # 제한된 저장소는 다른 안내를 받는다: 설정에 적힌 제품의 폴더가 없다.
        self.assertEqual(self.provide(diff=self.DIFF)["notices"], [provider.NO_PRODUCT_FOLDER_NOTICE])

    def test_a_repo_with_no_matching_words_gets_nothing_and_no_warning(self):
        self.assertEqual(self.provide(repo="acme/other", diff=make_diff(("a.py", ["+unrelated = 1"]))),
                         {"version": 1, "docs": [], "notices": []})


class ErrorTest(ProviderCase):
    def test_a_missing_vault_setting_is_an_error_for_every_repo_even_unlisted_ones(self):
        env = {"CED_TAKER_CONFIG": str(self.cfg), "CED_TAKER_DB": str(self.db_path)}
        for repo in ("acme/shop", "acme/other"):
            with self.subTest(repo=repo):
                with self.assertRaises(ConfigError) as ctx:
                    provider.provide(parse_request(request(repo=repo)), env=env)
                self.assertEqual(ctx.exception.reason, "vault_not_configured")

    def test_a_vault_folder_that_does_not_exist_is_an_error(self):
        env = dict(self.env, CED_TAKER_VAULT=str(self.vault / "nope"))
        with self.assertRaises(ConfigError) as ctx:
            provider.provide(parse_request(request()), env=env)
        self.assertEqual(ctx.exception.reason, "vault_not_found")

    def test_broken_config_is_an_error(self):
        self.cfg.write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigError):
            provider.provide(parse_request(request()), env=self.env)

    def test_a_malformed_limit_is_an_error(self):
        self.write_config({"repos": {"acme/other": []}})
        with self.assertRaises(ConfigError) as ctx:
            provider.provide(parse_request(request()), env=self.env)
        self.assertEqual(ctx.exception.reason, "config_unreadable")


class ProvideCommandTest(ProviderCase):
    def run_cli(self, raw, env=None):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["provide"], env=env or self.env, out=out, err=err, stdin=io.StringIO(raw))
        return code, out.getvalue(), err.getvalue()

    def test_prints_one_line_of_json_only(self):
        self.put("amount.md", key_doc())
        self.write_state()
        code, out, err = self.run_cli(request(diff=make_diff(("a.py", ["+amount"]))))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertEqual(json.loads(out)["docs"][0]["id"], "ced:amount")

    def test_korean_text_is_not_escaped(self):
        self.put("amount.md", key_doc(body="한글 본문"))
        self.write_state()
        _, out, _ = self.run_cli(request(diff=make_diff(("a.py", ["+amount"]))))
        self.assertIn("한글 본문", out)

    def test_errors_exit_one_with_a_reason_and_no_stdout(self):
        cases = [("not json", "오류: bad_request"), (request(version=9), "오류: unsupported_version")]
        for raw, expected in cases:
            with self.subTest(raw=raw[:20]):
                code, out, err = self.run_cli(raw)
                self.assertEqual((code, out, err.strip()), (1, "", expected))

    def test_config_error_exits_one_without_paths(self):
        self.cfg.write_text("{", encoding="utf-8")
        code, out, err = self.run_cli(request())
        self.assertEqual((code, out, err.strip()), (1, "", "오류: config_unreadable"))
        self.assertNotIn(str(self.vault), err)


class LauncherTest(ProviderCase):
    """bin/ced-taker 를 실제 프로세스로 띄운다. 설치 없이, 다른 폴더에서도 되는지 확인한다."""

    def run_launcher(self, raw):
        env = dict(self.env, PATH=os.environ.get("PATH", ""))
        with tempfile.TemporaryDirectory() as elsewhere:
            return subprocess.run(
                [sys.executable, str(REPO_ROOT / "bin" / "ced-taker"), "provide"],
                input=raw, capture_output=True, text=True, cwd=elsewhere, env=env, timeout=30,
            )

    def test_works_from_another_directory_without_install(self):
        self.put("amount.md", key_doc(body="실행 파일 시험"))
        self.write_state()
        done = self.run_launcher(request(diff=make_diff(("a.py", ["+amount = 1"]))))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        got = json.loads(done.stdout)
        self.assertEqual(got["docs"][0]["id"], "ced:amount")
        self.assertIn("실행 파일 시험", got["docs"][0]["text"])

    def test_a_repo_without_a_limit_sees_every_product_through_the_launcher(self):
        self.put("amount.md", key_doc(body="실행 파일 시험"))
        self.write_state()
        done = self.run_launcher(request(repo="acme/other", diff=make_diff(("a.py", ["+amount = 1"]))))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual([d["id"] for d in json.loads(done.stdout)["docs"]], ["ced:amount"])

    def test_missing_vault_setting_through_the_launcher(self):
        env_less = {"CED_TAKER_CONFIG": str(self.cfg), "PATH": os.environ.get("PATH", "")}
        with tempfile.TemporaryDirectory() as elsewhere:
            done = subprocess.run([sys.executable, str(REPO_ROOT / "bin" / "ced-taker"), "provide"], input=request(repo="acme/other"),
                                  capture_output=True, text=True, cwd=elsewhere, env=env_less, timeout=30)
        self.assertEqual((done.returncode, done.stdout, done.stderr.strip()), (1, "", "오류: vault_not_configured"))

    def test_bad_request_through_the_launcher(self):
        done = self.run_launcher("nope")
        self.assertEqual((done.returncode, done.stdout, done.stderr.strip()), (1, "", "오류: bad_request"))

    def test_launcher_is_executable(self):
        self.assertTrue(os.access(REPO_ROOT / "bin" / "ced-taker", os.X_OK))


if __name__ == "__main__":
    unittest.main()
