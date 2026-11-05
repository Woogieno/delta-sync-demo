"""데모용 DB 두 개를 만든다.

원본(source)은 컷오버 직전 운영 DB, 대상(target)은 오래된 스냅샷을 복원한 DB를 흉내 낸다.
- users: UUID PK (id > 최대값 방식이 성립하지 않는 경우)
- events: 복합 PK
- orders: 원본에만 있는 컬럼(coupon)이 있어 스키마가 어긋남
- 대상에는 스냅샷 이후 원본에 쌓인 행이 없다.

실행: python examples/make_demo_data.py <출력 폴더>
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path


def uid(n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"demo-user-{n}"))


def build(out: Path, n_users=300, n_late=5) -> None:
    out.mkdir(parents=True, exist_ok=True)
    src_path, dst_path = out / "source.db", out / "target.db"
    for p in (src_path, dst_path):
        p.unlink(missing_ok=True)

    src, dst = sqlite3.connect(src_path), sqlite3.connect(dst_path)

    src.executescript(
        """
        CREATE TABLE users  (id TEXT PRIMARY KEY, name TEXT, created_at TEXT);
        CREATE TABLE events (user_id TEXT, seq INTEGER, kind TEXT, feature TEXT,
                             PRIMARY KEY (user_id, seq));
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id TEXT, amount INTEGER, coupon TEXT);
        CREATE TABLE notes  (text TEXT);  -- PK 없음: 건너뛰는 경우를 보여 준다
        """
    )
    # 대상은 스키마가 어긋나 있다: events.feature, orders.coupon 칸이 없다.
    dst.executescript(
        """
        CREATE TABLE users  (id TEXT PRIMARY KEY, name TEXT, created_at TEXT, plan TEXT);
        CREATE TABLE events (user_id TEXT, seq INTEGER, kind TEXT, PRIMARY KEY (user_id, seq));
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id TEXT, amount INTEGER);
        CREATE TABLE notes  (text TEXT);
        """
    )

    total = n_users + n_late
    for n in range(total):
        row = (uid(n), f"user{n}", f"2026-09-{1 + n % 28:02d}")
        src.execute("INSERT INTO users VALUES (?,?,?)", row)
        if n < n_users:  # 스냅샷에는 앞쪽 사용자만 있다
            dst.execute("INSERT INTO users VALUES (?,?,?,?)", (*row, "free"))
        for seq in range(2):
            src.execute("INSERT INTO events VALUES (?,?,?,?)", (uid(n), seq, "login", "f1"))
            if n < n_users:
                dst.execute("INSERT INTO events VALUES (?,?,?)", (uid(n), seq, "login"))
        src.execute("INSERT INTO orders VALUES (?,?,?,?)", (n, uid(n), 1000 + n, None))
        if n < n_users:
            dst.execute("INSERT INTO orders VALUES (?,?,?)", (n, uid(n), 1000 + n))
    src.execute("INSERT INTO notes VALUES ('no pk')")
    dst.execute("INSERT INTO notes VALUES ('no pk')")
    src.commit(), dst.commit()
    src.close(), dst.close()
    print(f"생성: {src_path}, {dst_path}")


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "demo_data"))
