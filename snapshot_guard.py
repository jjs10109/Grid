"""真实快照为仓位权威；禁止跨产品、未来快照或陈旧快照静默混用。"""
from pathlib import Path
import pandas as pd


def validate_snapshot_date(path, cutoff, reader=None):
    if reader is None:
        import importlib
        reader = importlib.import_module("live_grid_simulator_current改").read_excel_any
    sheets = reader(Path(path), sheet_name=None)
    if not isinstance(sheets, dict):
        sheets = {"Sheet1": sheets}
    dates = []
    for frame in sheets.values():
        product_col = next((c for c in ["产品", "产品名称"] if c in frame), None)
        if product_col is None or "日期" not in frame:
            continue
        sub = frame[frame[product_col].astype(str).str.strip().isin({"聚砾1号", "巨杉资产聚砾1号", "聚砾网格模拟"})]
        parsed = pd.to_datetime(sub["日期"], errors="coerce").dropna()
        if not parsed.empty:
            dates.extend(parsed.dt.normalize().tolist())
    if not dates:
        raise ValueError("无法确认聚砾快照内部日期；请使用含产品和日期的真实持仓查询。")
    actual = max(dates)
    expected = pd.to_datetime(cutoff).normalize()
    if actual != expected:
        raise ValueError(f"聚砾持仓内部日期{actual.date()}与行情/成交截止日{expected.date()}不一致；请显式选择同日持仓及Oplus。")
    return actual.strftime("%Y-%m-%d")
