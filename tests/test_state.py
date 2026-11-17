"""상태 파일 읽기와 해시 검증(state.py) 시험. 임시 폴더에 가짜 볼트를 만들어 쓴다."""

import hashlib
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from ced_taker import state


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_state(product_dir, files, version=1):
    (pathlib.Path(product_dir) / state.STATE_FILE).write_text(
        json.dumps({"version": version, "files": files}), encoding="utf-8"
    )


class TempVaultCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ced = pathlib.Path(tmp.name) / "CED"
        self.product = self.ced / "demo"
        self.product.mkdir(parents=True)
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def put(self, name, data=b"hello"):
        (self.product / name).write_bytes(data)
        return sha(data)

    def verify(self, **kwargs):
        return state.verify_product(self.product, sleep=self.sleep, **kwargs)


class VerifyProductTest(TempVaultCase):
    def test_match_returns_the_bytes_that_were_hashed(self):
        digest = self.put("amount.md", b"abc")
        write_state(self.product, {"amount.md": digest})
        result = self.verify()
        self.assertEqual(result.product, "demo")
        (check,) = result.files
        self.assertEqual((check.name, check.status, check.actual), ("amount.md", state.MATCH, digest))
        self.assertEqual(check.data, b"abc")

    def test_mismatch_means_modified_or_in_transit(self):
        self.put("amount.md", b"changed")
        write_state(self.product, {"amount.md": sha(b"original")})
        (check,) = self.verify().files
        self.assertEqual(check.status, state.MISMATCH)
        self.assertEqual(check.actual, sha(b"changed"))
        self.assertIsNone(check.data)  # 맞지 않는 내용은 넘기지 않는다

    def test_missing_file(self):
        write_state(self.product, {"gone.md": sha(b"x")})
        (check,) = self.verify().files
        self.assertEqual(check.status, state.MISSING)

    def test_files_not_in_state_are_ignored(self):
        digest = self.put("amount.md")
        self.put("memo.md", b"human note")
        write_state(self.product, {"amount.md": digest})
        self.assertEqual([c.name for c in self.verify().files], ["amount.md"])

    def test_results_are_sorted_by_name(self):
        write_state(self.product, {"b.md": self.put("b.md"), "a.md": self.put("a.md")})
        self.assertEqual([c.name for c in self.verify().files], ["a.md", "b.md"])

    def test_empty_state_is_valid(self):
        write_state(self.product, {})
        self.assertEqual(self.verify().files, ())


class UnsafePathTest(TempVaultCase):
    def test_names_that_leave_the_product_folder_are_never_opened(self):
        (self.ced / "evil.md").write_bytes(b"outside")
        (self.product / "sub").mkdir()
        (self.product / "sub" / "x.md").write_bytes(b"nested")
        names = ["../evil.md", "sub/x.md", "/etc/passwd.md", "..\\evil.md", "notes.txt", ".hidden.md", ".md", ""]
        write_state(self.product, {n: sha(b"x") for n in names})
        result = self.verify()
        self.assertEqual({c.status for c in result.files}, {state.UNSAFE})
        self.assertTrue(all(c.data is None and c.actual is None for c in result.files))

    def test_symlink_is_not_followed(self):
        outside = self.ced / "outside.md"
        outside.write_bytes(b"outside")
        os.symlink(outside, self.product / "link.md")
        write_state(self.product, {"link.md": sha(b"outside")})
        (check,) = self.verify().files
        self.assertEqual(check.status, state.UNSAFE)
        self.assertIsNone(check.data)

    def test_directory_named_like_a_file_is_unsafe(self):
        (self.product / "dir.md").mkdir()
        write_state(self.product, {"dir.md": sha(b"x")})
        (check,) = self.verify().files
        self.assertEqual(check.status, state.UNSAFE)

    def test_fifo_is_unsafe_and_does_not_hang(self):
        os.mkfifo(self.product / "pipe.md")
        write_state(self.product, {"pipe.md": sha(b"x")})
        (check,) = self.verify().files  # 멈추면 이 시험이 끝나지 않는다
        self.assertEqual(check.status, state.UNSAFE)


class StateErrorTest(TempVaultCase):
    def reason(self):
        with self.assertRaises(state.StateError) as ctx:
            self.verify()
        return ctx.exception.reason

    def test_missing_state_does_not_retry(self):
        self.assertEqual(self.reason(), state.STATE_MISSING)
        self.assertEqual(self.sleeps, [])

    def test_unsupported_version_does_not_retry(self):
        write_state(self.product, {}, version=2)
        self.assertEqual(self.reason(), state.STATE_UNSUPPORTED_VERSION)
        self.assertEqual(self.sleeps, [])

    def test_broken_json_retries_three_times_then_fails(self):
        (self.product / state.STATE_FILE).write_text('{"version": 1, "fi', encoding="utf-8")
        self.assertEqual(self.reason(), state.STATE_BAD_FORMAT)
        self.assertEqual(self.sleeps, [0.5, 0.5, 0.5])

    def test_bad_structure(self):
        bad_docs = [
            "[]",
            '{"files": {}}',
            '{"version": "1", "files": {}}',
            '{"version": 1}',
            '{"version": 1, "files": []}',
            '{"version": 1, "files": {"a.md": "not-a-sha"}}',
            '{"version": 1, "files": {"a.md": 5}}',
        ]
        for doc in bad_docs:
            with self.subTest(doc=doc):
                self.sleeps.clear()
                (self.product / state.STATE_FILE).write_text(doc, encoding="utf-8")
                self.assertEqual(self.reason(), state.STATE_BAD_FORMAT)

    def test_invalid_utf8(self):
        (self.product / state.STATE_FILE).write_bytes(b"\xff\xfe\x00")
        self.assertEqual(self.reason(), state.STATE_BAD_FORMAT)

    def test_recovers_when_the_state_is_fixed_while_waiting(self):
        digest = self.put("amount.md")
        (self.product / state.STATE_FILE).write_text("{", encoding="utf-8")

        def fix_on_wait(seconds):
            self.sleeps.append(seconds)
            write_state(self.product, {"amount.md": digest})

        result = state.verify_product(self.product, sleep=fix_on_wait)
        self.assertEqual(result.files[0].status, state.MATCH)
        self.assertEqual(self.sleeps, [0.5])

    def test_error_reason_does_not_contain_path(self):
        with self.assertRaises(state.StateError) as ctx:
            self.verify()
        self.assertNotIn(str(self.product), str(ctx.exception))


class RaceTest(TempVaultCase):
    def test_state_changed_during_hashing_is_redone(self):
        old, new = self.put("amount.md", b"old"), sha(b"new")
        write_state(self.product, {"amount.md": old})
        original = state._check_file
        calls = []

        def rewrite_once(*args):
            calls.append(args)
            if len(calls) == 1:  # 첫 계산 중에 송신 앱이 전송을 끝낸 상황
                (self.product / "amount.md").write_bytes(b"new")
                write_state(self.product, {"amount.md": new})
            return original(*args)

        with mock.patch.object(state, "_check_file", rewrite_once):
            result = self.verify()
        self.assertEqual(len(calls), 2)  # 한 번 다시 했다
        self.assertEqual(result.files[0].status, state.MATCH)
        self.assertEqual(result.files[0].actual, new)

    def test_gives_up_after_two_redos(self):
        write_state(self.product, {"amount.md": sha(b"0")})
        original = state._check_file
        calls = []

        def always_rewrite(*args):
            calls.append(args)
            write_state(self.product, {"amount.md": sha(str(len(calls)).encode())})
            return original(*args)

        with mock.patch.object(state, "_check_file", always_rewrite):
            with self.assertRaises(state.StateError) as ctx:
                self.verify()
        self.assertEqual(ctx.exception.reason, state.STATE_CHANGING)
        self.assertEqual(len(calls), 3)  # 처음 한 번과 다시 두 번


class ScanProductsTest(TempVaultCase):
    def test_lists_only_folders_with_a_state_file(self):
        write_state(self.product, {})
        (self.ced / "no_state").mkdir()
        (self.ced / ".hidden").mkdir()
        write_state(self.ced / ".hidden", {})
        (self.ced / "file.txt").write_text("x")
        other = self.ced / "alpha"
        other.mkdir()
        write_state(other, {})
        self.assertEqual([p.name for p in state.scan_products(self.ced)], ["alpha", "demo"])

    def test_symlinked_folder_is_skipped(self):
        outside = self.ced.parent / "elsewhere"
        outside.mkdir()
        write_state(outside, {})
        os.symlink(outside, self.ced / "linked")
        self.assertEqual(state.scan_products(self.ced), [])

    def test_missing_ced_folder_gives_empty_list(self):
        self.assertEqual(state.scan_products(self.ced / "nope"), [])


if __name__ == "__main__":
    unittest.main()
