"""명령줄(cli.py) 시험: sync와 explain. 임시 폴더의 가짜 볼트를 쓴다."""

import hashlib
import io
import json
import pathlib
import tempfile
import unittest

from ced_taker import cli, state, sync
from tests.test_funnel import make_diff
from tests.test_sync import VaultCase, key_doc


class SyncCliTest(VaultCase):
    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        db_path = self.vault.parent / "ced.db"
        base = {"CED_TAKER_VAULT": str(self.vault), "CED_TAKER_DB": str(db_path)}
        code = cli.main(list(argv), env={**base, **(env or {})}, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_sync_prints_summary_without_paths_or_bodies(self):
        self.put("amount.md", key_doc())
        self.write_state()
        code, out, err = self.run_cli("sync")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("demo: new=1 updated=0 unchanged=0 reactivated=0 inactive=0", out)
        self.assertNotIn(str(self.vault), out)
        self.assertNotIn("본문 A", out)
        code, out, _ = self.run_cli("sync")
        self.assertIn("unchanged=1", out)

    def test_issues_are_listed(self):
        self.put("amount.md", key_doc())
        self.write_state()
        (self.product / "amount.md").write_text("바뀜", encoding="utf-8")
        _, out, _ = self.run_cli("sync")
        self.assertIn("  ! amount.md  mismatch", out)

    def test_single_product(self):
        self.put("amount.md", key_doc())
        self.write_state()
        code, out, _ = self.run_cli("sync", "--product", "demo")
        self.assertEqual(code, 0)
        self.assertIn("demo:", out)

    def test_unknown_or_unsafe_product_name(self):
        for name in ("nope", "../CED", ".hidden", "a/b"):
            with self.subTest(name=name):
                code, _, err = self.run_cli("sync", "--product", name)
                self.assertEqual((code, err.strip()), (1, "오류: product_not_found"))

    def test_config_error_exits_with_reason_code_only(self):
        code, _, err = self.run_cli("sync", env={"CED_TAKER_VAULT": str(self.vault / "missing")})
        self.assertEqual((code, err.strip()), (1, "오류: vault_not_found"))
        self.assertNotIn(str(self.vault), err)

    def test_state_error_is_reported(self):
        self.put("amount.md", key_doc())
        (self.product / state.STATE_FILE).write_text("{", encoding="utf-8")
        # 실제 대기를 피하려고 재시도 간격을 0으로 바꾼다
        original = state.verify_product
        state.verify_product = lambda d, **kw: original(d, wait=0, **kw)
        sync.verify_product = state.verify_product
        self.addCleanup(setattr, state, "verify_product", original)
        self.addCleanup(setattr, sync, "verify_product", original)
        code, out, _ = self.run_cli("sync")
        self.assertEqual(code, 0)
        self.assertIn("상태 파일 오류 (state_bad_format)", out)


class ExplainCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.root = root
        self.product = root / "vault" / "CED" / "demo"
        self.product.mkdir(parents=True)
        text = (
            '---\nproduct: "demo"\nkeywords: ["balance"]\nappearsIn: ["users.balance"]\ntables: ["users"]\n'
            'status: confirmed\nupdated: "2026-01-01T00:00:00.000Z"\n---\n# balance\n\n두 가지 뜻이 있다.\n'
        )
        data = text.encode()
        (self.product / "balance.md").write_bytes(data)
        self.write_state({"balance.md": hashlib.sha256(data).hexdigest()})
        (root / "diff.txt").write_text(make_diff(("a.py", ["+balance = 1"])), encoding="utf-8")
        (root / "desc.txt").write_text("PR 설명", encoding="utf-8")

    def write_state(self, files):
        (self.product / state.STATE_FILE).write_text(json.dumps({"version": 1, "files": files}), encoding="utf-8")

    def run_cli(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        env = {"CED_TAKER_VAULT": str(self.root / "vault"), "CED_TAKER_DB": str(self.root / "ced.db")}
        code = cli.main(list(argv), env=env, out=out, err=err, stdin=io.StringIO(stdin))
        return code, out.getvalue(), err.getvalue()

    def test_syncs_then_explains(self):
        code, out, err = self.run_cli("explain", "--product", "demo", "--diff", str(self.root / "diff.txt"))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("### balance", out)
        self.assertIn("> 두 가지 뜻이 있다.", out)
        self.assertNotIn("주의:", out)

    def test_diff_from_stdin(self):
        diff = (self.root / "diff.txt").read_text(encoding="utf-8")
        code, out, _ = self.run_cli("explain", "--product", "demo", "--diff", "-", stdin=diff)
        self.assertEqual(code, 0)
        self.assertIn("### balance", out)

    def test_nothing_matched_prints_nothing(self):
        code, out, _ = self.run_cli("explain", "--product", "demo", "--description", str(self.root / "desc.txt"))
        self.assertEqual((code, out), (0, ""))

    def test_sync_problem_adds_notice(self):
        (self.product / "balance.md").write_text("바뀐 내용", encoding="utf-8")  # 상태 파일과 해시가 어긋남
        self.run_cli("sync")  # 처음 동기화는 불일치라 아무것도 저장하지 않는다
        code, out, _ = self.run_cli("explain", "--product", "demo", "--diff", str(self.root / "diff.txt"))
        self.assertEqual((code, out), (0, ""))  # 저장된 문서가 없으니 해설도 없다

    def test_notice_when_old_content_is_used(self):
        self.run_cli("sync")  # 정상 문서를 먼저 저장한다
        (self.product / "balance.md").write_text("바뀐 내용", encoding="utf-8")
        _, out, _ = self.run_cli("explain", "--product", "demo", "--diff", str(self.root / "diff.txt"))
        self.assertIn("주의: CED 일부를 최신 상태로 반영하지 못함 (1개)", out)
        self.assertIn("### balance", out)  # 이전 내용을 계속 쓴다
        self.assertNotIn("balance.md", out)

    def test_input_errors(self):
        cases = [
            (("explain", "--product", "nope"), "오류: product_not_found"),
            (("explain", "--product", "demo", "--diff", "/no/such/file"), "오류: input_unreadable"),
            (("explain", "--product", "demo", "--diff", "-", "--description", "-"), "오류: stdin_used_twice"),
            (("explain", "--product", "../x"), "오류: product_not_found"),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(*argv)
                self.assertEqual((code, out, err.strip()), (1, "", expected))
                self.assertNotIn(str(self.root), err)


if __name__ == "__main__":
    unittest.main()
