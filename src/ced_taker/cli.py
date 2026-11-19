"""명령줄. sync, explain, provide, misses 네 명령이 있다.

출력에는 제품 이름, 파일명, 결과 종류, 사유 코드만 쓴다. 경로는 쓰지 않는다.
explain은 해설 문단을 출력하며, 거기에는 걸린 문서의 본문이 들어간다.
종료 코드: 0 = 실행함(문서 단위 문제는 보고만 한다), 1 = 설정, 저장소, 입력 오류.
"""

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, default_db_path, resolve_vault
from . import misses as misses_report
from . import provider
from .funnel import explain, sync_notice
from .store import Store, StoreError, read_misses
from .sync import product_dir, sync_product, sync_vault

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
    sub.add_parser("provide", help="참고 문서 공급자: 표준 입력의 JSON 요청에 JSON으로 답한다")
    mi = sub.add_parser("misses", help="놓침 기록을 보여 준다 (읽기 전용. DB 내용을 바꾸지 않는다)")
    mi.add_argument("--product", help="이 제품의 기록만 보여 준다 (기본: 모든 제품)")
    mi.add_argument("--json", action="store_true", help="기계가 읽을 수 있는 JSON으로 출력한다")
    mi.add_argument("--limit", type=int, default=misses_report.DEFAULT_LIMIT, help="횟수가 많은 쪽부터 이만큼만 보여 준다 (0이면 전부)")
    return parser


def main(argv=None, env=None, out=None, err=None, stdin=None):
    out, err, stdin = out or sys.stdout, err or sys.stderr, stdin or sys.stdin
    args = _build_parser().parse_args(argv)

    if args.command == "provide":
        return _run_provide(env, out, err, stdin)
    if args.command == "misses":
        return _run_misses(env, out, err, args)

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
        pdir = product_dir(vault, args.product)
        if pdir is None:
            print("오류: product_not_found", file=err)
            return 1
        reports = [sync_product(db, pdir)]
    else:
        reports = sync_vault(db, vault)
    if not reports:
        print("동기화할 제품이 없음", file=out)
    for report in reports:
        for line in format_report(report):
            print(line, file=out)
    return 0


def _run_explain(db, vault, args, out, err, stdin):
    pdir = product_dir(vault, args.product)
    if pdir is None:
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
    report = sync_product(db, pdir)
    result = explain(db, args.product, diff, description, notice=sync_notice(report))
    out.write(result.text)  # 걸린 문서가 없으면 아무것도 출력하지 않는다
    return 0


def _run_provide(env, out, err, stdin):
    """표준 입력의 요청을 읽고 표준 출력에 JSON만 쓴다. 오류는 표준 오류에 사유 코드만 쓰고 종료 코드 1."""
    try:
        raw = stdin.buffer.read(provider.MAX_REQUEST_BYTES + 1) if hasattr(stdin, "buffer") else stdin.read()
        response = provider.provide(provider.parse_request(raw), env=env)
    except (provider.ProviderError, ConfigError, StoreError) as exc:
        print(f"오류: {exc.reason}", file=err)
        return 1
    out.write(json.dumps(response, ensure_ascii=False) + "\n")
    return 0



def _run_misses(env, out, err, args):
    """놓침 기록을 보여 준다. 볼트 설정이 필요 없고, DB가 없어도 오류가 아니다."""
    if args.limit < 0:
        print("오류: bad_limit", file=err)
        return 1
    rows = read_misses(default_db_path(env), args.product)
    if args.json:
        out.write(misses_report.to_json(rows, args.limit))
    else:
        out.write(misses_report.format_table(rows, args.product, args.limit))
    return 0
