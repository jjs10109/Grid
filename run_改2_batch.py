# -*- coding: utf-8 -*-
"""改2 全业务日双产品批量驱动。

对每个业务日 D，按改1旧口径：
  asof      = D 的下一自然日（脚本内部 derive_business_date 会把周末回落到 D）
  instruction = 指令/组合指令{D:MMDD}_剔除已成交.xlsx
  oplus       = oplus交易文件/oplus{prev:MMDD}.xlsx
  position    = 持仓数据/全球产品持仓查询_{prev:YYYYMMDD}.xls
  固定 --no-apply-oplus-trades --price-band 0.30
两个产品（净值线3号、聚砾1号）各自独立进程，输出/状态由改2默认布局按产品隔离。
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON = r"D:\anaconda3\python.exe"
SCRIPT = "live_grid_simulator_current改2.py"
PRODUCTS = ("净值线3号", "聚砾1号")

# 首项为锚点（1013 的前一交易日），其余为 31 个业务日。
CHAIN = [
    "20261012",
    "20261013", "20261014", "20261015", "20261016", "20261019",
    "20261020", "20261021", "20261022", "20261023", "20261026",
    "20261027", "20261028", "20261029", "20261030", "20261102",
    "20261103", "20261104", "20261105", "20261106", "20261109",
    "20261110", "20261111", "20261112", "20261113", "20261116",
    "20261117", "20261118", "20261119", "20261120", "20261123",
    "20261124",
]


def token_to_date(token: str) -> dt.date:
    return dt.datetime.strptime(token, "%Y%m%d").date()


def build_jobs() -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for i in range(1, len(CHAIN)):
        d_token = CHAIN[i]
        prev_token = CHAIN[i - 1]
        d = token_to_date(d_token)
        asof = d + dt.timedelta(days=1)
        jobs.append({
            "date": d_token,
            "asof": asof.strftime("%Y-%m-%d"),
            "instruction": str(PROJECT_ROOT / "指令" / f"组合指令{d_token[4:]}_剔除已成交.xlsx"),
            "oplus": str(PROJECT_ROOT / "oplus交易文件" / f"oplus{prev_token[4:]}.xlsx"),
            "position": str(PROJECT_ROOT / "持仓数据" / f"全球产品持仓查询_{prev_token}.xls"),
        })
    return jobs


def preflight(jobs: list[dict[str, str]]) -> int:
    missing: list[str] = []
    for job in jobs:
        for key in ("instruction", "oplus", "position"):
            if not Path(job[key]).exists():
                missing.append(f"{job['date']} {key}: {job[key]}")
    print(f"业务日数: {len(jobs)}；产品: {', '.join(PRODUCTS)}；总运行次数: {len(jobs) * len(PRODUCTS)}")
    if missing:
        print("缺失输入文件:")
        for item in missing:
            print("  " + item)
        return 1
    print("预检通过：全部 instruction/oplus/position 文件齐备。")
    return 0


def run(jobs: list[dict[str, str]]) -> int:
    failures: list[str] = []
    for index, job in enumerate(jobs, start=1):
        for product in PRODUCTS:
            cmd = [
                PYTHON, SCRIPT,
                "--product", product,
                "--asof-date", job["asof"],
                "--instruction-file", job["instruction"],
                "--oplus-file", job["oplus"],
                "--position-file", job["position"],
                "--no-apply-oplus-trades",
                "--price-band", "0.30",
            ]
            result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, encoding="utf-8")
            tag = f"[{index}/{len(jobs)}] D={job['date']} 产品={product}"
            if result.returncode != 0:
                failures.append(tag)
                print(tag, f"FAILED exit={result.returncode}")
                print(result.stderr[-3000:])
                print("中途失败，停止后续。")
                return 1
            # 抽取关键行做进度回显。
            active = [ln for ln in result.stdout.splitlines() if "当前 active grid" in ln]
            print(tag, "OK", (active[0].strip() if active else ""))
    if failures:
        print("失败:", failures)
        return 1
    print("全部业务日双产品运行完成，无失败。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true", help="只检查输入文件，不运行")
    parser.add_argument("--resume-from-date", default="", help="从指定YYYYMMDD业务日开始（含），用于失败后续跑")
    args = parser.parse_args()
    jobs = build_jobs()
    if args.resume_from_date:
        jobs = [j for j in jobs if j["date"] >= args.resume_from_date]
    if args.preflight:
        return preflight(jobs)
    code = preflight(jobs)
    if code != 0:
        return code
    return run(jobs)


if __name__ == "__main__":
    sys.exit(main())
