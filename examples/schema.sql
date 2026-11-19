-- ced-taker DB의 표 구조. 코드(src/ced_taker/store.py)에서 만든 DB에서 읽어 낸 것이다.
-- DB는 볼트의 CED에서 다시 만들 수 있는 캐시이다. 이 파일은 구조를 읽기 위한 문서이다.

PRAGMA user_version = 1;

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

CREATE TABLE ingest_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    product TEXT NOT NULL,
    name TEXT,
    sha256 TEXT,
    outcome TEXT NOT NULL,
    reason TEXT
);

CREATE TABLE misses (
    product TEXT NOT NULL,
    token TEXT NOT NULL,
    near_name TEXT NOT NULL,
    count INTEGER NOT NULL,
    first_at TEXT NOT NULL,
    last_at TEXT NOT NULL,
    PRIMARY KEY (product, token, near_name)
) WITHOUT ROWID;

CREATE INDEX terms_by_value ON terms (product, kind, value);

CREATE INDEX ingest_log_by_note ON ingest_log (product, name, id);
