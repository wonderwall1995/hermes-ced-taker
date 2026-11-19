"""SQLite 저장소.

DB는 볼트의 CED에서 다시 만들 수 있는 캐시이며, 원문의 저장소가 아니다.
- notes: 문서 한 개당 한 줄. 최신 본문만 둔다 (덮어쓰기).
- terms: 조회 열쇠. 종류는 name(컬럼명), keyword, table, column_ref(테이블.컬럼) 네 가지다.
- ingest_log: 변화가 있을 때만 기록한다. 본문은 기록하지 않고, 해시와 사유 코드만 남긴다.
시각은 호출하는 쪽이 넘긴다 (시험에서 고정하기 위해서).
"""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .parser import KeyNote

SCHEMA_VERSION = 1

# 문서 종류
KEY = "key"
INDEX = "index"

# 조회 열쇠 종류
TERM_NAME = "name"
TERM_KEYWORD = "keyword"
TERM_TABLE = "table"
TERM_COLUMN_REF = "column_ref"

# upsert_note 결과
NEW = "new"
UPDATED = "updated"
UNCHANGED = "unchanged"
REACTIVATED = "reactivated"

# 이력에만 쓰는 결과. 파일 검사 결과(mismatch, missing, unsafe)와 오류(parse_error, state_error)도 같은 칸에 쓴다.
INACTIVE = "inactive"

UNSUPPORTED_SCHEMA = "unsupported_schema"

_SCHEMA = """
CREATE TABLE notes (
    product TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('key', 'index')),
    sha256 TEXT NOT NULL,
    status TEXT,
    doc_time TEXT NOT NULL,
    note_count INTEGER,
    body TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    inactive_reason TEXT,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (product, name)
) WITHOUT ROWID;

CREATE TABLE terms (
    product TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('name', 'keyword', 'table', 'column_ref')),
    value TEXT NOT NULL,
    PRIMARY KEY (product, name, kind, value),
    FOREIGN KEY (product, name) REFERENCES notes (product, name) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX terms_by_value ON terms (product, kind, value);

CREATE TABLE ingest_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    product TEXT NOT NULL,
    name TEXT,
    sha256 TEXT,
    outcome TEXT NOT NULL,
    reason TEXT
);
CREATE INDEX ingest_log_by_note ON ingest_log (product, name, id);

CREATE TABLE misses (
    product TEXT NOT NULL,
    token TEXT NOT NULL,
    near_name TEXT NOT NULL,
    count INTEGER NOT NULL,
    first_at TEXT NOT NULL,
    last_at TEXT NOT NULL,
    PRIMARY KEY (product, token, near_name)
) WITHOUT ROWID;
"""


class StoreError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class StoredNote:
    product: str
    name: str
    kind: str
    sha256: str
    status: str
    doc_time: str
    note_count: int
    body: str
    active: bool
    inactive_reason: str
    changed_at: str


@dataclass(frozen=True)
class LogEntry:
    at: str
    product: str
    name: str
    sha256: str
    outcome: str
    reason: str


def _terms_of(note):
    """Key 문서에서 조회 열쇠를 뽑는다. 목차에는 열쇠가 없다."""
    terms = {(TERM_NAME, note.name)}
    terms.update((TERM_KEYWORD, v) for v in note.keywords)
    terms.update((TERM_TABLE, v) for v in note.tables)
    terms.update((TERM_COLUMN_REF, v) for v in note.appears_in)
    return terms


class Store:
    def __init__(self, conn):
        self.conn = conn

    @classmethod
    def open(cls, path):
        """DB를 연다. 없으면 만들고(권한 600), 스키마 버전이 다르면 StoreError."""
        if str(path) != ":memory:":
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fresh = not path.exists()
            if fresh:  # 만들어지는 순간부터 다른 사용자가 읽지 못하게 한다
                os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")  # 조회가 여러 개 동시에 돌아도 서로 막지 않는다
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with conn:
                conn.executescript(_SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            conn.close()
            raise StoreError(UNSUPPORTED_SCHEMA)
        return cls(conn)

    def close(self):
        self.conn.close()

    # ---- 쓰기 ----

    def upsert_note(self, note, sha256, now):
        """문서를 저장한다. NEW, UPDATED, REACTIVATED, UNCHANGED 중 하나를 돌려준다.

        해시가 같고 활성 상태면 아무것도 쓰지 않는다. 바뀌었을 때만 이력에 남긴다.
        """
        kind = KEY if isinstance(note, KeyNote) else INDEX
        row = self.conn.execute(
            "SELECT sha256, kind, active FROM notes WHERE product = ? AND name = ?",
            (note.product, note.name),
        ).fetchone()
        if row is None:
            outcome = NEW
        elif row[0] == sha256 and row[1] == kind:
            if row[2]:
                return UNCHANGED
            outcome = REACTIVATED
        else:
            outcome = UPDATED
        with self.conn:
            self.conn.execute(
                "INSERT INTO notes (product, name, kind, sha256, status, doc_time, note_count, body, active,"
                " inactive_reason, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?)"
                " ON CONFLICT (product, name) DO UPDATE SET kind = excluded.kind, sha256 = excluded.sha256,"
                " status = excluded.status, doc_time = excluded.doc_time, note_count = excluded.note_count,"
                " body = excluded.body, active = 1, inactive_reason = NULL, changed_at = excluded.changed_at",
                (
                    note.product,
                    note.name,
                    kind,
                    sha256,
                    note.status if kind == KEY else None,
                    note.updated if kind == KEY else note.generated,
                    note.notes if kind == INDEX else None,
                    note.body,
                    now,
                ),
            )
            self.conn.execute("DELETE FROM terms WHERE product = ? AND name = ?", (note.product, note.name))
            if kind == KEY:
                self.conn.executemany(
                    "INSERT INTO terms (product, name, kind, value) VALUES (?, ?, ?, ?)",
                    [(note.product, note.name, k, v) for k, v in sorted(_terms_of(note))],
                )
            self._log(now, note.product, note.name, sha256, outcome, None)
        return outcome

    def mark_inactive(self, product, name, reason, now):
        """문서를 비활성으로 바꾼다. 이미 비활성이거나 없으면 False. 행은 지우지 않는다."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE notes SET active = 0, inactive_reason = ?, changed_at = ?"
                " WHERE product = ? AND name = ? AND active = 1",
                (reason, now, product, name),
            )
            if cur.rowcount == 0:
                return False
            sha = self.conn.execute(
                "SELECT sha256 FROM notes WHERE product = ? AND name = ?", (product, name)
            ).fetchone()[0]
            self._log(now, product, name, sha, INACTIVE, reason)
        return True

    def record_event(self, product, name, sha256, outcome, reason, now):
        """저장하지 않는 사건(해시 불일치, 오류 등)을 이력에 남긴다.

        직전 기록과 해시, 결과, 사유가 모두 같으면 남기지 않는다. 남겼으면 True.
        """
        last = self.conn.execute(
            "SELECT sha256, outcome, reason FROM ingest_log WHERE product = ? AND name IS ? ORDER BY id DESC LIMIT 1",
            (product, name),
        ).fetchone()
        if last == (sha256, outcome, reason):
            return False
        with self.conn:
            self._log(now, product, name, sha256, outcome, reason)
        return True

    def record_misses(self, product, pairs, now):
        """놓침을 기록한다. pairs는 (글에 나온 단어, 거의 같은 컬럼명) 목록이다. 조회 한 번에 1씩 센다."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO misses (product, token, near_name, count, first_at, last_at) VALUES (?, ?, ?, 1, ?, ?)"
                " ON CONFLICT (product, token, near_name) DO UPDATE SET count = count + 1, last_at = excluded.last_at",
                [(product, token, near, now, now) for token, near in sorted(set(pairs))],
            )

    def _log(self, now, product, name, sha256, outcome, reason):
        self.conn.execute(
            "INSERT INTO ingest_log (at, product, name, sha256, outcome, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (now, product, name, sha256, outcome, reason),
        )

    # ---- 읽기 ----

    def get_note(self, product, name):
        row = self.conn.execute(
            "SELECT product, name, kind, sha256, status, doc_time, note_count, body, active, inactive_reason,"
            " changed_at FROM notes WHERE product = ? AND name = ?",
            (product, name),
        ).fetchone()
        if row is None:
            return None
        return StoredNote(*row[:8], bool(row[8]), *row[9:])

    def usable_names(self, product):
        """해설에 쓸 수 있는 Key 문서의 이름. 활성이고 status가 confirmed인 것만."""
        rows = self.conn.execute(
            "SELECT name FROM notes WHERE product = ? AND kind = 'key' AND active = 1 AND status = 'confirmed'",
            (product,),
        )
        return {r[0] for r in rows}

    def find_usable(self, product, names):
        """컬럼명이 정확히 같은(대소문자 구분) 해설용 문서를 이름순으로 돌려준다."""
        names = sorted(set(names))
        found = []
        for i in range(0, len(names), 500):  # SQLite 변수 개수 제한을 피한다
            chunk = names[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                "SELECT product, name, kind, sha256, status, doc_time, note_count, body, active, inactive_reason,"
                " changed_at FROM notes WHERE product = ? AND kind = 'key' AND active = 1 AND status = 'confirmed'"
                f" AND name IN ({marks})",
                [product, *chunk],
            )
            found.extend(StoredNote(*r[:8], bool(r[8]), *r[9:]) for r in rows)
        return sorted(found, key=lambda n: n.name)

    def misses(self, product):
        rows = self.conn.execute(
            "SELECT token, near_name, count FROM misses WHERE product = ? ORDER BY token, near_name", (product,)
        )
        return [tuple(r) for r in rows]

    def active_names(self, product):
        rows = self.conn.execute("SELECT name FROM notes WHERE product = ? AND active = 1", (product,))
        return {r[0] for r in rows}

    def terms_of(self, product, name):
        rows = self.conn.execute("SELECT kind, value FROM terms WHERE product = ? AND name = ?", (product, name))
        return set(rows)

    def history(self, product, name):
        """문서 하나의 이력을 오래된 것부터 돌려준다. name이 None이면 제품 단위 사건이다."""
        rows = self.conn.execute(
            "SELECT at, product, name, sha256, outcome, reason FROM ingest_log"
            " WHERE product = ? AND name IS ? ORDER BY id",
            (product, name),
        )
        return [LogEntry(*r) for r in rows]


def read_misses(path, product=None):
    """DB를 읽기 전용으로 열어 놓침 기록을 돌려준다. DB나 표가 없으면 빈 목록이다.

    DB를 새로 만들지 않고 내용을 바꾸지 않는다(읽기 전용 연결). 다만 WAL 모드의 DB를 읽으면 SQLite가 부속 파일(-wal, -shm)을
    만들 수 있다. 이것은 정상이고 DB 본 파일은 바뀌지 않는다. 부속 파일을 만들지 않는 immutable 방식은 쓰지 않는다.
    다른 프로세스가 동시에 쓰는 중이면 읽는 값이 어긋날 수 있기 때문이다.
    행: (제품, 단어, 거의 같은 컬럼명, 횟수, 처음 본 시각, 마지막으로 본 시각). 횟수가 많은 순, 그다음 제품, 단어, 컬럼명 순이다.
    """
    path = Path(path)
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        query = "SELECT product, token, near_name, count, first_at, last_at FROM misses"
        args = ()
        if product is not None:
            query += " WHERE product = ?"
            args = (product,)
        query += " ORDER BY count DESC, product, token, near_name"
        return [tuple(r) for r in conn.execute(query, args)]
    except sqlite3.Error:  # 표가 없는 옛 DB, 깨진 파일
        return []
    finally:
        conn.close()
