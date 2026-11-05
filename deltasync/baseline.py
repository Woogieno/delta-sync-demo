"""기준선 저장과 사후 검증.

기준선(baseline)은 작업 직전 원본의 행별 컬럼 해시다.
검증기는 기준선이 없거나 깨져 있으면 통과가 아니라 실패한다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .db import columns, list_tables, pk_columns, q

VERSION = 1


class BaselineError(Exception):
    """기준선이 없거나 읽을 수 없다. 검증을 통과로 처리하면 안 된다."""


def _h(value) -> str:
    return hashlib.sha1(repr(value).encode("utf-8")).hexdigest()[:10]


def _row_key(pk_values: tuple) -> str:
    return json.dumps(list(pk_values), ensure_ascii=False)


def snapshot(conn: sqlite3.Connection) -> dict:
    tables = {}
    for t in list_tables(conn):
        cols = columns(conn, t)
        pk = pk_columns(conn, t)
        if not pk:
            # PK가 없는 테이블은 행을 식별할 수 없어 건수만 기록한다.
            n = conn.execute(f"SELECT COUNT(*) FROM {q(t)}").fetchone()[0]
            tables[t] = {"columns": cols, "pk": [], "count": n, "rows": {}}
            continue
        pk_idx = [cols.index(c) for c in pk]
        rows = {}
        for r in conn.execute(f"SELECT {', '.join(q(c) for c in cols)} FROM {q(t)}"):
            key = _row_key(tuple(r[i] for i in pk_idx))
            rows[key] = {c: _h(v) for c, v in zip(cols, r)}
        tables[t] = {"columns": cols, "pk": pk, "count": len(rows), "rows": rows}
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tables": tables,
    }


def write_baseline(conn: sqlite3.Connection, out: str | Path) -> dict:
    snap = snapshot(conn)
    Path(out).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return snap


def load_baseline(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise BaselineError(f"기준선 파일이 없습니다: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BaselineError(f"기준선을 읽을 수 없습니다: {e}") from e
    if not isinstance(data, dict) or data.get("version") != VERSION or "tables" not in data:
        raise BaselineError("기준선 형식이 올바르지 않습니다")
    return data


@dataclass
class TableResult:
    table: str
    missing_table: bool = False
    missing_rows: list[str] = field(default_factory=list)
    changed_rows: list[tuple[str, list[str]]] = field(default_factory=list)
    extra_rows: int = 0
    count_changed: tuple[int, int] | None = None  # PK 없는 테이블의 건수 비교
    baseline_only_cols: list[str] = field(default_factory=list)  # 대상에 칸이 없어 비교 제외

    def failed(self, strict: bool) -> bool:
        return bool(
            self.missing_table
            or self.missing_rows
            or self.changed_rows
            or self.count_changed
            or (strict and self.extra_rows)
        )


def verify(conn: sqlite3.Connection, baseline: dict, strict: bool = False) -> tuple[bool, list[TableResult]]:
    """기준선의 모든 행이 대상에 같은 값으로 있는지 확인한다.

    - 행 누락, 값 변경, 테이블 누락 -> 실패
    - 대상에만 있는 추가 행 -> strict일 때만 실패 (컷오버 뒤 정상적으로 쌓일 수 있음)
    - 대상에 칸이 없는 컬럼은 비교에서 제외하고 알린다 (스키마 교집합 정책과 동일)
    """
    current = snapshot(conn)["tables"]
    results: list[TableResult] = []
    for t, base in baseline["tables"].items():
        res = TableResult(table=t)
        results.append(res)
        cur = current.get(t)
        if cur is None:
            res.missing_table = True
            continue
        shared = [c for c in base["columns"] if c in cur["columns"]]
        res.baseline_only_cols = [c for c in base["columns"] if c not in cur["columns"]]

        if not base["pk"]:
            if cur["count"] != base["count"]:
                res.count_changed = (base["count"], cur["count"])
            continue

        for key, row in base["rows"].items():
            now = cur["rows"].get(key)
            if now is None:
                res.missing_rows.append(key)
                continue
            diff = [c for c in shared if row.get(c) != now.get(c)]
            if diff:
                res.changed_rows.append((key, diff))
        res.extra_rows = len(set(cur["rows"]) - set(base["rows"]))
    ok = not any(r.failed(strict) for r in results)
    return ok, results
