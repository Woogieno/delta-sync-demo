"""명령줄 인터페이스.

종료 코드: 0 성공 / 1 검증 실패 / 2 사용 오류(기준선 없음 등)
"""
from __future__ import annotations

import argparse
import sys
from contextlib import closing

from . import baseline as bl
from .db import columns, connect, list_tables, pk_columns, pk_declared_types, q
from .delta import TablePlan, apply_plans, plan_all


def _print_plans(plans: list[TablePlan], title: str) -> None:
    print(f"== {title} ==")
    for p in plans:
        if p.status == "skipped":
            print(f"  [건너뜀] {p.table}: {p.reason}")
            continue
        extra = []
        if p.source_only_cols:
            extra.append(f"원본에만 있는 컬럼(이관 제외): {', '.join(p.source_only_cols)}")
        if p.target_only_cols:
            extra.append(f"대상에만 있는 컬럼(기본값/NULL): {', '.join(p.target_only_cols)}")
        print(f"  {p.table}: 반영 대상 {p.to_insert}행" + (f"  ({'; '.join(extra)})" if extra else ""))
    total = sum(p.to_insert for p in plans if p.status == "ok")
    print(f"  합계: {total}행")


def cmd_profile(a) -> int:
    with closing(connect(a.db, readonly=True)) as conn:
        return _profile(conn)


def _profile(conn) -> int:
    print(f"{'테이블':<24}{'PK':<28}{'PK 타입':<14}{'행 수':>8}  PK 유일")
    for t in list_tables(conn):
        pk = pk_columns(conn, t)
        n = conn.execute(f"SELECT COUNT(*) FROM {q(t)}").fetchone()[0]
        if pk:
            cols = ", ".join(q(c) for c in pk)
            d = conn.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT {cols} FROM {q(t)})").fetchone()[0]
            uniq = "예" if d == n else "아니오"
        else:
            uniq = "-"
        print(f"{t:<24}{','.join(pk) or '(없음)':<28}{','.join(pk_declared_types(conn, t)) or '-':<14}{n:>8}  {uniq}")
    return 0


def cmd_plan(a) -> int:
    src, dst = (a.target, a.source) if a.reverse else (a.source, a.target)
    with closing(connect(src, readonly=True)) as s, closing(connect(dst, readonly=True)) as d:
        _print_plans(plan_all(s, d), "역방향 델타 계획 (롤백용)" if a.reverse else "델타 계획")
    return 0


def cmd_apply(a) -> int:
    with closing(connect(a.source, readonly=True)) as s, closing(
        connect(a.target, readonly=not a.execute)
    ) as d:
        return _apply(a, s, d)


def _apply(a, s, d) -> int:
    plans = plan_all(s, d)
    _print_plans(plans, "델타 계획")
    if not a.execute:
        print("\n[dry-run] 변경하지 않았습니다. 반영하려면 --execute 를 붙이세요.")
        return 0
    inserted = apply_plans(s, d, plans)
    print(f"\n반영 완료: {sum(inserted.values())}행 {inserted}")
    remaining = sum(p.to_insert for p in plan_all(s, d) if p.status == "ok")
    print(f"재계획 결과 남은 행: {remaining}행 (0이어야 멱등)")
    return 0 if remaining == 0 else 1


def cmd_baseline(a) -> int:
    with closing(connect(a.db, readonly=True)) as conn:
        snap = bl.write_baseline(conn, a.out)
    rows = sum(t["count"] for t in snap["tables"].values())
    print(f"기준선 저장: {a.out} (테이블 {len(snap['tables'])}개, {rows}행)")
    return 0


def cmd_verify(a) -> int:
    try:
        base = bl.load_baseline(a.baseline)
    except bl.BaselineError as e:
        print(f"실패: {e}", file=sys.stderr)
        return 2
    with closing(connect(a.db, readonly=True)) as conn:
        ok, results = bl.verify(conn, base, strict=a.strict)
    for r in results:
        if r.missing_table:
            print(f"  [실패] {r.table}: 테이블 없음")
        if r.missing_rows:
            print(f"  [실패] {r.table}: 누락 행 {len(r.missing_rows)}건 (예: {r.missing_rows[0]})")
        if r.changed_rows:
            key, cols = r.changed_rows[0]
            print(f"  [실패] {r.table}: 값 변경 {len(r.changed_rows)}건 (예: {key} 컬럼 {cols})")
        if r.count_changed:
            print(f"  [실패] {r.table}: 건수 {r.count_changed[0]} -> {r.count_changed[1]}")
        if r.extra_rows:
            tag = "실패" if a.strict else "참고"
            print(f"  [{tag}] {r.table}: 기준선에 없는 추가 행 {r.extra_rows}건")
        if r.baseline_only_cols:
            print(f"  [참고] {r.table}: 대상에 칸이 없어 비교 제외 {r.baseline_only_cols}")
    print("검증 " + ("통과" if ok else "실패"))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deltasync", description="PK 집합 대조 델타 동기화와 기준선 검증")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("profile", help="테이블별 PK 유형과 유일성 확인")
    sp.add_argument("--db", required=True)
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("plan", help="반영 대상 계산 (읽기 전용)")
    sp.add_argument("--source", required=True)
    sp.add_argument("--target", required=True)
    sp.add_argument("--reverse", action="store_true", help="대상에만 있는 행을 원본으로 되돌리는 계획")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("apply", help="델타 반영 (기본 dry-run)")
    sp.add_argument("--source", required=True)
    sp.add_argument("--target", required=True)
    sp.add_argument("--execute", action="store_true", help="실제로 반영")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("baseline", help="기준선 저장")
    sp.add_argument("--db", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_baseline)

    sp = sub.add_parser("verify", help="기준선 대비 사후 검증")
    sp.add_argument("--db", required=True)
    sp.add_argument("--baseline", required=True)
    sp.add_argument("--strict", action="store_true", help="기준선에 없는 추가 행도 실패로 처리")
    sp.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
