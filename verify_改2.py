# -*- coding: utf-8 -*-
"""改2 双产品全业务日综合验证（只读，不重算）。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PRODUCTS = {
    "净值线3号": "巨杉网格化",
    "聚砾1号": "聚砾1号",
}
TOKENS = [
    "20261013", "20261014", "20261015", "20261016", "20261019",
    "20261020", "20261021", "20261022", "20261023", "20261026",
    "20261027", "20261028", "20261029", "20261030", "20261102",
    "20261103", "20261104", "20261105", "20261106", "20261109",
    "20261110", "20261111", "20261112", "20261113", "20261116",
    "20261117", "20261118", "20261119", "20261120", "20261123",
    "20261124",
]

violations: list[str] = []
stats = {p: {"active": 0, "fills": 0} for p in PRODUCTS}


def note(condition: bool, message: str) -> None:
    if not condition:
        violations.append(message)


for folder_product, internal_product in PRODUCTS.items():
    for token in TOKENS:
        out_dir = ROOT / "outputs_live_grid改2" / folder_product / f"网格{token}"
        state_dir = ROOT / "state改2" / folder_product / f"state{token}"
        prefix = f"网格{token}"

        # 1. 文件完整性
        xlsx = list(out_dir.glob("*.xlsx"))
        note(len(xlsx) == 11, f"{folder_product}/{token}: 输出xlsx数={len(xlsx)}（期望11）")
        state_files = sorted(p.name for p in state_dir.iterdir())
        expect_state = sorted([
            "active_grid_master.xlsx", "current_positions.csv",
            "delta_ledger.xlsx", "instruction_snapshot.csv",
        ])
        note(state_files == expect_state, f"{folder_product}/{token}: state文件={state_files}")

        report = out_dir / f"{prefix}_复核报告.xlsx"
        errors = pd.read_excel(report, sheet_name="errors")
        note(errors.shape[0] == 0, f"{folder_product}/{token}: errors表有{errors.shape[0]}行")

        # 以实际导出的 active_grid_master（已应用单向/取整/带宽/去重）作为逻辑检查对象。
        active = pd.read_excel(state_dir / "active_grid_master.xlsx")

        # 产品隔离
        note(set(active["product"].dropna().unique()) <= {internal_product},
             f"{folder_product}/{token}: 出现非本产品 {set(active['product'].unique())}")

        # 3. 无双向：每个导出代码只允许一个方向
        side_sets = active.groupby("code")["side"].nunique()
        both = list(side_sets[side_sets > 1].index)
        note(not both, f"{folder_product}/{token}: 存在双向代码 {both}")

        # 4. 价格两位小数
        bad_round = active[~np.isclose(active["price"], active["price"].round(2), atol=1e-9)]
        note(bad_round.empty, f"{folder_product}/{token}: 超2位价格 {bad_round['price'].tolist()}")

        # 5. 带宽：非经理档必须在 band 内
        non_mgr = active[active["is_manager_level"] == False]  # noqa: E712
        out_band = non_mgr[(non_mgr["price"] < non_mgr["band_low"] - 1e-9)
                           | (non_mgr["price"] > non_mgr["band_high"] + 1e-9)]
        note(out_band.empty, f"{folder_product}/{token}: 超带档位 {out_band[['code','price']].to_dict('records')}")

        stats[folder_product]["active"] += len(active)

        # 现货篮子：有效行价格2位、价格类型 f-指定价；过滤空标记行
        for market in ("A股", "港股"):
            basket = pd.read_excel(out_dir / f"{prefix}_{market}_当日网格_现货篮子.xlsx")
            code_col = basket.columns[0]
            valid = basket[basket[code_col].notna()].copy()
            if len(valid):
                price_col = [c for c in valid.columns if "价格" in str(c) and "类型" not in str(c)]
                type_col = [c for c in valid.columns if "价格类型" in str(c)]
                if type_col:
                    note(set(valid[type_col[0]].dropna().unique()) <= {"f-指定价"},
                         f"{folder_product}/{token}/{market}: 价格类型异常")
                if price_col:
                    px = pd.to_numeric(valid[price_col[0]], errors="coerce")
                    note(np.isclose(px, px.round(2), atol=1e-9).all(),
                         f"{folder_product}/{token}/{market}: 篮子价格非2位")

    # 汇总条数核对
    summary_dir = ROOT / "outputs_live_grid改2" / folder_product / "汇总指令"
    for market in ("A股", "港股"):
        summ = pd.read_excel(summary_dir / f"{market}_汇总指令.xlsx")
        stats[folder_product][f"summary_{market}"] = len(summ)

print("=" * 60)
for product, data in stats.items():
    print(f"{product}: 累计有效档位={data['active']}；"
          f"汇总 A股={data.get('summary_A股')} 港股={data.get('summary_港股')}")
print("=" * 60)
if violations:
    print(f"验证未通过，共 {len(violations)} 项：")
    for v in violations:
        print("  - " + v)
    raise SystemExit(1)
print("全部检查通过：文件完整、errors=0、无双向、无超2位、无超带、产品隔离正确。")
