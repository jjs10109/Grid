# -*- coding: utf-8 -*-
"""从每日原始模拟结果重建比例指令，再按日期覆盖更新汇总；不重新计算策略。"""
from __future__ import annotations

import argparse
import importlib
import re
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook


def refresh_latest_outputs(
    project_root: str | Path,
    start_date: str = "2026-10-13",
    current_output_dir: str | Path | None = None,
    engine: Any = None,
    basket_template: str | Path | None = None,
) -> dict[str, Any]:
    """兼容旧产物；同日仅选一份报告，不累计追加，不将回放结果混入汇总。"""
    root = Path(project_root).resolve()
    start = pd.to_datetime(start_date, errors="raise").normalize()
    latest = root / "最新输出文件"
    template = Path(basket_template) if basket_template else root / "现货篮子指令参考模板.xlsx"
    if not template.is_absolute():
        template = root / template
    engine = engine or importlib.import_module("live_grid_simulator_current改")
    current = Path(current_output_dir).resolve() if current_output_dir else None
    reports: dict[str, Path] = {}
    scores: dict[str, tuple[int, int, int]] = {}
    sources = [root / "outputs_live_grid改", latest]
    candidates = [p for parent in sources if parent.exists() for p in parent.glob("网格????????/网格????????_复核报告.xlsx")]
    if current and current.exists():
        candidates.extend(current.glob("网格????????_复核报告.xlsx"))
    for report in candidates:
        match = re.fullmatch(r"网格(20\d{6})_复核报告\.xlsx", report.name)
        if not match:
            continue
        token = match.group(1)
        if pd.to_datetime(token, format="%Y%m%d") < start:
            continue
        # 显式本次运行优先；否则取较新的报告，复制件同时间时选最新输出。
        score = (int(current is not None and report.parent.resolve() == current), report.stat().st_mtime_ns, int(latest in report.parents))
        if token not in scores or score > scores[token]:
            scores[token] = score
            reports[token] = report
    if not reports:
        raise FileNotFoundError(f"没有找到 {start.date()} 起的正常模式复核报告，未覆盖汇总。")

    from manager_execution_policy import POLICY_VERSION
    compatible = {}
    excluded_dates = []
    for token, report in reports.items():
        try:
            meta = pd.read_excel(report, sheet_name="run_meta")
            valid = not meta.empty and meta.iloc[0].get("execution_policy") == POLICY_VERSION
        except ValueError:
            valid = False
        if valid:
            compatible[token] = report
        else:
            excluded_dates.append(token)
    reports = compatible
    if not reports:
        raise ValueError("现有历史文件尚未按经理档位手工/90%规则重建，未更新汇总；请先重跑正常网格。")

    combined: dict[str, list[list[Any]]] = {"A股": [], "港股": []}
    provenance = []
    days = []
    for token, report in sorted(reports.items()):
        prefix = f"网格{token}"
        source = report.parent
        simulation_file = source / f"{prefix}_昨日分钟流动性模拟.xlsx"
        if not simulation_file.exists():
            raise FileNotFoundError(f"缺少原始模拟成交报告：{simulation_file}；不能从旧固定数量文件反推比例。")
        positions = pd.read_excel(report, sheet_name="current_positions", dtype={"code": str})
        orders = pd.read_excel(simulation_file, sheet_name="可模拟成交网格", dtype={"code": str})
        if not orders.empty:
            required = {"product", "unit", "code", "side", "target_pos", "price"}
            if not required.issubset(orders.columns):
                raise ValueError(f"{simulation_file} 缺少列：{sorted(required - set(orders.columns))}")
        destination = latest / prefix
        destination.mkdir(parents=True, exist_ok=True)
        # 其余七份文件原样保留；两份模拟导入文件从原始目标仓位重建。
        for item in source.glob(f"{prefix}_*.xlsx"):
            if item.name.endswith("_昨日分钟模拟成交_可导入.xlsx"):
                continue
            target = destination / item.name
            if item.resolve() != target.resolve():
                shutil.copy2(item, target)

        previous_channels = engine.HK_CHANNEL_MAP.copy()
        old_hk = source / f"{prefix}_港股_昨日分钟模拟成交_可导入.xlsx"
        try:
            # 沿用该日已记录的港股交易通道，不用其他日期的通道覆盖。
            if old_hk.exists():
                channel_book = load_workbook(old_hk, read_only=True, data_only=True)
                try:
                    for values in channel_book.worksheets[0].iter_rows(min_row=2, values_only=True):
                        if values[0] is not None and values[2] is not None:
                            code = engine.canonical_code(values[0], market="港股")
                            engine.HK_CHANNEL_MAP[code] = str(values[2])
                finally:
                    channel_book.close()
            a_orders, hk_orders = engine.split_orders_by_market(orders)
            day_counts = {}
            for market, market_orders in [("A股", a_orders), ("港股", hk_orders)]:
                target = destination / f"{prefix}_{market}_昨日分钟模拟成交_可导入.xlsx"
                # 导出比例无需净资产，内部分钟模拟仍使用既有的净资产口径。
                engine.export_basket_reference_template(market_orders, positions, float("nan"), template, target)
                workbook = load_workbook(target, read_only=True, data_only=True)
                try:
                    rows = [list(row[:9]) for row in workbook.worksheets[0].iter_rows(min_row=2, values_only=True) if row[0] is not None]
                finally:
                    workbook.close()
                day_counts[market] = len(rows)
                for source_row, row in enumerate(rows, start=2):
                    if row[6] is not None or row[7] is not None or row[8] is None or row[8] <= 0:
                        raise ValueError(f"{target}:{source_row} 不是有效的按比例指令。")
                    summary_row = len(combined[market]) + 2
                    combined[market].append(row)
                    provenance.append({
                        "市场": market, "业务日期": pd.to_datetime(token, format="%Y%m%d").strftime("%Y-%m-%d"),
                        "汇总Excel行号": summary_row, "每日文件": str(target.relative_to(latest)),
                        "每日Excel行号": source_row, "证券代码": row[0], "委托方向": row[3], "净值比例(%)": row[8],
                    })
            days.append({"date": token, **day_counts})
        finally:
            engine.HK_CHANNEL_MAP.clear()
            engine.HK_CHANNEL_MAP.update(previous_channels)

    summary_dir = latest / "汇总指令"
    summary_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for market, rows in combined.items():
        # 固定文件名，每次从每日结果重建，不向旧汇总追加。
        path = summary_dir / f"{market}_汇总指令.xlsx"
        engine.write_basket_reference_rows(rows, template, path)
        paths[market] = path
    columns = ["市场", "业务日期", "汇总Excel行号", "每日文件", "每日Excel行号", "证券代码", "委托方向", "净值比例(%)"]
    engine.save_csv_atomic(pd.DataFrame(provenance, columns=columns), summary_dir / "汇总来源.csv")
    most_recent_manual = latest / f"网格{max(reports)}" / f"网格{max(reports)}_手工下单文件.xlsx"
    if most_recent_manual.exists():
        shutil.copy2(most_recent_manual, latest / "手工下单文件.xlsx")
    if excluded_dates:
        print(f"以下旧规则日期未混入最新汇总，需重建：{', '.join(sorted(excluded_dates))}")
    return {"folder": latest, "start_date": start.strftime("%Y-%m-%d"), "end_date": max(reports), "days": days, "paths": paths, "excluded_dates": sorted(excluded_dates), "counts": {market: len(rows) for market, rows in combined.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description="重建10月13日起的按比例指令，并覆盖最新汇总；无需Wind。")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--start-date", default="2026-10-13")
    args = parser.parse_args()
    result = refresh_latest_outputs(args.project_root, args.start_date)
    print(f"已覆盖更新：{result['folder']}，共 {len(result['days'])} 个业务日")
    for market, path in result["paths"].items():
        print(f"{market}：{result['counts'][market]} 条 -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
