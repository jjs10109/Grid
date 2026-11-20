"""经理原始档位仅手工执行；中间网格每段最多推进90%的仓位差。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import Alignment

POLICY_VERSION = "manager_manual_intermediate_90_v1"
AUTO_COMPLETION = 0.90


def apply_manager_execution_policy(table: pd.DataFrame, manager_levels: list[dict], side: str) -> pd.DataFrame:
    """在价格带筛选之前处理整段；合成锚点不当作经理原始档位删除。"""
    if side not in {"buy", "sell"}:
        raise ValueError(f"不支持的方向：{side}")
    out = table.iloc[:0].copy()
    if table.empty or len(manager_levels) < 2:
        return out
    # 同价经理档保留最后一个目标，与既有拆档引擎一致。
    levels = {float(x["price"]): float(x["target_pos"]) for x in manager_levels}
    ordered = sorted(levels, reverse=(side == "buy"))
    pieces = []
    for index, (p0, p1) in enumerate(zip(ordered, ordered[1:]), start=1):
        w0, w1 = levels[p0], levels[p1]
        delta = w1 - w0
        if (side == "buy" and delta <= 1e-12) or (side == "sell" and delta >= -1e-12):
            continue
        # 两端原始经理价格均排除；仅调整真正位于两原始经理档之间的自动档。
        prices = pd.to_numeric(table["price"], errors="coerce")
        part = table[(prices > min(p0, p1) + 1e-8) & (prices < max(p0, p1) - 1e-8)].copy()
        if part.empty:
            continue
        part = part.sort_values("price", ascending=(side == "sell"), kind="stable")
        progress = ((pd.to_numeric(part["target_pos"], errors="raise") - w0) / delta).clip(0, 1).cummax()
        last = float(progress.iloc[-1])
        if last <= 1e-12:
            # 原曲线完全没有推进时，保持零推进，不能凭空造出交易。
            continue
        completion = (progress / last * AUTO_COMPLETION).clip(0, AUTO_COMPLETION)
        part["target_before_manual_policy"] = part["target_pos"]
        part["target_pos"] = w0 + delta * completion
        part["is_manager_level"] = False
        part["manual_policy_interval"] = index
        part["manager_start_price"] = p0
        part["manager_end_price"] = p1
        part["manager_start_target"] = w0
        part["manager_end_target"] = w1
        part["automatic_completion"] = completion
        part["execution_policy"] = POLICY_VERSION
        pieces.append(part)
    if not pieces:
        return out
    result = pd.concat(pieces, ignore_index=True).sort_values("price", ascending=(side == "sell"), kind="stable")
    result["add_or_reduce_pos"] = result["target_pos"].diff().fillna(0.)
    return result.reset_index(drop=True)


def manual_order_table(snapshot, accounts, positions, active_grid, business_date, engine):
    """真实当前仓位与预计自动执行结果分列；不把模拟成交当作真实持仓。"""
    columns = ["业务日期", "产品", "单元", "证券代码", "证券名称", "买卖方向", "原始经理指令", "经理档位价格",
               "经理目标仓位", "当前真实仓位", "尚需执行仓位", "自动档全部成交后预计仓位", "预计自动成交后剩余仓位", "状态"]
    rows = []
    for _, instruction in snapshot.iterrows():
        code = engine.canonical_code(instruction["code"])
        if engine.market_group(code) not in {"A", "HK"}:
            continue
        for _, account in accounts.iterrows():
            product, unit = account["product"], account["unit"]
            current, _ = engine.get_position(positions, product, unit, code)
            for side in ["buy", "sell"]:
                levels = engine.build_effective_levels(instruction.get(f"{side}_levels") or [], current, side)
                for level in levels:
                    price, target = float(level["price"]), float(level["target_pos"])
                    planned = float(current)
                    if active_grid is not None and not active_grid.empty:
                        eligible = active_grid[(active_grid["product"] == product) & (active_grid["unit"] == unit) &
                                               (active_grid["code"] == code) & (active_grid["side"] == side)]
                        eligible = eligible[eligible["price"] > price + 1e-8] if side == "buy" else eligible[eligible["price"] < price - 1e-8]
                        if not eligible.empty:
                            planned = float(eligible.sort_values("price", ascending=(side == "sell")).iloc[-1]["target_pos"])
                    direction = 1 if side == "buy" else -1
                    remaining = max(0., direction * (target - current))
                    after_remaining = max(0., direction * (target - planned))
                    rows.append([business_date, product, unit, code, instruction.get("name", ""),
                                 "买入" if side == "buy" else "卖出", level.get("raw_instruction") or engine._manager_instruction_text(side, price, target),
                                 price, target, current, remaining, planned, after_remaining,
                                 "已达到或超过目标" if remaining <= 1e-12 else "经理档位仅手工确认；不自动执行"])
    return pd.DataFrame(rows, columns=columns)


def export_manual_order_file(table, path, engine):
    """手工清单展示真实与预计仓位，长指令换行、比例保留四位小数。"""
    path = Path(path)
    engine.save_excel_report(path, {"手工下单文件": table})
    book = load_workbook(path)
    sheet = book.worksheets[0]
    for cell in sheet[1]:
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[cell.column_letter].width = 20
    sheet.row_dimensions[1].height = 44
    sheet.column_dimensions["G"].width = 60
    sheet.column_dimensions["N"].width = 42
    for row in sheet.iter_rows(min_row=2):
        row[6].alignment = Alignment(vertical="center", wrap_text=True)
        row[13].alignment = Alignment(vertical="center", wrap_text=True)
        for cell in row[8:13]:
            cell.number_format = "0.0000%"
    sheet.auto_filter.ref = sheet.dimensions
    temporary = path.with_name(path.stem + ".tmp.xlsx")
    try:
        book.save(temporary)
        book.close()
        temporary.replace(path)
    finally:
        book.close()
        if temporary.exists():
            temporary.unlink()
    return path
