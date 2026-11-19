"""데모(demo.py)와 examples/ 의 올려 둔 파일 시험.

올려 둔 파일이 지금 코드가 만든 결과와 같은지 확인한다. 코드를 바꾸고 scripts/build_demo.py 를 다시 돌리지 않으면 실패한다.
"""

import pathlib
import sqlite3
import tempfile
import unittest

from ced_taker import demo
from ced_taker.store import Store
from ced_taker.sync import sync_vault

EXAMPLES = demo.EXAMPLES


def dump(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


class DemoFilesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)

    def test_committed_generated_files_match_what_the_code_makes_now(self):
        files, _ = demo.build_examples(work_dir=self.tmp)
        for name, text in files.items():
            with self.subTest(name=name):
                self.assertEqual((EXAMPLES / name).read_text(encoding="utf-8"), text,
                                 f"{name} 이 코드와 어긋났다. python3 scripts/build_demo.py 를 다시 돌린다.")

    def test_committed_database_matches_what_the_code_makes_now(self):
        built = self.tmp / "built.db"
        demo.build_db(EXAMPLES / "demo-vault", built)
        self.assertEqual(dump(EXAMPLES / "demo.db"), dump(built))

    def test_demo_database_is_one_clean_file_with_the_expected_content(self):
        self.assertEqual([p.name for p in EXAMPLES.glob("demo.db*")], ["demo.db"])  # WAL 부속 파일이 없다
        # 올려 둔 파일을 Store.open으로 직접 열면 WAL 모드로 바뀌고 부속 파일이 생겨 파일이 변조된다. 복사본을 연다.
        copy = self.tmp / "copy.db"
        copy.write_bytes((EXAMPLES / "demo.db").read_bytes())
        db = Store.open(copy)
        self.addCleanup(db.close)
        self.assertEqual(db.active_names("rwa"), {"balance", "rwa"})
        self.assertEqual(db.usable_names("rwa"), {"balance"})
        self.assertEqual([e.outcome for e in db.history("rwa", "balance")], ["new"])

    def test_demo_vault_documents_pass_verification_without_any_issue(self):
        db = Store.open(self.tmp / "check.db")
        self.addCleanup(db.close)
        (report,) = sync_vault(db, EXAMPLES / "demo-vault", demo.FIXED_NOW)
        self.assertEqual((report.issues, report.state_error), ((), None))
        self.assertEqual(report.counts, {"new": 2})

    def test_running_the_tests_does_not_modify_the_committed_files(self):
        before = {p: p.read_bytes() for p in EXAMPLES.rglob("*") if p.is_file()}
        demo.build_examples(work_dir=self.tmp)
        demo.build_db(EXAMPLES / "demo-vault", self.tmp / "other.db")
        after = {p: p.read_bytes() for p in EXAMPLES.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(sorted(p.name for p in EXAMPLES.glob("demo.db*")), ["demo.db"])

    def test_schema_file_is_readable_sql_that_recreates_the_tables(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript((EXAMPLES / "schema.sql").read_text(encoding="utf-8"))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, {"notes", "terms", "ingest_log", "misses"})

    def test_the_example_shows_the_funnel_working(self):
        text = (EXAMPLES / "explain-example.md").read_text(encoding="utf-8")
        self.assertIn("### balance", text)
        self.assertIn("바뀐 줄 3회, PR 설명 1회", text)  # 락 파일의 balance와 조사가 붙은 "balance를"을 제대로 다룬 결과
        self.assertNotIn("pnpm-lock", text)

    def test_examples_contain_no_local_paths(self):
        for path in EXAMPLES.rglob("*"):
            if path.is_file():
                data = path.read_bytes()  # DB도 같이 본다
                for needle in (b"/home/", b"/tmp/", b"/Users/"):
                    self.assertNotIn(needle, data, f"{path.name} 에 개인 경로가 있다")


if __name__ == "__main__":
    unittest.main()
