"""SQLite 저장소(store.py) 시험. 시험 자료는 모두 가짜다."""

import os
import pathlib
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

from ced_taker import store
from ced_taker.parser import IndexNote, KeyNote
from ced_taker.store import Store

T1, T2, T3 = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"
SHA_A, SHA_B = "a" * 64, "b" * 64


def key_note(name="amount", product="demo", body="본문 A", **overrides):
    fields = dict(
        name=name,
        product=product,
        keywords=("amount", "total"),
        appears_in=("orders.amount", "refunds.amount"),
        tables=("orders", "refunds"),
        status="confirmed",
        updated="2026-01-01T00:00:00.000Z",
        body=body,
    )
    fields.update(overrides)
    return KeyNote(**fields)


def index_note(product="demo"):
    return IndexNote(name=product, product=product, notes=1, generated="2026-01-01T00:00:00.000Z", body="목차")


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.db = Store.open(":memory:")
        self.addCleanup(self.db.close)


class UpsertTest(StoreCase):
    def test_new_note_is_saved_with_terms(self):
        self.assertEqual(self.db.upsert_note(key_note(), SHA_A, T1), store.NEW)
        saved = self.db.get_note("demo", "amount")
        self.assertEqual((saved.kind, saved.sha256, saved.status, saved.body), ("key", SHA_A, "confirmed", "본문 A"))
        self.assertTrue(saved.active)
        self.assertEqual(
            self.db.terms_of("demo", "amount"),
            {
                (store.TERM_NAME, "amount"),
                (store.TERM_KEYWORD, "amount"),
                (store.TERM_KEYWORD, "total"),
                (store.TERM_TABLE, "orders"),
                (store.TERM_TABLE, "refunds"),
                (store.TERM_COLUMN_REF, "orders.amount"),
                (store.TERM_COLUMN_REF, "refunds.amount"),
            },
        )

    def test_same_hash_is_unchanged_and_writes_nothing(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        self.assertEqual(self.db.upsert_note(key_note(), SHA_A, T2), store.UNCHANGED)
        self.assertEqual(self.db.get_note("demo", "amount").changed_at, T1)
        self.assertEqual(len(self.db.history("demo", "amount")), 1)

    def test_changed_note_overwrites_body_and_replaces_terms(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        newer = key_note(body="본문 B", keywords=("sum",), tables=("orders",), appears_in=("orders.amount",))
        self.assertEqual(self.db.upsert_note(newer, SHA_B, T2), store.UPDATED)
        saved = self.db.get_note("demo", "amount")
        self.assertEqual((saved.body, saved.sha256, saved.changed_at), ("본문 B", SHA_B, T2))
        self.assertEqual(
            self.db.terms_of("demo", "amount"),
            {
                (store.TERM_NAME, "amount"),
                (store.TERM_KEYWORD, "sum"),
                (store.TERM_TABLE, "orders"),
                (store.TERM_COLUMN_REF, "orders.amount"),
            },
        )

    def test_history_keeps_hashes_but_not_bodies(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        self.db.upsert_note(key_note(body="본문 B"), SHA_B, T2)
        entries = self.db.history("demo", "amount")
        self.assertEqual([(e.at, e.sha256, e.outcome) for e in entries], [(T1, SHA_A, "new"), (T2, SHA_B, "updated")])
        dump = "\n".join(self.db.conn.iterdump())
        self.assertEqual(dump.count("본문 B"), 1)  # notes에만 있고 이력에는 없다

    def test_duplicate_values_in_lists_are_stored_once(self):
        note = key_note(keywords=("amount", "amount"), tables=("orders", "orders"))
        self.db.upsert_note(note, SHA_A, T1)
        self.assertEqual(len([t for t in self.db.terms_of("demo", "amount") if t[0] == store.TERM_TABLE]), 1)

    def test_index_note_has_no_terms(self):
        self.assertEqual(self.db.upsert_note(index_note(), SHA_A, T1), store.NEW)
        saved = self.db.get_note("demo", "demo")
        self.assertEqual((saved.kind, saved.note_count, saved.status), ("index", 1, None))
        self.assertEqual(self.db.terms_of("demo", "demo"), set())

    def test_same_name_in_different_products_is_separate(self):
        self.db.upsert_note(key_note(product="demo"), SHA_A, T1)
        self.db.upsert_note(key_note(product="other", body="다른 제품"), SHA_B, T1)
        self.assertEqual(self.db.get_note("demo", "amount").body, "본문 A")
        self.assertEqual(self.db.get_note("other", "amount").body, "다른 제품")

    def test_non_confirmed_status_is_stored(self):
        self.db.upsert_note(key_note(status="draft"), SHA_A, T1)
        self.assertEqual(self.db.get_note("demo", "amount").status, "draft")

    def test_failed_write_leaves_no_partial_data(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        newer = key_note(body="본문 B", keywords=("sum",))
        # 본문 덮어쓰기와 열쇠 교체가 끝난 뒤, 마지막 단계(이력 기록)에서 실패시킨다.
        with mock.patch.object(self.db, "_log", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.db.upsert_note(newer, SHA_B, T2)
        saved = self.db.get_note("demo", "amount")
        self.assertEqual((saved.body, saved.sha256), ("본문 A", SHA_A))
        self.assertEqual(len(self.db.terms_of("demo", "amount")), 7)
        self.assertEqual(len(self.db.history("demo", "amount")), 1)


class InactiveTest(StoreCase):
    def test_mark_inactive_keeps_the_row(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        self.assertTrue(self.db.mark_inactive("demo", "amount", "missing", T2))
        saved = self.db.get_note("demo", "amount")
        self.assertFalse(saved.active)
        self.assertEqual((saved.inactive_reason, saved.body), ("missing", "본문 A"))
        self.assertEqual(self.db.history("demo", "amount")[-1].outcome, store.INACTIVE)
        self.assertEqual(self.db.history("demo", "amount")[-1].reason, "missing")

    def test_mark_inactive_twice_logs_once(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        self.db.mark_inactive("demo", "amount", "missing", T2)
        self.assertFalse(self.db.mark_inactive("demo", "amount", "missing", T3))
        self.assertEqual(len(self.db.history("demo", "amount")), 2)

    def test_mark_inactive_unknown_note(self):
        self.assertFalse(self.db.mark_inactive("demo", "nope", "missing", T1))
        self.assertEqual(self.db.history("demo", "nope"), [])

    def test_same_hash_comes_back_as_reactivated(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        self.db.mark_inactive("demo", "amount", "missing", T2)
        self.assertEqual(self.db.upsert_note(key_note(), SHA_A, T3), store.REACTIVATED)
        saved = self.db.get_note("demo", "amount")
        self.assertTrue(saved.active)
        self.assertIsNone(saved.inactive_reason)
        self.assertEqual(len(self.db.terms_of("demo", "amount")), 7)

    def test_changed_hash_while_inactive_is_updated_and_active(self):
        self.db.upsert_note(key_note(), SHA_A, T1)
        self.db.mark_inactive("demo", "amount", "missing", T2)
        self.assertEqual(self.db.upsert_note(key_note(body="본문 B"), SHA_B, T3), store.UPDATED)
        self.assertTrue(self.db.get_note("demo", "amount").active)


class RecordEventTest(StoreCase):
    def test_repeated_identical_event_is_logged_once(self):
        self.assertTrue(self.db.record_event("demo", "amount", SHA_A, "parse_error", "missing_field:status", T1))
        self.assertFalse(self.db.record_event("demo", "amount", SHA_A, "parse_error", "missing_field:status", T2))
        self.assertEqual(len(self.db.history("demo", "amount")), 1)

    def test_changed_event_is_logged(self):
        self.db.record_event("demo", "amount", SHA_A, "parse_error", "bad_value:keywords", T1)
        self.assertTrue(self.db.record_event("demo", "amount", SHA_B, "parse_error", "bad_value:keywords", T2))
        self.assertTrue(self.db.record_event("demo", "amount", SHA_B, "mismatch", None, T3))

    def test_same_error_after_a_good_sync_is_logged_again(self):
        self.db.record_event("demo", "amount", SHA_A, "mismatch", None, T1)
        self.db.upsert_note(key_note(), SHA_B, T2)
        self.assertTrue(self.db.record_event("demo", "amount", SHA_A, "mismatch", None, T3))

    def test_product_level_event_has_no_name(self):
        self.assertTrue(self.db.record_event("demo", None, None, "state_error", "state_bad_format", T1))
        self.assertFalse(self.db.record_event("demo", None, None, "state_error", "state_bad_format", T2))
        (entry,) = self.db.history("demo", None)
        self.assertEqual((entry.name, entry.reason), (None, "state_bad_format"))


class OpenTest(unittest.TestCase):
    def test_file_db_is_private_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sub" / "ced.db"
            db = Store.open(path)
            db.upsert_note(key_note(), SHA_A, T1)
            db.close()
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            again = Store.open(path)
            self.addCleanup(again.close)
            self.assertEqual(again.get_note("demo", "amount").body, "본문 A")

    def test_unsupported_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ced.db"
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA user_version = 99")
            conn.close()
            with self.assertRaises(store.StoreError) as ctx:
                Store.open(path)
            self.assertEqual(ctx.exception.reason, store.UNSUPPORTED_SCHEMA)

    def test_foreign_keys_are_enforced(self):
        db = Store.open(":memory:")
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute("INSERT INTO terms (product, name, kind, value) VALUES ('x', 'y', 'name', 'z')")


if __name__ == "__main__":
    unittest.main()
