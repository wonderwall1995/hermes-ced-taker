"""문서 파서(parser.py) 시험. 시험 자료는 모두 가짜이며 실제 CED 내용을 쓰지 않는다."""

import unittest

from ced_taker import parser
from ced_taker.parser import IndexNote, KeyNote, ParseError, parse_document

KEY_DOC = """---
product: "demo"
keywords: ["amount","total"]
appearsIn: ["orders.amount","refunds.amount"]
tables: ["orders","refunds"]
status: confirmed
updated: "2026-01-01T00:00:00.000Z"
---
# amount

목차: [[demo]]

## 설명

주문 한 건의 금액. 환불 금액일 수도 있다.
"""

INDEX_DOC = """---
product: "demo"
notes: 1
generated: "2026-01-01T00:00:00.000Z"
---
# demo 맥락 문서

- [[amount]] — 금액
"""


def reason_of(text, name="amount"):
    """깨진 문서가 내는 (사유, 필드)를 돌려준다. 깨지지 않으면 시험을 실패시킨다."""
    with unittest.TestCase().assertRaises(ParseError) as ctx:
        parse_document(text, name)
    return ctx.exception.reason, ctx.exception.field


class ParseKeyNoteTest(unittest.TestCase):
    def test_reads_all_fields(self):
        note = parse_document(KEY_DOC, "amount")
        self.assertIsInstance(note, KeyNote)
        self.assertEqual(note.name, "amount")
        self.assertEqual(note.product, "demo")
        self.assertEqual(note.keywords, ("amount", "total"))
        self.assertEqual(note.appears_in, ("orders.amount", "refunds.amount"))
        self.assertEqual(note.tables, ("orders", "refunds"))
        self.assertEqual(note.status, "confirmed")
        self.assertEqual(note.updated, "2026-01-01T00:00:00.000Z")

    def test_body_is_kept_as_is(self):
        note = parse_document(KEY_DOC, "amount")
        self.assertTrue(note.body.startswith("# amount\n"))
        self.assertIn("목차: [[demo]]", note.body)  # 위키링크는 그대로
        self.assertIn("환불 금액일 수도 있다.", note.body)

    def test_crlf_and_bom(self):
        text = "﻿" + KEY_DOC.replace("\n", "\r\n")
        note = parse_document(text, "amount")
        self.assertEqual(note.keywords, ("amount", "total"))
        self.assertNotIn("\r", note.body)

    def test_empty_lists_are_allowed(self):
        text = KEY_DOC.replace('["amount","total"]', "[]").replace('["orders","refunds"]', "[]")
        note = parse_document(text, "amount")
        self.assertEqual(note.keywords, ())
        self.assertEqual(note.tables, ())

    def test_value_may_contain_colon(self):
        text = KEY_DOC.replace('"2026-01-01T00:00:00.000Z"', '"a: b"')
        self.assertEqual(parse_document(text, "amount").updated, "a: b")

    def test_status_other_than_confirmed_is_still_parsed(self):
        note = parse_document(KEY_DOC.replace("confirmed", "draft"), "amount")
        self.assertEqual(note.status, "draft")

    def test_body_may_contain_dashes_line(self):
        note = parse_document(KEY_DOC + "\n---\n\n뒤쪽 구분선\n", "amount")
        self.assertIn("뒤쪽 구분선", note.body)


class ParseIndexNoteTest(unittest.TestCase):
    def test_reads_index(self):
        note = parse_document(INDEX_DOC, "demo")
        self.assertIsInstance(note, IndexNote)
        self.assertEqual(note.name, "demo")
        self.assertEqual(note.product, "demo")
        self.assertEqual(note.notes, 1)
        self.assertEqual(note.generated, "2026-01-01T00:00:00.000Z")
        self.assertIn("[[amount]]", note.body)

    def test_notes_must_be_a_non_negative_integer(self):
        for bad in ('"1"', "-1", "true", "1.5"):
            with self.subTest(bad=bad):
                text = INDEX_DOC.replace("notes: 1", f"notes: {bad}")
                self.assertEqual(reason_of(text, "demo"), (parser.WRONG_TYPE, "notes"))


class BrokenDocumentTest(unittest.TestCase):
    def test_no_front_matter(self):
        self.assertEqual(reason_of("# amount\n본문만 있다.\n")[0], parser.NO_FRONT_MATTER)

    def test_empty_text(self):
        self.assertEqual(reason_of("")[0], parser.NO_FRONT_MATTER)

    def test_unterminated_front_matter(self):
        self.assertEqual(reason_of('---\nproduct: "demo"\n')[0], parser.UNTERMINATED_FRONT_MATTER)

    def test_bad_line(self):
        text = KEY_DOC.replace("status: confirmed", "이 줄은 필드가 아니다")
        self.assertEqual(reason_of(text)[0], parser.BAD_LINE)

    def test_bad_value(self):
        text = KEY_DOC.replace('["amount","total"]', '["amount",')
        self.assertEqual(reason_of(text), (parser.BAD_VALUE, "keywords"))

    def test_empty_value(self):
        text = KEY_DOC.replace("status: confirmed", "status:")
        self.assertEqual(reason_of(text), (parser.BAD_VALUE, "status"))

    def test_duplicate_field(self):
        text = KEY_DOC.replace("status: confirmed", "status: confirmed\nstatus: draft")
        self.assertEqual(reason_of(text), (parser.DUPLICATE_FIELD, "status"))

    def test_unknown_kind(self):
        text = '---\nproduct: "demo"\n---\n본문\n'
        self.assertEqual(reason_of(text)[0], parser.UNKNOWN_KIND)

    def test_ambiguous_kind(self):
        text = KEY_DOC.replace("status: confirmed", "status: confirmed\nnotes: 1")
        self.assertEqual(reason_of(text)[0], parser.AMBIGUOUS_KIND)

    def test_missing_key_field(self):
        for field in parser.KEY_FIELDS:
            if field == "status":  # status가 없으면 종류를 알 수 없다
                continue
            with self.subTest(field=field):
                lines = [ln for ln in KEY_DOC.split("\n") if not ln.startswith(field + ":")]
                self.assertEqual(reason_of("\n".join(lines)), (parser.MISSING_FIELD, field))

    def test_missing_index_field(self):
        text = INDEX_DOC.replace('generated: "2026-01-01T00:00:00.000Z"\n', "")
        self.assertEqual(reason_of(text, "demo"), (parser.MISSING_FIELD, "generated"))

    def test_list_field_must_be_list_of_text(self):
        for bad in ('"amount"', "[1,2]", '["a",1]'):
            with self.subTest(bad=bad):
                text = KEY_DOC.replace('["amount","total"]', bad)
                self.assertEqual(reason_of(text), (parser.WRONG_TYPE, "keywords"))

    def test_text_field_must_be_non_empty_text(self):
        for bad in ('""', "1", '["x"]'):
            with self.subTest(bad=bad):
                text = KEY_DOC.replace('product: "demo"', f"product: {bad}")
                self.assertEqual(reason_of(text), (parser.WRONG_TYPE, "product"))

    def test_error_message_never_contains_body(self):
        text = KEY_DOC.replace("status: confirmed", "status:")
        with self.assertRaises(ParseError) as ctx:
            parse_document(text, "amount")
        self.assertNotIn("주문 한 건", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
