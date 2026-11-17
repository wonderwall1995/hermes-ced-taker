"""동기화(sync.py) 시험. 임시 폴더의 가짜 볼트를 쓴다."""

import hashlib
import json
import pathlib
import tempfile
import unittest

from ced_taker import state, store, sync
from ced_taker.store import Store

T1, T2, T3 = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"


def key_doc(product="demo", keywords='["amount"]', body="본문 A"):
    return (
        f'---\nproduct: "{product}"\nkeywords: {keywords}\nappearsIn: ["orders.amount"]\n'
        f'tables: ["orders"]\nstatus: confirmed\nupdated: "2026-01-01T00:00:00.000Z"\n---\n# amount\n\n{body}\n'
    )


def index_doc(product="demo"):
    return f'---\nproduct: "{product}"\nnotes: 1\ngenerated: "2026-01-01T00:00:00.000Z"\n---\n# {product}\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class VaultCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.vault = pathlib.Path(tmp.name) / "vault"
        self.product = self.vault / "CED" / "demo"
        self.product.mkdir(parents=True)
        self.files = {}  # 상태 파일에 적을 {파일명: 해시}
        self.db = Store.open(":memory:")
        self.addCleanup(self.db.close)

    def put(self, name, text, listed=True):
        data = text.encode("utf-8") if isinstance(text, str) else text
        (self.product / name).write_bytes(data)
        if listed:
            self.files[name] = sha(data)

    def write_state(self):
        (self.product / state.STATE_FILE).write_text(json.dumps({"version": 1, "files": self.files}), encoding="utf-8")

    def sync(self, now=T1):
        self.write_state()
        return sync.sync_product(self.db, self.product, now, sleep=lambda s: None)

    def outcomes(self, name):
        return [e.outcome for e in self.db.history("demo", name)]


class HappyPathTest(VaultCase):
    def test_first_sync_stores_notes_and_terms(self):
        self.put("amount.md", key_doc())
        self.put("demo.md", index_doc())
        report = self.sync()
        self.assertEqual(report.counts, {"new": 2})
        self.assertEqual(report.issues, ())
        self.assertEqual(self.db.get_note("demo", "amount").body, "# amount\n\n본문 A\n")
        self.assertEqual(self.db.get_note("demo", "demo").kind, "index")
        self.assertIn((store.TERM_COLUMN_REF, "orders.amount"), self.db.terms_of("demo", "amount"))

    def test_second_sync_changes_nothing_and_logs_nothing(self):
        self.put("amount.md", key_doc())
        self.sync(T1)
        report = self.sync(T2)
        self.assertEqual(report.counts, {"unchanged": 1})
        self.assertEqual(self.outcomes("amount"), ["new"])

    def test_changed_document_is_updated(self):
        self.put("amount.md", key_doc())
        self.sync(T1)
        self.put("amount.md", key_doc(body="본문 B"))
        self.assertEqual(self.sync(T2).counts, {"updated": 1})
        self.assertIn("본문 B", self.db.get_note("demo", "amount").body)

    def test_original_files_are_not_modified(self):
        self.put("amount.md", key_doc())
        self.write_state()
        before = {p.name: p.read_bytes() for p in self.product.iterdir()}
        sync.sync_product(self.db, self.product, T1, sleep=lambda s: None)
        self.assertEqual({p.name: p.read_bytes() for p in self.product.iterdir()}, before)

    def test_files_not_in_state_are_ignored(self):
        self.put("amount.md", key_doc())
        self.put("memo.md", "사람이 쓴 메모", listed=False)
        self.sync()
        self.assertIsNone(self.db.get_note("demo", "memo"))


class KeepPreviousContentTest(VaultCase):
    def setUp(self):
        super().setUp()
        self.put("amount.md", key_doc())
        self.sync(T1)

    def test_mismatch_keeps_old_content_and_logs_once(self):
        (self.product / "amount.md").write_text(key_doc(body="사람이 고침"), encoding="utf-8")  # 상태는 그대로
        report = self.sync(T2)
        self.assertEqual([(i.name, i.outcome) for i in report.issues], [("amount.md", "mismatch")])
        self.assertIn("본문 A", self.db.get_note("demo", "amount").body)
        self.sync(T3)  # 같은 불일치가 계속돼도 이력은 한 번만 남는다
        self.assertEqual(self.outcomes("amount"), ["new", "mismatch"])

    def test_broken_new_version_keeps_old_content(self):
        self.put("amount.md", key_doc().replace("status: confirmed\n", ""))  # 해시는 맞지만 형식이 깨짐
        report = self.sync(T2)
        (issue,) = report.issues
        self.assertEqual((issue.outcome, issue.reason), ("parse_error", "unknown_kind"))
        saved = self.db.get_note("demo", "amount")
        self.assertIn("본문 A", saved.body)
        self.assertTrue(saved.active)
        self.sync(T3)
        self.assertEqual(self.outcomes("amount"), ["new", "parse_error"])

    def test_recovers_when_the_document_is_fixed(self):
        self.put("amount.md", key_doc().replace("status: confirmed\n", ""))
        self.sync(T2)
        self.put("amount.md", key_doc(body="본문 C"))
        self.assertEqual(self.sync(T3).counts, {"updated": 1})
        self.assertEqual(self.outcomes("amount"), ["new", "parse_error", "updated"])

    def test_bad_encoding_is_a_parse_error(self):
        self.put("amount.md", b"\xff\xfe\x00 not utf-8")
        (issue,) = self.sync(T2).issues
        self.assertEqual((issue.outcome, issue.reason), ("parse_error", sync.BAD_ENCODING))


class RejectTest(VaultCase):
    def test_product_mismatch_is_skipped(self):
        self.put("amount.md", key_doc(product="other"))
        report = self.sync()
        self.assertEqual([(i.name, i.outcome) for i in report.issues], [("amount.md", "product_mismatch")])
        self.assertIsNone(self.db.get_note("demo", "amount"))
        self.assertIsNone(self.db.get_note("other", "amount"))
        self.assertEqual(self.outcomes("amount"), ["product_mismatch"])

    def test_unsafe_names_are_reported_and_never_stored(self):
        (self.vault / "CED" / "evil.md").write_text(key_doc(), encoding="utf-8")
        self.files["../evil.md"] = sha(key_doc().encode())
        self.put("amount.md", key_doc())
        report = self.sync()
        self.assertIn(("../evil.md", "unsafe"), [(i.name, i.outcome) for i in report.issues])
        self.assertEqual(self.db.active_names("demo"), {"amount"})

    def test_state_error_leaves_db_untouched(self):
        self.put("amount.md", key_doc())
        self.sync(T1)
        (self.product / state.STATE_FILE).write_text("{", encoding="utf-8")
        report = sync.sync_product(self.db, self.product, T2, sleep=lambda s: None)
        self.assertEqual(report.state_error, state.STATE_BAD_FORMAT)
        self.assertTrue(self.db.get_note("demo", "amount").active)
        sync.sync_product(self.db, self.product, T3, sleep=lambda s: None)
        (entry,) = self.db.history("demo", None)
        self.assertEqual((entry.outcome, entry.reason), ("state_error", state.STATE_BAD_FORMAT))


class DisappearTest(VaultCase):
    def setUp(self):
        super().setUp()
        self.put("amount.md", key_doc())
        self.sync(T1)

    def test_missing_file_becomes_inactive_then_reactivates(self):
        (self.product / "amount.md").unlink()
        report = self.sync(T2)
        self.assertEqual(report.counts, {"inactive": 1})
        saved = self.db.get_note("demo", "amount")
        self.assertEqual((saved.active, saved.inactive_reason, "본문 A" in saved.body), (False, "missing", True))
        self.put("amount.md", key_doc())
        self.assertEqual(self.sync(T3).counts, {"reactivated": 1})
        self.assertEqual(self.outcomes("amount"), ["new", "inactive", "reactivated"])

    def test_removed_from_state_becomes_inactive(self):
        del self.files["amount.md"]  # 파일은 남아 있지만 상태 파일에서 빠짐
        report = self.sync(T2)
        self.assertEqual(report.counts, {"inactive": 1})
        self.assertEqual(self.db.get_note("demo", "amount").inactive_reason, sync.NOT_IN_STATE)
        self.assertEqual(self.sync(T3).counts, {})  # 두 번째부터는 아무 일도 없다

    def test_listed_but_never_seen_missing_file_is_logged_without_a_row(self):
        self.files["ghost.md"] = sha(b"x")
        report = self.sync(T2)
        self.assertIn(("ghost.md", "missing"), [(i.name, i.outcome) for i in report.issues])
        self.assertIsNone(self.db.get_note("demo", "ghost"))
        self.assertEqual(self.outcomes("ghost"), ["missing"])


class SyncVaultTest(VaultCase):
    def test_all_products_are_synced(self):
        self.put("amount.md", key_doc())
        self.write_state()
        other = self.vault / "CED" / "other"
        other.mkdir()
        data = key_doc(product="other").encode()
        (other / "x.md").write_bytes(data)
        (other / state.STATE_FILE).write_text(json.dumps({"version": 1, "files": {"x.md": sha(data)}}))
        reports = sync.sync_vault(self.db, self.vault, T1, sleep=lambda s: None)
        self.assertEqual([r.product for r in reports], ["demo", "other"])
        self.assertEqual(self.db.active_names("other"), {"x"})

    def test_no_ced_folder(self):
        empty = self.vault.parent / "empty"
        empty.mkdir()
        self.assertEqual(sync.sync_vault(self.db, empty, T1), [])


if __name__ == "__main__":
    unittest.main()
