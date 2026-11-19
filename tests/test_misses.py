"""놓침 기록 보기(misses.py, store.read_misses, misses 명령) 시험. 시험 자료는 모두 가짜다."""

import io
import json
import os
import pathlib
import tempfile
import unittest

from ced_taker import cli, misses
from ced_taker.parser import KeyNote
from ced_taker.store import Store, read_misses

T = "2026-11-%02dT00:00:00Z"


def note(name, product):
    return KeyNote(name, product, (), (), (), "confirmed", "2026-01-01T00:00:00.000Z", "본문")


class DbCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.db_path = self.dir / "ced.db"

    def populate(self):
        db = Store.open(self.db_path)
        for name, product in (("account_balance", "shop"), ("order_total", "shop"), ("balance", "bank")):
            db.upsert_note(note(name, product), "a" * 64, T % 1)
        spec = (("accountBalance", "account_balance", "shop", 8), ("AccountBalance", "account_balance", "shop", 3),
                ("orderTotal", "order_total", "shop", 5), ("Balance", "balance", "bank", 2))
        for token, near, product, times in spec:
            for i in range(times):
                db.record_misses(product, [(token, near)], T % (10 + i))
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()


class ClassifyTest(unittest.TestCase):
    def test_case_only_difference(self):
        self.assertEqual(misses.classify("Balance", "balance"), misses.CASE)
        self.assertEqual(misses.classify("ACCOUNT_BALANCE", "account_balance"), misses.CASE)

    def test_underscore_or_shape_difference(self):
        for token, name in (("accountBalance", "account_balance"), ("AccountBalance", "account_balance"),
                            ("account_balance", "accountBalance"), ("accountbalance", "account_balance")):
            with self.subTest(token=token):
                self.assertEqual(misses.classify(token, name), misses.SHAPE)


class ReadMissesTest(DbCase):
    def test_rows_are_ordered_by_count_then_name(self):
        self.populate()
        rows = read_misses(self.db_path)
        self.assertEqual([(r[0], r[1], r[3]) for r in rows],
                         [("shop", "accountBalance", 8), ("shop", "orderTotal", 5), ("shop", "AccountBalance", 3), ("bank", "Balance", 2)])

    def test_first_and_last_seen(self):
        self.populate()
        top = read_misses(self.db_path)[0]
        self.assertEqual((top[4], top[5]), (T % 10, T % 17))

    def test_product_filter(self):
        self.populate()
        self.assertEqual([r[1] for r in read_misses(self.db_path, "bank")], ["Balance"])
        self.assertEqual(read_misses(self.db_path, "unknown"), [])

    def test_a_missing_database_gives_nothing_and_creates_nothing(self):
        self.assertEqual(read_misses(self.db_path), [])
        self.assertEqual(list(self.dir.iterdir()), [])
        self.assertEqual(read_misses(self.dir / "no_such_folder" / "x.db"), [])
        self.assertFalse((self.dir / "no_such_folder").exists())

    def test_a_database_without_the_table_or_a_broken_file_gives_nothing(self):
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE other (x)")
        conn.commit()
        conn.close()
        self.assertEqual(read_misses(self.db_path), [])
        self.db_path.write_bytes(b"this is not a database")
        self.assertEqual(read_misses(self.db_path), [])

    def test_reading_never_changes_the_database_content(self):
        self.populate()
        before = self.db_path.read_bytes()
        read_misses(self.db_path)
        read_misses(self.db_path, "shop")
        self.assertEqual(self.db_path.read_bytes(), before)


class SummaryTest(DbCase):
    def test_counts_per_kind_cover_every_row(self):
        self.populate()
        summary = misses.summarize(read_misses(self.db_path))
        self.assertEqual((summary["pairs"], summary["total"]), (4, 18))
        self.assertEqual(summary["kinds"], {"case": {"pairs": 1, "total": 2}, "shape": {"pairs": 3, "total": 16}})

    def test_empty(self):
        self.assertEqual(misses.summarize([]), {"pairs": 0, "total": 0, "kinds": {"case": {"pairs": 0, "total": 0}, "shape": {"pairs": 0, "total": 0}}})


class FormatTest(DbCase):
    def test_table_has_header_rows_and_a_kind_summary(self):
        self.populate()
        text = misses.format_table(read_misses(self.db_path))
        lines = text.splitlines()
        self.assertEqual(lines[0], "놓침 기록 (제품: 전체) - 4쌍, 합계 18회")
        self.assertTrue(lines[1].startswith("제품"))
        self.assertIn("accountBalance", lines[2])
        self.assertEqual(lines[-1], "유형별: 대소문자만 다름 1쌍(2회), 밑줄·모양 차이 3쌍(16회)")

    def test_columns_line_up_even_with_korean(self):
        self.populate()
        lines = misses.format_table(read_misses(self.db_path)).splitlines()[1:5]
        positions = {misses._width(l[: l.index(next(c for c in l.split("  ") if c.strip().isdigit() or c.strip() == "횟수"))]) for l in lines}
        self.assertEqual(len(positions), 1)  # 횟수 칸이 모든 줄에서 같은 위치에서 시작한다

    def test_limit_cuts_the_table_but_not_the_summary(self):
        self.populate()
        text = misses.format_table(read_misses(self.db_path), limit=2)
        self.assertIn("횟수가 많은 2쌍만 보여 줌. 전체는 4쌍", text)
        self.assertNotIn("Balance  ", text.split("(횟수가")[0].split("accountBalance")[1].replace("orderTotal", ""))
        self.assertIn("밑줄·모양 차이 3쌍(16회)", text)  # 요약은 전체 기준

    def test_limit_zero_shows_everything(self):
        self.populate()
        text = misses.format_table(read_misses(self.db_path), limit=0)
        self.assertNotIn("보여 줌", text)
        self.assertEqual(text.count("Balance"), 3)

    def test_no_records(self):
        self.assertEqual(misses.format_table([]), "놓침 기록이 없다 (제품: 전체)\n")
        self.assertEqual(misses.format_table([], product="bank"), "놓침 기록이 없다 (제품: bank)\n")

    def test_json_has_summary_and_rows_with_a_kind(self):
        self.populate()
        data = json.loads(misses.to_json(read_misses(self.db_path)))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["summary"]["pairs"], 4)
        first = data["misses"][0]
        self.assertEqual((first["token"], first["near_name"], first["kind"], first["count"]), ("accountBalance", "account_balance", "shape", 8))
        self.assertEqual(set(first), {"product", "token", "near_name", "kind", "count", "first_at", "last_at"})

    def test_json_limit_cuts_rows_but_not_the_summary(self):
        self.populate()
        data = json.loads(misses.to_json(read_misses(self.db_path), limit=1))
        self.assertEqual((len(data["misses"]), data["summary"]["pairs"]), (1, 4))


class CommandTest(DbCase):
    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        base = {"CED_TAKER_DB": str(self.db_path), "HERMES_HOME": str(self.dir / "hermes_home")}  # 시험이 실제 홈을 건드리지 않게 한다
        code = cli.main(["misses", *argv], env=env or base, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_default_output_is_the_table(self):
        self.populate()
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("놓침 기록 (제품: 전체)", out)

    def test_product_json_and_limit_options(self):
        self.populate()
        _, out, _ = self.run_cli("--product", "bank")
        self.assertIn("(제품: bank)", out)
        self.assertNotIn("accountBalance", out)
        _, out, _ = self.run_cli("--json", "--limit", "1")
        self.assertEqual(len(json.loads(out)["misses"]), 1)
        _, out, _ = self.run_cli("--limit", "0")
        self.assertNotIn("보여 줌", out)

    def test_a_negative_limit_is_an_error(self):
        code, out, err = self.run_cli("--limit", "-1")
        self.assertEqual((code, out, err.strip()), (1, "", "오류: bad_limit"))

    def test_no_database_is_not_an_error_and_creates_nothing(self):
        code, out, err = self.run_cli()
        self.assertEqual((code, out, err), (0, "놓침 기록이 없다 (제품: 전체)\n", ""))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_no_vault_setting_is_needed(self):
        self.populate()
        env = {"CED_TAKER_DB": str(self.db_path), "HERMES_HOME": str(self.dir / "h"), "CED_TAKER_CONFIG": str(self.dir / "nope.json")}
        code, out, _ = self.run_cli(env=env)
        self.assertEqual(code, 0)
        self.assertIn("놓침 기록", out)

    def test_output_never_contains_paths_or_document_bodies(self):
        self.populate()
        _, out, _ = self.run_cli()
        _, js, _ = self.run_cli("--json")
        for text in (out, js):
            self.assertNotIn(str(self.dir), text)
            self.assertNotIn("본문", text)

    def test_the_database_content_is_unchanged_by_the_command(self):
        self.populate()
        before = self.db_path.read_bytes()
        self.run_cli()
        self.run_cli("--json")
        self.assertEqual(self.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
