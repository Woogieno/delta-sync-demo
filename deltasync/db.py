"""SQLite 접근 헬퍼. 식별자 인용과 스키마 조회만 담당한다."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {p}")
    uri = p.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
    conn = sqlite3.connect(uri, uri=True)
    return conn


def q(name: str) -> str:
    """식별자를 안전하게 인용한다."""
    return '"' + name.replace('"', '""') + '"'


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({q(table)})")]


def pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """PK 컬럼을 PK 순서대로 반환한다. PK가 없으면 빈 리스트."""
    info = [r for r in conn.execute(f"PRAGMA table_info({q(table)})") if r[5] > 0]
    return [r[1] for r in sorted(info, key=lambda r: r[5])]


def pk_declared_types(conn: sqlite3.Connection, table: str) -> list[str]:
    info = [r for r in conn.execute(f"PRAGMA table_info({q(table)})") if r[5] > 0]
    return [(r[2] or "").upper() for r in sorted(info, key=lambda r: r[5])]
