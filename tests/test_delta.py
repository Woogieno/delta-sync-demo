import io
import sqlite3
import unittest
from contextlib import redirect_stdout

from helpers import fresh_demo

from deltasync import cli
from deltasync.db import columns, connect
from deltasync.delta import apply_plans, plan_all


def counts(path):
    c = sqlite3.connect(path)
    r = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
         for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close()
    return r


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.src, self.dst, self.out = fresh_demo(n_users=300, n_late=5)
        self.addCleanup(self.tmp.cleanup)

    def plans(self):
        s, d = connect(self.src, True), connect(self.dst, True)
        by = {p.table: p for p in plan_all(s, d)}
        s.close(), d.close()
        return by

    def test_plan_finds_only_missing_rows(self):
        by = self.plans()
        self.assertEqual(by["users"].to_insert, 5)      # UUID PK
        self.assertEqual(by["events"].to_insert, 10)    # 복합 PK (5명 x 2)
        self.assertEqual(by["orders"].to_insert, 5)

    def test_table_without_pk_is_skipped_not_guessed(self):
        by = self.plans()
        self.assertEqual(by["notes"].status, "skipped")

    def test_schema_drift_uses_column_intersection(self):
        by = self.plans()
        self.assertEqual(by["events"].source_only_cols, ["feature"])
        self.assertEqual(by["orders"].source_only_cols, ["coupon"])
        self.assertEqual(by["users"].target_only_cols, ["plan"])

    def test_apply_is_idempotent(self):
        s, d = connect(self.src, True), connect(self.dst)
        first = apply_plans(s, d, plan_all(s, d))
        self.assertEqual(sum(first.values()), 20)
        second = apply_plans(s, d, plan_all(s, d))
        self.assertEqual(sum(second.values()), 0)
        s.close(), d.close()

    def test_apply_keeps_target_schema(self):
        s, d = connect(self.src, True), connect(self.dst)
        apply_plans(s, d, plan_all(s, d))
        self.assertNotIn("feature", columns(d, "events"))
        row = d.execute("SELECT plan FROM users ORDER BY name DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)  # 대상 전용 컬럼은 NULL/기본값으로 남는다
        s.close(), d.close()

    def test_dry_run_does_not_change_target(self):
        before = counts(self.dst)
        with redirect_stdout(io.StringIO()):
            code = cli.main(["apply", "--source", str(self.src), "--target", str(self.dst)])
        self.assertEqual(code, 0)
        self.assertEqual(counts(self.dst), before)

    def test_execute_via_cli_then_rerun_adds_nothing(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["apply", "--source", str(self.src), "--target", str(self.dst), "--execute"]), 0)
            after_first = counts(self.dst)
            self.assertEqual(cli.main(["apply", "--source", str(self.src), "--target", str(self.dst), "--execute"]), 0)
        self.assertEqual(counts(self.dst), after_first)

    def test_reverse_plan_sees_rows_written_only_to_target(self):
        # 컷오버 뒤 대상에만 쌓인 행: 롤백 시 원본에 없으면 유실된다.
        c = sqlite3.connect(self.dst)
        c.execute("INSERT INTO orders VALUES (9999, 'new-user', 1)")
        c.commit(), c.close()
        d, s = connect(self.dst, True), connect(self.src, True)
        rev = {p.table: p for p in plan_all(d, s)}
        self.assertEqual(rev["orders"].to_insert, 1)
        s.close(), d.close()

    def test_apply_is_atomic(self):
        # 대상에 삽입을 막는 제약을 걸어 중간 실패를 만든다.
        c = sqlite3.connect(self.dst)
        c.execute(
            "CREATE TRIGGER block BEFORE INSERT ON orders WHEN NEW.amount > 1300 "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
        c.commit(), c.close()
        before = counts(self.dst)
        s, d = connect(self.src, True), connect(self.dst)
        with self.assertRaises(sqlite3.DatabaseError):
            apply_plans(s, d, plan_all(s, d))
        s.close(), d.close()
        self.assertEqual(counts(self.dst), before)  # events 등 앞선 테이블도 되돌려진다


if __name__ == "__main__":
    unittest.main()
