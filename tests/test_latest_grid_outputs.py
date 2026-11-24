from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
grid = importlib.import_module("live_grid_simulator_current改")
from latest_grid_outputs import refresh_latest_outputs


class RatioOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.template = self.root / "现货篮子指令参考模板.xlsx"
        shutil.copy2(ROOT / self.template.name, self.template)
        self.positions = pd.DataFrame([dict(product=grid.SIM_PRODUCT_NAME, unit=grid.SIM_UNIT_NAME, code="00700.HK", current_pos=.03)])

    def tearDown(self):
        self.temporary.cleanup()

    def orders(self, targets=(.05, .06, .045)):
        return pd.DataFrame([
            dict(product=grid.SIM_PRODUCT_NAME, unit=grid.SIM_UNIT_NAME, code="00700.HK", name="腾讯控股", side=side, target_pos=target, price=price, sim_first_fill_seq=i)
            for i, (target, side, price) in enumerate(zip(targets, ("buy", "buy", "sell"), (400., 390., 410.)))
        ])

    def rows(self, path):
        workbook = load_workbook(path, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            self.assertEqual([sheet.cell(1, c).value for c in range(1, 10)], grid.BASKET_TEMPLATE_HEADERS)
            return [list(row[:9]) for row in sheet.iter_rows(min_row=2, values_only=True) if row[0] is not None]
        finally:
            workbook.close()

    def test_trade_percent_not_target_percent_and_no_nav_conversion(self):
        path = self.root / "orders.xlsx"
        grid.export_basket_reference_template(self.orders(), self.positions, float("nan"), self.template, path)
        rows = self.rows(path)
        self.assertEqual([row[8] for row in rows], [2., 1., 1.5])
        self.assertEqual([row[3] for row in rows], ["1-买入", "1-买入", "2-卖出"])
        self.assertTrue(all(row[6] is None and row[7] is None for row in rows))
        self.assertTrue(all(row[4] == "f-指定价" for row in rows))
        grid.export_basket_reference_template(self.orders(), self.positions, 1., self.template, self.root / "other_nav.xlsx")
        self.assertEqual(rows, self.rows(self.root / "other_nav.xlsx"))

    def test_partial_fill_delta_and_zero_trade(self):
        orders = self.orders((.034, .034, .032))
        path = self.root / "partial.xlsx"
        grid.export_basket_reference_template(orders, self.positions, 0., self.template, path)
        self.assertEqual([row[8] for row in self.rows(path)], [.4, .2])

    def test_wrong_side_fails(self):
        orders = self.orders().iloc[:1].copy()
        orders.loc[:, "side"] = "sell"
        with self.assertRaisesRegex(ValueError, "目标仓位增加"):
            grid.export_basket_reference_template(orders, self.positions, 0., self.template, self.root / "bad.xlsx")

    def create_day(self, token, orders):
        folder = self.root / "最新输出文件" / "聚砾1号" / f"网格{token}"
        folder.mkdir(parents=True, exist_ok=True)
        grid.save_excel_report(folder / f"网格{token}_复核报告.xlsx", {"current_positions": self.positions, "run_meta": pd.DataFrame([{"execution_policy": grid.POLICY_VERSION, "simulation_product": grid.SIM_PRODUCT_NAME}])})
        grid.save_excel_report(folder / f"网格{token}_昨日分钟流动性模拟.xlsx", {"可模拟成交网格": orders})
        marker = folder / f"网格{token}_基金经理格式_当前全部网格.xlsx"
        shutil.copy2(self.template, marker)
        return folder

    def test_summary_start_date_daily_baseline_empty_market_and_repeat_overwrite(self):
        # 每天都从3%起步；跨日不能从昨日末仓位继续相减，也不能抵销交易。
        self.create_day("20261012", self.orders())
        self.create_day("20261013", self.orders())
        self.create_day("20261014", self.orders())
        first = refresh_latest_outputs(self.root, engine=grid)
        self.assertEqual(first["counts"], {"A股": 0, "港股": 6})
        expected = self.rows(first["paths"]["港股"])
        self.assertEqual([row[8] for row in expected], [2., 1., 1.5, 2., 1., 1.5])
        self.assertEqual(self.rows(first["paths"]["A股"]), [])
        self.assertNotIn("20261012", [day["date"] for day in first["days"]])
        self.assertTrue((self.root / "最新输出文件/聚砾1号/网格20261013/网格20261013_基金经理格式_当前全部网格.xlsx").exists())
        second = refresh_latest_outputs(self.root, engine=grid)
        self.assertEqual(second["counts"], first["counts"])
        self.assertEqual(self.rows(second["paths"]["港股"]), expected)
        provenance = pd.read_csv(self.root / "最新输出文件/聚砾1号/汇总指令/汇总来源.csv")
        self.assertEqual(provenance["业务日期"].tolist(), ["2026-10-13"] * 3 + ["2026-10-14"] * 3)
        # 显式本次运行可替换同日旧结果，汇总须缩短并清除旧尾行。
        folder = self.create_day("20261014", self.orders().iloc[:1])
        third = refresh_latest_outputs(self.root, current_output_dir=folder, engine=grid)
        self.assertEqual(third["counts"]["港股"], 4)
        self.assertEqual(len(self.rows(third["paths"]["港股"])), 4)

    def test_empty_day_has_no_phantom_order(self):
        self.create_day("20261013", pd.DataFrame())
        result = refresh_latest_outputs(self.root, engine=grid)
        self.assertEqual(result["counts"], {"A股": 0, "港股": 0})
        self.assertEqual(self.rows(result["paths"]["港股"]), [])

    def test_old_policy_does_not_enter_new_summary(self):
        self.create_day("20261013", self.orders())
        old = self.create_day("20261014", self.orders())
        grid.save_excel_report(old / "网格20261014_复核报告.xlsx", {"current_positions": self.positions, "run_meta": pd.DataFrame([{"execution_policy": "old"}])})
        result = refresh_latest_outputs(self.root, engine=grid)
        self.assertEqual(result["counts"]["港股"], 3)
        self.assertEqual(result["excluded_dates"], ["20261014"])


if __name__ == "__main__":
    unittest.main()
