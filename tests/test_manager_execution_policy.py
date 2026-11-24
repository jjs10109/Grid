import importlib
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manager_execution_policy import apply_manager_execution_policy, manual_order_table, POLICY_VERSION


class ManagerPolicyTests(unittest.TestCase):
    def test_buy_last_inner_reaches_90_percent_and_all_original_prices_removed(self):
        levels = [{"price": 11., "target_pos": .01}, {"price": 10., "target_pos": .02}]
        table = pd.DataFrame({"price": [11., 10.8, 10.6, 10.4, 10.2, 10.], "target_pos": [.01, .012, .014, .016, .018, .02], "is_manager_level": [True, False, False, False, False, True]})
        result = apply_manager_execution_policy(table, levels, "buy")
        self.assertEqual(result.price.tolist(), [10.8, 10.6, 10.4, 10.2])
        self.assertAlmostEqual(result.target_pos.iloc[-1], .019)
        self.assertTrue((result.automatic_completion <= .9).all())
        self.assertFalse(result.is_manager_level.any())
        self.assertTrue(result.execution_policy.eq(POLICY_VERSION).all())

    def test_sell_reserves_ten_percent_and_no_terminal_price(self):
        levels = [{"price": 10., "target_pos": .02}, {"price": 11., "target_pos": .01}]
        table = pd.DataFrame({"price": [10., 10.2, 10.4, 10.6, 10.8, 11.], "target_pos": [.02, .018, .016, .014, .012, .01]})
        result = apply_manager_execution_policy(table, levels, "sell")
        self.assertAlmostEqual(result.target_pos.iloc[-1], .011)
        self.assertEqual(len(result), 4)

    def test_single_level_and_no_inner_levels_are_manual_only(self):
        table = pd.DataFrame({"price": [10., 11.], "target_pos": [.02, .01]})
        self.assertTrue(apply_manager_execution_policy(table, [{"price": 10., "target_pos": .02}], "buy").empty)
        self.assertTrue(apply_manager_execution_policy(table, [{"price": 11., "target_pos": .01}, {"price": 10., "target_pos": .02}], "buy").empty)

    def test_each_interval_and_synthetic_midpoint(self):
        levels = [{"price": 12., "target_pos": .01}, {"price": 11., "target_pos": .02}, {"price": 10., "target_pos": .03}]
        table = pd.DataFrame({"price": [12., 11.5, 11., 10.5, 10.], "target_pos": [.01, .015, .02, .025, .03], "is_manager_level": [True] * 5})
        result = apply_manager_execution_policy(table, levels, "buy")
        self.assertEqual(result.price.tolist(), [11.5, 10.5])
        self.assertAlmostEqual(result.target_pos.iloc[0], .019)
        self.assertAlmostEqual(result.target_pos.iloc[1], .029)

    def test_manual_file_separates_actual_remaining_from_planned_remaining(self):
        engine = importlib.import_module("live_grid_simulator_current改")
        accounts = pd.DataFrame([{"product": engine.SIM_PRODUCT_NAME, "unit": engine.SIM_UNIT_NAME}])
        positions = pd.DataFrame([{"product": engine.SIM_PRODUCT_NAME, "unit": engine.SIM_UNIT_NAME, "code": "600000.SH", "name": "示例", "current_pos": .01}])
        snapshot = pd.DataFrame([{"code": "600000.SH", "name": "示例", "buy_levels": [{"price": 11., "target_pos": .01}, {"price": 10., "target_pos": .02}], "sell_levels": []}])
        active = pd.DataFrame([{"product": engine.SIM_PRODUCT_NAME, "unit": engine.SIM_UNIT_NAME, "code": "600000.SH", "side": "buy", "price": 10.2, "target_pos": .019}])
        rows = manual_order_table(snapshot, accounts, positions, active, "2026-11-19", engine)
        last = rows.iloc[-1]
        self.assertAlmostEqual(last["当前真实仓位"], .01)
        self.assertAlmostEqual(last["尚需执行仓位"], .01)
        self.assertAlmostEqual(last["自动档全部成交后预计仓位"], .019)
        self.assertAlmostEqual(last["预计自动成交后剩余仓位"], .001)

    def test_order_builder_removes_anchors_before_all_export_and_simulation_paths(self):
        engine = importlib.import_module("live_grid_simulator_current改")
        levels = [{"price": 11., "target_pos": .01}, {"price": 10., "target_pos": .02}]
        table = pd.DataFrame({"price": [11., 10.8, 10.6, 10.4, 10.2, 10.], "target_pos": [.01, .012, .014, .016, .018, .02]})
        accounts = pd.DataFrame([{"product": engine.SIM_PRODUCT_NAME, "unit": engine.SIM_UNIT_NAME}])
        positions = pd.DataFrame([{"product": engine.SIM_PRODUCT_NAME, "unit": engine.SIM_UNIT_NAME, "code": "600000.SH", "name": "示例", "current_pos": .01}])
        instruction = pd.Series({"code": "600000.SH", "name": "示例", "buy_levels": levels, "sell_levels": [], "instruction_hash": "test"})
        args = SimpleNamespace(lookback_years=3, price_adj="F", split_method="linear", buy_gamma=1., sell_gamma=1., auto_split_buy_anchors=False, min_order_weight=0., force=False)
        with patch.object(engine, "fetch_daily_profile_cached", return_value=pd.DataFrame()), patch.object(engine, "evaluate_manager_grid_splitter", return_value={"buy_table": table}):
            active, new, checks = engine.build_orders_for_code(instruction, accounts, positions, "2026-11-20", .3, {"600000.SH": 10.5}, {}, args, set(), "2026-11-19")
        self.assertEqual(active.price.tolist(), [10.8, 10.6, 10.4, 10.2])
        self.assertAlmostEqual(active.order_weight.sum(), .009)
        self.assertEqual(len(active), len(new))
        self.assertFalse(active.is_manager_level.any())
        self.assertTrue(checks.pass_position_check.all())
        with patch.object(engine, "fetch_daily_profile_cached", return_value=pd.DataFrame()), patch.object(engine, "evaluate_manager_grid_splitter", return_value={"buy_table": table}):
            partial, _, _ = engine.build_orders_for_code(instruction, accounts, positions, "2026-11-20", .02, {"600000.SH": 10.5}, {}, args, set(), "2026-11-19")
        self.assertEqual(partial.price.tolist(), [10.6, 10.4])
        self.assertLess(partial.automatic_completion.max(), .9)


if __name__ == "__main__":
    unittest.main()
