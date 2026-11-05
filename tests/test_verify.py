"""검증기 판별력 테스트 (결함 주입).

통과하는 경우만 확인하면 검증기가 항상 통과를 내도 알 수 없다.
그래서 대상 DB를 일부러 망가뜨려 검증기가 실제로 실패를 내는지 확인한다.
"""
import io
import json
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import fresh_demo

from deltasync import baseline as bl
from deltasync import cli
from deltasync.db import connect
from deltasync.delta import apply_plans, plan_all


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.src, self.dst, self.out = fresh_demo()
        self.addCleanup(self.tmp.cleanup)
        self.base_path = self.out / "baseline.json"
        s = connect(self.src, True)
        bl.write_baseline(s, self.base_path)       # 작업 직전 원본 기준선
        d = connect(self.dst)
        apply_plans(s, d, plan_all(s, d))            # 정상 델타 반영
        s.close(), d.close()

    def verify(self, strict=False):
        conn = connect(self.dst, True)
        ok, results = bl.verify(conn, bl.load_baseline(self.base_path), strict=strict)
        conn.close()
        return ok, results

    def mutate(self, sql):
        c = sqlite3.connect(self.dst)
        c.executescript(sql)
        c.commit(), c.close()

    # --- 정상 경로 ---
    def test_0_clean_target_passes(self):
        ok, results = self.verify()
        self.assertTrue(ok)
        # 대상에 칸이 없는 컬럼은 실패가 아니라 참고로 알린다.
        info = {r.table: r.baseline_only_cols for r in results}
        self.assertEqual(info["events"], ["feature"])

    # --- 결함 주입 6종: 모두 실패를 내야 한다 ---
    def test_1_deleted_row_is_detected(self):
        self.mutate("DELETE FROM orders WHERE id = 7;")
        ok, results = self.verify()
        self.assertFalse(ok)
        self.assertEqual(next(r for r in results if r.table == "orders").missing_rows, ["[7]"])

    def test_2_changed_value_is_detected(self):
        self.mutate("UPDATE orders SET amount = amount + 1 WHERE id = 7;")
        ok, results = self.verify()
        self.assertFalse(ok)
        changed = next(r for r in results if r.table == "orders").changed_rows
        self.assertEqual(changed, [("[7]", ["amount"])])

    def test_3_dropped_table_is_detected(self):
        self.mutate("DROP TABLE events;")
        ok, results = self.verify()
        self.assertFalse(ok)
        self.assertTrue(next(r for r in results if r.table == "events").missing_table)

    def test_4_extra_row_fails_only_in_strict_mode(self):
        self.mutate("INSERT INTO orders VALUES (9999, 'x', 1);")
        self.assertTrue(self.verify(strict=False)[0])   # 컷오버 뒤 정상 추가일 수 있음
        self.assertFalse(self.verify(strict=True)[0])

    def test_5_missing_baseline_fails_instead_of_passing(self):
        self.base_path.unlink()
        with self.assertRaises(bl.BaselineError):
            bl.load_baseline(self.base_path)
        with redirect_stderr(io.StringIO()):
            code = cli.main(["verify", "--db", str(self.dst), "--baseline", str(self.base_path)])
        self.assertEqual(code, 2)

    def test_6_corrupt_baseline_fails(self):
        self.base_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(bl.BaselineError):
            bl.load_baseline(self.base_path)
        self.base_path.write_text(json.dumps({"version": 999, "tables": {}}), encoding="utf-8")
        with self.assertRaises(bl.BaselineError):
            bl.load_baseline(self.base_path)

    # --- CLI 종료 코드 ---
    def test_cli_exit_codes(self):
        with redirect_stdout(io.StringIO()):
            ok = cli.main(["verify", "--db", str(self.dst), "--baseline", str(self.base_path)])
            self.mutate("DELETE FROM orders WHERE id = 7;")
            bad = cli.main(["verify", "--db", str(self.dst), "--baseline", str(self.base_path)])
        self.assertEqual((ok, bad), (0, 1))


if __name__ == "__main__":
    unittest.main()
