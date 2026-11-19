"""놓침 기록을 사람이 읽는 표와 JSON으로 보여 준다.

놓침은 글에는 나왔지만 컬럼명과 정확히 같지는 않은 단어이다(예: 글의 accountBalance와 DB의 account_balance).
이 기록으로 이름 규칙을 넓힐지(대소문자 무시, 밑줄 차이 무시) 판단한다. 그래서 차이의 유형별 개수를 함께 보여 준다.
이 모듈은 DB를 읽은 결과(행 목록)만 다룬다. 파일을 열지 않는다.
"""

import json
import unicodedata

CASE = "case"  # 대소문자만 다르다
SHAPE = "shape"  # 밑줄이나 모양이 다르다 (snake_case와 camelCase 등)
KIND_LABELS = {CASE: "대소문자만 다름", SHAPE: "밑줄·모양 차이"}
DEFAULT_LIMIT = 50


def classify(token, near_name):
    """단어와 컬럼명의 차이 유형. 대소문자만 다르면 case, 그 밖에는 shape이다."""
    return CASE if token.lower() == near_name.lower() else SHAPE


def summarize(rows):
    """전체 행 기준의 요약: 쌍의 수, 합계 횟수, 유형별 쌍의 수와 횟수."""
    kinds = {CASE: {"pairs": 0, "total": 0}, SHAPE: {"pairs": 0, "total": 0}}
    for _product, token, near, count, _first, _last in rows:
        entry = kinds[classify(token, near)]
        entry["pairs"] += 1
        entry["total"] += count
    return {"pairs": len(rows), "total": sum(r[3] for r in rows), "kinds": kinds}


def to_json(rows, limit=DEFAULT_LIMIT):
    shown = rows[:limit] if limit else rows
    return json.dumps(
        {
            "version": 1,
            "summary": summarize(rows),
            "misses": [
                {"product": p, "token": t, "near_name": n, "kind": classify(t, n), "count": c, "first_at": f, "last_at": la}
                for p, t, n, c, f, la in shown
            ],
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _width(text):
    """터미널에서 차지하는 칸 수. 한글처럼 넓은 글자는 두 칸으로 센다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text, width):
    return text + " " * max(0, width - _width(text))


def format_table(rows, product=None, limit=DEFAULT_LIMIT):
    """사람이 읽는 표. 기록이 없으면 그렇다고 말한다."""
    scope = product if product is not None else "전체"
    if not rows:
        return f"놓침 기록이 없다 (제품: {scope})\n"
    summary = summarize(rows)
    shown = rows[:limit] if limit else rows
    header = ("제품", "단어", "거의 같은 컬럼명", "유형", "횟수", "처음 본 시각", "마지막으로 본 시각")
    body = [(p, t, n, KIND_LABELS[classify(t, n)], str(c), f, la) for p, t, n, c, f, la in shown]
    widths = [max(_width(row[i]) for row in [header] + body) for i in range(len(header))]
    lines = [f"놓침 기록 (제품: {scope}) - {summary['pairs']}쌍, 합계 {summary['total']}회"]
    for row in [header] + body:
        lines.append("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip())
    if len(shown) < len(rows):
        lines.append(f"(횟수가 많은 {len(shown)}쌍만 보여 줌. 전체는 {len(rows)}쌍. --limit 0 이면 전부 보여 준다)")
    kinds = summary["kinds"]
    lines.append("유형별: " + ", ".join(f"{KIND_LABELS[k]} {kinds[k]['pairs']}쌍({kinds[k]['total']}회)" for k in (CASE, SHAPE)))
    return "\n".join(lines) + "\n"
