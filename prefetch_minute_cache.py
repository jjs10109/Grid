# -*- coding: utf-8 -*-

"""分钟级行情（Wind WSI）持久化缓存预取脚本。

用法：
    # 先看计划，不连 Wind
    D:\\anaconda3\\python.exe prefetch_minute_cache.py --dry-run

    # 实际预取（默认 2026-10-09 ~ 2026-10-23 的全部工作日）
    D:\\anaconda3\\python.exe prefetch_minute_cache.py

    # 指定区间与指令文件
    D:\\anaconda3\\python.exe prefetch_minute_cache.py --start 2026-10-12 --end 2026-10-22

缓存位置：项目根 / minute_cache / {code}__bs1__{YYYYMMDD}.parquet
已存在非空缓存直接跳过，不重复消耗 WSI 配额。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_loader import (  # noqa: E402
    PROJECT_ROOT as DL_ROOT,
    configure_minute_cache,
    fetch_wind_intraday_minute,
    _minute_cache_path,
)
CODE_PATTERN = re.compile(r"([0-9]{4,6})\s*\.\s*(SZ|SH|BJ|HK)", re.IGNORECASE)


def norm_canon(value: str) -> str:
    """项目内部口径：A 股 6 位、港股 5 位（00941.HK）。"""
    s = str(value).strip().upper().replace(" ", "")
    m = re.match(r"^([0-9]{1,6})\.(SZ|SH|BJ|HK)$", s)
    if not m:
        return ""
    num, mkt = m.group(1), m.group(2)
    return f"{num.zfill(5 if mkt == 'HK' else 6)}.{mkt}"



def collect_codes(instruction_dir: Path) -> list[str]:
    from openpyxl import load_workbook

    codes: dict[str, set[str]] = {}
    for path in sorted(instruction_dir.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        try:
            wb = load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:
            print(f"[skip] {path.name}: {type(exc).__name__}")
            continue
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for value in row:
                        if not isinstance(value, str):
                            continue
                        for m in CODE_PATTERN.finditer(value.replace(" ", "")):
                            code = norm_canon(m.group(0))
                            if code:
                                codes.setdefault(code, set()).add(path.name)
        finally:
            wb.close()
    return sorted(codes)


def trading_days(start: str, end: str) -> list[str]:
    """工作日集合；A/H 股在 10 月无额外假期，周末由调用方也一并过滤。"""
    days = pd.bdate_range(start=pd.to_datetime(start), end=pd.to_datetime(end))
    return [d.strftime("%Y-%m-%d") for d in days]


def main() -> int:
    ap = argparse.ArgumentParser(description="预取分钟行情到本地持久化缓存")
    ap.add_argument("--start", default="2026-10-09", help="起始日期（含）")
    ap.add_argument("--end", default="2026-10-23", help="结束日期（含）")
    ap.add_argument("--instruction-dir", default=str(PROJECT_ROOT / "指令"))
    ap.add_argument("--codes", default="", help="手动指定代码，逗号分隔；不填则从指令文件抽取")
    ap.add_argument("--bar-size", type=int, default=1)
    ap.add_argument("--refresh", action="store_true", help="忽略已有缓存强制重拉")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个组合，用于小样本验证")
    args = ap.parse_args()

    if args.codes:
        codes = sorted({norm_canon(x) for x in re.split(r"[,，;；\s]+", args.codes) if x.strip()})
        codes = [c for c in codes if c]
        sources = {"manual": ""}
    else:
        inst_dir = Path(args.instruction_dir)
        if not inst_dir.exists():
            print(f"指令目录不存在: {inst_dir}")
            return 2
        codes = collect_codes(inst_dir)
        sources = {"dir": str(inst_dir)}

    days = trading_days(args.start, args.end)
    tasks = [(c, d) for c in codes for d in days]
    if args.limit:
        tasks = tasks[: args.limit]

    print(f"代码数={len(codes)} 交易日数={len(days)} 组合数={len(tasks)}")
    print(f"代码：{', '.join(codes)}")
    print(f"日期：{', '.join(days)}")

    if args.dry_run:
        existing = sum(
            1
            for c, d in tasks
            if (p := _minute_cache_path(c, args.bar_size, d)).exists() and p.stat().st_size > 0
        )
        print(f"已有非空缓存={existing} 待请求={len(tasks) - existing}")
        return 0

    configure_minute_cache(enabled=True, refresh=args.refresh)

    rows = []
    hits = fetched = empty = errors = 0
    for i, (code, day) in enumerate(tasks, 1):
        path = _minute_cache_path(code, args.bar_size, day)
        had_cache = path.exists() and path.stat().st_size > 0
        try:
            df = fetch_wind_intraday_minute(code, day, bar_size=args.bar_size, price_adj="")
            if had_cache and not args.refresh:
                status = "hit"
                hits += 1
            elif df.empty:
                status = "empty"
                empty += 1
            else:
                status = "fetch"
                fetched += 1
            bars = int(len(df))
            first = df["date"].min().strftime("%H:%M") if bars else ""
            last = df["date"].max().strftime("%H:%M") if bars else ""
        except Exception as exc:
            status = "error"
            bars = 0
            first = last = ""
            errors += 1
            df = pd.DataFrame()
            print(f"[error] {code} {day}: {type(exc).__name__}: {exc}")
        rows.append(
            {
                "code": code,
                "date": day,
                "status": status,
                "bars": bars,
                "first_bar": first,
                "last_bar": last,
                "cache_file": path.name,
                "bytes": path.stat().st_size if path.exists() else 0,
            }
        )
        if i % 20 == 0 or i == len(tasks):
            print(f"进度 {i}/{len(tasks)}  命中={hits} 新取={fetched} 无数据={empty} 异常={errors}")

    report_dir = Path(DL_ROOT) / "minute_cache"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "_prefetch_report.csv"
    pd.DataFrame(rows).to_csv(report_path, index=False, encoding="utf-8-sig")

    summary = {
        "start": args.start,
        "end": args.end,
        "codes": len(codes),
        "days": len(days),
        "tasks": len(tasks),
        "hit": hits,
        "fetched": fetched,
        "empty": empty,
        "error": errors,
        "report": str(report_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
