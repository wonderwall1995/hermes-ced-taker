"""데모 만들기: 데모 볼트에서 DB, 스키마 SQL, 해설 예시, 공급자 계약의 요청과 응답 예시를 만든다.

모두 같은 입력(examples/)과 고정된 시각으로 만들기 때문에 결과가 늘 같다. 시험이 올려 둔 파일과 지금 만든 결과가
같은지 확인해서, 예시가 코드와 어긋나지 않게 한다.
"""

import json
import os
import pathlib
import tempfile

from .funnel import explain, sync_notice
from .provider import parse_request, provide
from .store import Store
from .sync import product_dir, sync_product

FIXED_NOW = "2026-01-01T00:00:00Z"
DEMO_PRODUCT = "rwa"
DEMO_REPO = "acme/shop"  # 예시 저장소 이름. 실제 저장소가 아니다.

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"


def schema_sql(db_path):
    """DB에서 표 구조를 읽어 SQL 텍스트로 만든다. 코드에서 만든 구조가 그대로 문서가 된다."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        statements = [r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
    finally:
        conn.close()
    header = ("-- ced-taker DB의 표 구조. 코드(src/ced_taker/store.py)에서 만든 DB에서 읽어 낸 것이다.\n"
              "-- DB는 볼트의 CED에서 다시 만들 수 있는 캐시이다. 이 파일은 구조를 읽기 위한 문서이다.\n\n")
    return header + f"PRAGMA user_version = {version};\n\n" + ";\n\n".join(statements) + ";\n"


def build_db(vault, db_path, now=FIXED_NOW):
    """볼트의 모든 제품을 동기화해 DB를 만든다. 부속 파일(WAL) 없이 한 파일로 남긴다."""
    db_path = pathlib.Path(db_path)
    if db_path.exists():
        db_path.unlink()
    db = Store.open(db_path)
    try:
        report = sync_product(db, product_dir(vault, DEMO_PRODUCT), now)
        db.conn.execute("PRAGMA journal_mode = DELETE")
        db.conn.execute("VACUUM")
    finally:
        db.close()
    return report


def build_examples(examples_dir=EXAMPLES, work_dir=None, now=FIXED_NOW):
    """예시 파일의 내용을 만들어 {파일 이름: 텍스트}로 돌려준다. DB는 work_dir에 만든다."""
    examples_dir = pathlib.Path(examples_dir)
    vault = examples_dir / "demo-vault"
    diff = (examples_dir / "sample.diff").read_text(encoding="utf-8")
    description = (examples_dir / "sample-description.txt").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(work_dir) if work_dir else pathlib.Path(tmp)
        db_path = work / "demo.db"
        report = build_db(vault, db_path, now)
        schema = schema_sql(db_path)

        db = Store.open(db_path)
        try:
            result = explain(db, DEMO_PRODUCT, diff, description, notice=sync_notice(report), now=now)
        finally:
            db.close()

        # 공급자 계약의 요청과 응답. 설정은 임시 파일로 만들고 환경 변수로 가리킨다.
        config = work / "config.json"
        config.write_text(json.dumps({"vault": str(vault)}), encoding="utf-8")  # repos 제한 없이: 기본은 모든 제품
        request = {"version": 1, "repo": DEMO_REPO, "title": "합계 로직 수정", "description": description.strip(), "diff": diff}
        env = {"CED_TAKER_CONFIG": str(config), "CED_TAKER_DB": str(work / "provide.db"), "HERMES_HOME": str(work / "home")}
        response = provide(parse_request(json.dumps(request)), env=env, now=now)

    def dump(data):
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    return {
        "schema.sql": schema,
        "explain-example.md": result.text,
        "provide-request.json": dump(request),
        "provide-response.json": dump(response),
    }, db_path.name


def main():
    """examples/ 의 생성 파일(demo.db, schema.sql, explain-example.md, provide-*.json)을 다시 만든다."""
    with tempfile.TemporaryDirectory() as tmp:
        files, _ = build_examples(work_dir=tmp)
        for name, text in files.items():
            (EXAMPLES / name).write_text(text, encoding="utf-8")
        build_db(EXAMPLES / "demo-vault", EXAMPLES / "demo.db")
    for name in sorted(os.listdir(EXAMPLES)):
        print(name)


if __name__ == "__main__":
    main()
