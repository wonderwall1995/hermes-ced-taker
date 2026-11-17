"""명령줄. sync와 explain 두 명령이 있다.

출력에는 제품 이름, 파일명, 결과 종류, 사유 코드만 쓴다. 경로는 쓰지 않는다.
explain은 해설 문단을 출력하며, 거기에는 걸린 문서의 본문이 들어간다.
종료 코드: 0 = 실행함(문서 단위 문제는 보고만 한다), 1 = 설정, 저장소, 입력 오류.
"""

import argparse
import sys
from pathlib import Path

from .config import ConfigError, default_db_path, resolve_vault
from .funnel import explain, sync_notice
from .store import Store, StoreError
from .sync import sync_product, sync_vault

_ORDER = ("new", "updated", "unchanged", "reactivated", "inactive")


def format_report(report):
    lines = []
    if report.state_error:
        lines.append(f"{report.product}: 상태 파일 오류 ({report.state_error}), 이전 DB 내용을 그대로 둠")
    else:
        counts = " ".join(f"{k}={report.counts.get(k, 0)}" for k in _ORDER)
        lines.append(f"{report.product}: {counts}")
    for issue in report.issues:
        detail = f" ({issue.reason})" if issue.reason else ""
        lines.append(f"  ! {issue.name}  {issue.outcome}{detail}")
    return lines


def _product_dir(vault, product):
    """제품 폴더를 돌려준다. 이름이 안전하지 않거나 폴더가 없으면 None."""
    path = Path(vault) / "CED" / product
    if not product or "/" in product or "\\" in product or product.startswith(".") or not path.is_dir():
        return None
    return path


def _read_text(source, stdin):
    if source is None:
        return ""
    if source == "-":
        return stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ValueError("input_unreadable") from None


def _build_parser():
    parser = argparse.ArgumentParser(prog="ced_taker")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="볼트의 CED 변화를 DB에 반영한다")
    sync.add_argument("--product", help="이 제품만 동기화한다 (기본: 모두)")
    ex = sub.add_parser("explain", help="diff와 PR 설명에서 CED 해설 문단을 만든다 (먼저 동기화한다)")
    ex.add_argument("--product", required=True)
    ex.add_argument("--diff", help="diff 파일 경로 (- 는 표준 입력)")
    ex.add_argument("--description", help="PR 설명 파일 경로 (- 는 표준 입력)")
    return parser


def main(argv=None, env=None, out=None, err=None, stdin=None):
    out, err, stdin = out or sys.stdout, err or sys.stderr, stdin or sys.stdin
    args = _build_parser().parse_args(argv)

    try:
        vault = resolve_vault(env)
        db = Store.open(default_db_path(env))
    except (ConfigError, StoreError) as exc:
        print(f"오류: {exc.reason}", file=err)
        return 1

    try:
        if args.command == "sync":
            return _run_sync(db, vault, args, out, err)
        return _run_explain(db, vault, args, out, err, stdin)
    finally:
        db.close()


def _run_sync(db, vault, args, out, err):
    if args.product:
        product_dir = _product_dir(vault, args.product)
        if product_dir is None:
            print("오류: product_not_found", file=err)
            return 1
        reports = [sync_product(db, product_dir)]
    else:
        reports = sync_vault(db, vault)
    if not reports:
        print("동기화할 제품이 없음", file=out)
    for report in reports:
        for line in format_report(report):
            print(line, file=out)
    return 0


def _run_explain(db, vault, args, out, err, stdin):
    product_dir = _product_dir(vault, args.product)
    if product_dir is None:
        print("오류: product_not_found", file=err)
        return 1
    if args.diff == "-" and args.description == "-":
        print("오류: stdin_used_twice", file=err)
        return 1
    try:
        diff = _read_text(args.diff, stdin)
        description = _read_text(args.description, stdin)
    except ValueError as exc:
        print(f"오류: {exc}", file=err)
        return 1
    report = sync_product(db, product_dir)
    result = explain(db, args.product, diff, description, notice=sync_notice(report))
    out.write(result.text)  # 걸린 문서가 없으면 아무것도 출력하지 않는다
    return 0
