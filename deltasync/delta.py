"""PK 집합 대조 기반 델타 동기화.

`id > 최대값` 방식은 PK가 UUID(정렬 불가)이거나 복합키일 때 성립하지 않는다.
그래서 양쪽 PK 집합을 전량 대조해 대상에 없는 행만 반영한다.
스키마가 어긋난 경우에는 양쪽에 모두 있는 컬럼(교집합)만 옮긴다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .db import columns, list_tables, pk_columns, q


@dataclass
class TablePlan:
    table: str
    status: str = "ok"  # ok | skipped
    reason: str = ""
    pk: list[str] = field(default_factory=list)
    common_cols: list[str] = field(default_factory=list)
    source_only_cols: list[str] = field(default_factory=list)  # 대상에 칸이 없는 컬럼
    target_only_cols: list[str] = field(default_factory=list)  # 이관 후 NULL/기본값이 되는 컬럼
    missing_pks: list[tuple] = field(default_factory=list)

    @property
    def to_insert(self) -> int:
        return len(self.missing_pks)


def _pk_set(conn: sqlite3.Connection, table: str, pk: list[str]) -> set[tuple]:
    cols = ", ".join(q(c) for c in pk)
    return {tuple(r) for r in conn.execute(f"SELECT {cols} FROM {q(table)}")}


def _is_unique(conn: sqlite3.Connection, table: str, pk: list[str]) -> bool:
    cols = ", ".join(q(c) for c in pk)
    total = conn.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
    distinct = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {cols} FROM {q(table)})"
    ).fetchone()[0]
    return total == distinct


def plan_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> TablePlan:
    plan = TablePlan(table=table)

    if table not in list_tables(dst):
        plan.status, plan.reason = "skipped", "대상에 테이블이 없음"
        return plan

    pk = pk_columns(src, table)
    if not pk:
        plan.status, plan.reason = "skipped", "PK가 없어 행 식별 불가"
        return plan
    plan.pk = pk

    s_cols, d_cols = columns(src, table), columns(dst, table)
    plan.common_cols = [c for c in s_cols if c in d_cols]
    plan.source_only_cols = [c for c in s_cols if c not in d_cols]
    plan.target_only_cols = [c for c in d_cols if c not in s_cols]

    if not all(c in plan.common_cols for c in pk):
        plan.status, plan.reason = "skipped", "PK 컬럼이 대상에 없음"
        return plan

    # 대조가 성립하려면 PK가 양쪽에서 유일해야 한다.
    if not (_is_unique(src, table, pk) and _is_unique(dst, table, pk)):
        plan.status, plan.reason = "skipped", "PK가 유일하지 않아 대조 불성립"
        return plan

    plan.missing_pks = sorted(_pk_set(src, table, pk) - _pk_set(dst, table, pk), key=repr)
    return plan


def plan_all(src: sqlite3.Connection, dst: sqlite3.Connection) -> list[TablePlan]:
    return [plan_table(src, dst, t) for t in list_tables(src)]


def apply_plans(
    src: sqlite3.Connection, dst: sqlite3.Connection, plans: list[TablePlan]
) -> dict[str, int]:
    """계획을 대상에 반영한다. 한 트랜잭션이라 중간에 실패하면 전부 되돌린다."""
    inserted: dict[str, int] = {}
    dst.execute("BEGIN")
    try:
        for p in plans:
            if p.status != "ok" or not p.missing_pks:
                continue
            cols = ", ".join(q(c) for c in p.common_cols)
            marks = ", ".join("?" for _ in p.common_cols)
            wanted = set(p.missing_pks)
            pk_idx = [p.common_cols.index(c) for c in p.pk]
            rows = [
                r
                for r in src.execute(f"SELECT {cols} FROM {q(p.table)}")
                if tuple(r[i] for i in pk_idx) in wanted
            ]
            dst.executemany(f"INSERT INTO {q(p.table)} ({cols}) VALUES ({marks})", rows)
            inserted[p.table] = len(rows)
        dst.execute("COMMIT")
    except Exception:
        dst.execute("ROLLBACK")
        raise
    return inserted
