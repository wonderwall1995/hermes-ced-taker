"""CED 문서 파서.

문서 한 개의 텍스트를 읽어 필드와 본문을 꺼낸다. 파일을 읽거나 DB에 쓰지 않는다.
본문은 고치지 않고 그대로 보존한다 (줄바꿈만 LF로 통일).
형식이 깨진 문서는 ParseError를 낸다. 오류에는 사유 코드만 담고 본문은 담지 않는다.
"""

import json
from dataclasses import dataclass

# 사유 코드. 로그에 그대로 기록한다.
NO_FRONT_MATTER = "no_front_matter"
UNTERMINATED_FRONT_MATTER = "unterminated_front_matter"
BAD_LINE = "bad_line"
BAD_VALUE = "bad_value"
DUPLICATE_FIELD = "duplicate_field"
UNKNOWN_KIND = "unknown_kind"
AMBIGUOUS_KIND = "ambiguous_kind"
MISSING_FIELD = "missing_field"
WRONG_TYPE = "wrong_type"

KEY_FIELDS = ("product", "keywords", "appearsIn", "tables", "status", "updated")
INDEX_FIELDS = ("product", "notes", "generated")


class ParseError(Exception):
    """형식이 깨진 문서. reason은 사유 코드, field는 문제가 된 필드 이름(있을 때)."""

    def __init__(self, reason, field=None):
        self.reason = reason
        self.field = field
        super().__init__(reason if field is None else f"{reason}:{field}")


@dataclass(frozen=True)
class KeyNote:
    """Key 문서. name은 파일명에서 온 컬럼명이다."""

    name: str
    product: str
    keywords: tuple
    appears_in: tuple
    tables: tuple
    status: str
    updated: str
    body: str


@dataclass(frozen=True)
class IndexNote:
    """제품 목차 문서."""

    name: str
    product: str
    notes: int
    generated: str
    body: str


def _split_front_matter(text):
    """(front matter 줄 목록, 본문)을 돌려준다."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines[0].strip() != "---":
        raise ParseError(NO_FRONT_MATTER)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body = "\n".join(lines[i + 1 :])
            if body.startswith("\n"):
                body = body[1:]
            return lines[1:i], body
    raise ParseError(UNTERMINATED_FRONT_MATTER)


def _parse_value(raw, key):
    """값 한 개를 읽는다. 따옴표 문자열, 배열, 숫자는 JSON 규칙을 따른다. 그 밖은 맨 글자 문자열이다."""
    raw = raw.strip()
    if raw == "":
        raise ParseError(BAD_VALUE, key)
    if raw[0] in '"[' or raw[0] == "-" or raw[0].isdigit() or raw in ("true", "false", "null"):
        try:
            return json.loads(raw)
        except ValueError:
            raise ParseError(BAD_VALUE, key) from None
    return raw


def _parse_fields(lines):
    fields = {}
    for line in lines:
        if line.strip() == "":
            continue
        key, sep, raw = line.partition(":")
        key = key.strip()
        if not sep or not key or " " in key:
            raise ParseError(BAD_LINE)
        if key in fields:
            raise ParseError(DUPLICATE_FIELD, key)
        fields[key] = _parse_value(raw, key)
    return fields


def _require(fields, names):
    for name in names:
        if name not in fields:
            raise ParseError(MISSING_FIELD, name)


def _text(fields, key):
    value = fields[key]
    if not isinstance(value, str) or value == "":
        raise ParseError(WRONG_TYPE, key)
    return value


def _text_list(fields, key):
    value = fields[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ParseError(WRONG_TYPE, key)
    return tuple(value)


def parse_document(text, name):
    """문서 텍스트를 KeyNote 또는 IndexNote로 바꾼다. 깨졌으면 ParseError."""
    lines, body = _split_front_matter(text)
    fields = _parse_fields(lines)

    is_index = "notes" in fields
    is_key = "status" in fields
    if is_index and is_key:
        raise ParseError(AMBIGUOUS_KIND)
    if not is_index and not is_key:
        raise ParseError(UNKNOWN_KIND)

    if is_index:
        _require(fields, INDEX_FIELDS)
        count = fields["notes"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ParseError(WRONG_TYPE, "notes")
        return IndexNote(
            name=name,
            product=_text(fields, "product"),
            notes=count,
            generated=_text(fields, "generated"),
            body=body,
        )

    _require(fields, KEY_FIELDS)
    return KeyNote(
        name=name,
        product=_text(fields, "product"),
        keywords=_text_list(fields, "keywords"),
        appears_in=_text_list(fields, "appearsIn"),
        tables=_text_list(fields, "tables"),
        status=_text(fields, "status"),
        updated=_text(fields, "updated"),
        body=body,
    )
