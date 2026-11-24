import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
m = importlib.import_module("live_grid_simulator_current改")
from snapshot_guard import validate_snapshot_date


class CurrentRulesTests(unittest.TestCase):
    def test_product_isolation(self):
        self.assertTrue(m.is_sim_product("聚砾1号"))
        self.assertTrue(m.is_sim_product("巨杉资产聚砾1号"))
        self.assertFalse(m.is_sim_product("净值线3号"))
        self.assertFalse(m.is_sim_product("巨杉网格化"))

    def test_board_bands(self):
        for code, band in [("600000.SH", .1), ("000001.SZ", .1), ("300174.SZ", .2), ("301035.SZ", .2), ("688146.SH", .2), ("920394.BJ", .3), ("00700.HK", .3)]:
            self.assertEqual(m.board_price_band(code), band)

    def test_nearest_side_and_ties(self):
        self.assertEqual(m.choose_single_side([{"side":"buy","price":9.8},{"side":"sell","price":10.5}],10)[0],"buy")
        self.assertEqual(m.choose_single_side([{"side":"buy","price":9.8},{"side":"sell","price":10.2}],10)[0],"sell")

    def test_snapshot_exact_date_and_product(self):
        df = pd.DataFrame({"产品":["净值线3号","聚砾1号"],"日期":["2026-11-24","2026-11-23"]})
        reader = lambda *a,**k: {"Sheet1":df}
        self.assertEqual(validate_snapshot_date("dummy", "2026-11-23",reader),"2026-11-23")
        for date in ["2026-11-22","2026-11-24"]:
            with self.assertRaisesRegex(ValueError,"不一致"):
                validate_snapshot_date("dummy",date,reader)

    def test_old_percentage_increment_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"停用"):
            m.incremental_oplus_trades(pd.DataFrame(),pd.DataFrame())

    def test_suppressed_side_does_not_poison_ledger(self):
        levels_buy=[{"price":10.,"target_pos":.03},{"price":9.,"target_pos":.04}]
        levels_sell=[{"price":11.,"target_pos":.01},{"price":12.,"target_pos":0.}]
        instruction=pd.Series({"code":"600000.SH","name":"示例","buy_levels":levels_buy,"sell_levels":levels_sell,"instruction_hash":"x"})
        tables={"buy_table":pd.DataFrame({"price":[10.,9.9,9.],"target_pos":[.03,.035,.04]}),"sell_table":pd.DataFrame({"price":[11.,11.1,12.],"target_pos":[.01,.005,0.]})}
        accounts=pd.DataFrame([{"product":m.SIM_PRODUCT_NAME,"unit":m.SIM_UNIT_NAME}])
        positions=pd.DataFrame([{"product":m.SIM_PRODUCT_NAME,"unit":m.SIM_UNIT_NAME,"code":"600000.SH","name":"示例","current_pos":.02}])
        args=SimpleNamespace(lookback_years=3,price_adj="F",split_method="linear",buy_gamma=1.,sell_gamma=1.,auto_split_buy_anchors=False,min_order_weight=0.,force=False,price_band_mode="fixed",price_round_digits=2,allow_same_day_opposite=False)
        keys=set()
        with patch.object(m,"fetch_daily_profile_cached",return_value=pd.DataFrame()),patch.object(m,"evaluate_manager_grid_splitter",return_value=tables):
            active,new,checks=m.build_orders_for_code(instruction,accounts,positions,"2026-11-25",.3,{"600000.SH":10.}, {},args,keys,"2026-11-24")
        self.assertEqual(set(active.side),{"buy"})
        self.assertEqual(keys,set(new.dedupe_key))
        self.assertTrue(checks.loc[checks.side=="sell","suppressed_by_one_side_rule"].all())
        self.assertFalse(checks.loc[checks.side=="sell","pass_position_check"].any())


if __name__ == "__main__":
    unittest.main()
