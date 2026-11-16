# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, List, Tuple, Optional, Dict

import numpy as np
import pandas as pd

from config import DEFAULT_PRICE_ADJ
from utils import norm_code, guess_market


# 项目根目录（data_loader.py 位于项目根），持久化缓存默认放在其下。
PROJECT_ROOT = Path(__file__).resolve().parent

# 日频 WSD 持久化缓存的运行期配置，由 configure_daily_cache() 设置。
_DAILY_CACHE: Dict[str, Any] = {
    "enabled": True,      # 默认开启持久化缓存
    "refresh": False,     # True 时忽略本地缓存、全量重取
    "dir": None,          # None 时使用项目根 / wind_cache
}


def configure_daily_cache(
    enabled: bool = True,
    refresh: bool = False,
    cache_dir: Optional[str | Path] = None,
) -> None:
    """配置日频 WSD 持久化缓存。主脚本在启动时按命令行参数调用。"""
    _DAILY_CACHE["enabled"] = bool(enabled)
    _DAILY_CACHE["refresh"] = bool(refresh)
    _DAILY_CACHE["dir"] = Path(cache_dir).expanduser() if cache_dir else None


def _daily_cache_dir() -> Path:
    d = _DAILY_CACHE["dir"]
    if d is None:
        d = PROJECT_ROOT / "wind_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_token(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))


def _daily_cache_path(code: str, price_adj: str) -> Path:
    """每只标的、每个复权口径对应一个 parquet 缓存文件。"""
    adj = str(price_adj or "").strip() or "none"
    return _daily_cache_dir() / f"{_safe_token(norm_code(code))}__{_safe_token(adj)}.parquet"


def _read_daily_cache(path: Path) -> Optional[pd.DataFrame]:
    try:
        if path.exists():
            return normalize_daily_df(pd.read_parquet(path))
    except Exception:
        return None
    return None


def _write_daily_cache(path: Path, df: pd.DataFrame) -> None:
    """原子写入：先写临时文件再替换，避免中断产生损坏缓存。落盘失败不阻断主流程。"""
    try:
        tmp = path.with_name(path.name + ".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
    except Exception:
        pass


# 分钟频 WSI 持久化缓存的运行期配置，由 configure_minute_cache() 设置。
_MINUTE_CACHE: Dict[str, Any] = {
    "enabled": True,      # 默认开启持久化缓存
    "refresh": False,     # True 时忽略本地缓存、重新向 WSI 请求
    "dir": None,          # None 时使用项目根 / minute_cache
}


def configure_minute_cache(
    enabled: bool = True,
    refresh: bool = False,
    cache_dir: Optional[str | Path] = None,
) -> None:
    """配置分钟频 WSI 持久化缓存。"""
    _MINUTE_CACHE["enabled"] = bool(enabled)
    _MINUTE_CACHE["refresh"] = bool(refresh)
    _MINUTE_CACHE["dir"] = Path(cache_dir).expanduser() if cache_dir else None


def _minute_cache_dir() -> Path:
    d = _MINUTE_CACHE["dir"]
    if d is None:
        d = PROJECT_ROOT / "minute_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _minute_cache_path(code: str, bar_size: int, date: str) -> Path:
    """每只标的、每个 bar 周期、每个交易日一个 parquet 缓存文件。"""
    day = pd.to_datetime(date, errors="coerce")
    day_token = day.strftime("%Y%m%d") if pd.notna(day) else _safe_token(date)
    return _minute_cache_dir() / f"{_safe_token(norm_code(code))}__bs{int(bar_size)}__{day_token}.parquet"


def _read_minute_cache(path: Path) -> Optional[pd.DataFrame]:
    try:
        if not path.exists():
            return None
        raw = pd.read_parquet(path)
        if raw is None or raw.empty:
            return None
        fetch_code = ""
        if "wind_fetch_code" in raw.columns:
            vals = raw["wind_fetch_code"].dropna()
            fetch_code = str(vals.iloc[-1]) if not vals.empty else ""
            raw = raw.drop(columns=["wind_fetch_code"])
        df = normalize_intraday_df(raw)
        if df.empty:
            return None
        df.attrs["wind_fetch_code"] = fetch_code
        return df
    except Exception:
        return None


def _write_minute_cache(path: Path, df: pd.DataFrame) -> None:
    """原子写入分钟 bar；额外记录实际使用的 Wind 代码，便于审计。落盘失败不阻断主流程。"""
    try:
        out = df.copy()
        out["wind_fetch_code"] = df.attrs.get("wind_fetch_code", "") or ""
        tmp = path.with_name(path.name + ".tmp")
        out.to_parquet(tmp, index=False)
        tmp.replace(path)
    except Exception:
        pass


def ensure_wind() -> Any:
    try:
        from WindPy import w
    except Exception as e:
        raise RuntimeError("WindPy is not available. Please run this in an environment with Wind installed.") from e
    if not w.isconnected():
        w.start()
    return w



def wind_code_candidates(code: str) -> List[str]:
    """Return Wind-friendly code candidates while keeping project-internal codes unchanged.

    The live project stores HK codes in a 5-digit canonical form (e.g. 00941.HK)
    for stable matching across holdings/Oplus/instructions.  Some Wind terminals,
    including the user's environment, return data for the 4-digit form instead
    (e.g. 0941.HK).  For HK we therefore try the one-leading-zero-removed form
    first, then the canonical form, then the fully stripped numeric form.
    """
    c = norm_code(code)
    out: List[str] = []

    def add(x: str) -> None:
        x = norm_code(x)
        if x and x not in out:
            out.append(x)

    if c.endswith('.HK'):
        stem = c[:-3]
        if stem.isdigit():
            if len(stem) == 5 and stem.startswith('0'):
                add(f"{stem[1:]}.HK")
            add(c)
            stripped = stem.lstrip('0') or '0'
            add(f"{stripped}.HK")
            return out
    add(c)
    return out

def intraday_window(market: str, date: str) -> List[Tuple[str, str]]:
    if market == "A":
        return [(f"{date} 09:30:00", f"{date} 11:30:00"), (f"{date} 13:00:00", f"{date} 15:00:00")]
    return [(f"{date} 09:30:00", f"{date} 12:00:00"), (f"{date} 13:00:00", f"{date} 16:00:00")]


def normalize_daily_df(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    rename = {}
    for c in data.columns:
        lc = str(c).strip().lower()
        if lc in {"time", "datetime", "trade_dt"}:
            rename[c] = "date"
        elif lc == "amt":
            rename[c] = "amount"
        elif lc == "vol":
            rename[c] = "volume"
        else:
            rename[c] = lc
    data = data.rename(columns=rename)
    required = ["high", "low", "close", "volume", "amount"]
    missing = [c for c in required if c not in data.columns]
    if missing:
        raise ValueError(f"daily data missing columns: {missing}")
    if "date" not in data.columns:
        data["date"] = pd.RangeIndex(len(data))
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for c in required:
        data[c] = pd.to_numeric(data[c], errors="coerce")
    data = data.dropna(subset=["date", "high", "low", "close"]).sort_values("date").reset_index(drop=True)
    return data[["date", "high", "low", "close", "volume", "amount"]]


def normalize_intraday_df(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["date", "high", "low", "close", "volume", "amount"])
    data = df.copy()
    rename = {}
    for c in data.columns:
        lc = str(c).strip().lower()
        if lc in {"time", "datetime", "trade_dt"}:
            rename[c] = "date"
        elif lc == "amt":
            rename[c] = "amount"
        elif lc == "vol":
            rename[c] = "volume"
        else:
            rename[c] = lc
    data = data.rename(columns=rename)
    required = ["close", "volume", "amount"]
    if any(c not in data.columns for c in required):
        return pd.DataFrame(columns=["date", "high", "low", "close", "volume", "amount"])
    if "date" not in data.columns:
        data["date"] = pd.RangeIndex(len(data))
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for c in ["high", "low", "close", "volume", "amount"]:
        if c in data.columns:
            data[c] = pd.to_numeric(data[c], errors="coerce")
    if "high" not in data.columns:
        data["high"] = data["close"]
    if "low" not in data.columns:
        data["low"] = data["close"]
    data = data.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return data[["date", "high", "low", "close", "volume", "amount"]]


def _wsd_request_range(code: str, req_start: Any, req_end: Any, price_adj: str, allow_empty: bool = False) -> pd.DataFrame:
    """向 Wind 请求单个时间区间的日频 OHLCVA，返回规范化 DataFrame。"""
    w = ensure_wind()
    opt = f"PriceAdj={price_adj}" if str(price_adj or "").strip() else ""
    attempts = []
    hard_errors = []
    for wind_code in wind_code_candidates(code):
        r = w.wsd(
            wind_code,
            "high,low,close,volume,amt",
            pd.Timestamp(req_start).strftime("%Y-%m-%d"),
            pd.Timestamp(req_end).strftime("%Y-%m-%d"),
            opt,
        )
        err = getattr(r, "ErrorCode", -1)
        if err != 0:
            attempts.append(f"{wind_code}: ErrorCode={err}")
            hard_errors.append(wind_code)
            continue
        try:
            df = pd.DataFrame(np.array(r.Data).T, columns=["high", "low", "close", "volume", "amount"])
            df["date"] = pd.to_datetime(r.Times)
            df = normalize_daily_df(df)
        except Exception as exc:
            attempts.append(f"{wind_code}: parse={type(exc).__name__}")
            continue
        if not df.empty:
            df.attrs["wind_fetch_code"] = wind_code
            return df
        attempts.append(f"{wind_code}: empty")
    # 允许空（如次新股上市前区间）：仅当所有候选都 ErrorCode=0 但无数据时返回空表；
    # 存在真实 ErrorCode 时仍按错误处理，避免吞掉配额/权限问题。
    if allow_empty and not hard_errors:
        return pd.DataFrame(columns=["date", "high", "low", "close", "volume", "amount"])
    raise RuntimeError(
        f"Wind wsd failed/no data for {code}; tried {wind_code_candidates(code)}; details: {'; '.join(attempts)}"
    )


def fetch_wind_daily_ohlcva(code: str, end_date: str, lookback_years: int = 3, price_adj: str = DEFAULT_PRICE_ADJ) -> pd.DataFrame:
    code = norm_code(code)
    end = pd.to_datetime(end_date).normalize()
    start = (end - pd.DateOffset(years=lookback_years)).normalize()

    # 未启用持久化缓存：保持原始行为，每次全量请求。
    if not _DAILY_CACHE["enabled"]:
        return _wsd_request_range(code, start, end, price_adj)

    cache_path = _daily_cache_path(code, price_adj)
    cached = None if _DAILY_CACHE["refresh"] else _read_daily_cache(cache_path)

    if cached is None or cached.empty:
        # 无缓存或强制刷新：取完整目标区间。
        merged = _wsd_request_range(code, start, end, price_adj)
    else:
        parts: List[pd.DataFrame] = []
        cmin = cached["date"].min()
        cmax = cached["date"].max()
        # 头部缺失（缓存最早日晚于目标起点）：补取早期区间。
        # 次新股上市前区间 Wind 返回 ErrorCode=0 但无数据，allow_empty 容忍该空结果。
        if cmin > start:
            head = _wsd_request_range(code, start, cmin - pd.Timedelta(days=1), price_adj, allow_empty=True)
            if not head.empty:
                parts.append(head)
        parts.append(cached)
        # 尾部增量：从缓存最后日期起（含，重叠 1 天用于刷新近期数据）。
        if cmax < end:
            parts.append(_wsd_request_range(code, cmax, end, price_adj))
        merged = (
            pd.concat(parts, ignore_index=True)
            .drop_duplicates(subset="date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )

    # 落盘完整缓存（长期累积，历史部分持续复用）。
    _write_daily_cache(cache_path, merged)

    # 切出本次目标窗口返回，结构与原函数一致。
    result = merged[(merged["date"] >= start) & (merged["date"] <= end)].reset_index(drop=True)
    return result



def fetch_wind_latest_raw_close(code: str, end_date: str, lookback_days: int = 15, return_fetch_code: bool = False):
    """Return the latest unadjusted close on/before end_date.

    For HK securities Wind code candidates are tried automatically, e.g.
    internal 00941.HK -> Wind 0941.HK first.  Set return_fetch_code=True to
    receive ``(price, wind_code_used)`` for audit output.
    """
    w = ensure_wind()
    code = norm_code(code)
    end = pd.to_datetime(end_date)
    start = end - pd.Timedelta(days=max(int(lookback_days), 1))
    attempts = []
    for wind_code in wind_code_candidates(code):
        r = w.wsd(
            wind_code,
            "close",
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            "",
        )
        err = getattr(r, "ErrorCode", -1)
        if err != 0:
            attempts.append(f"{wind_code}: ErrorCode={err}")
            continue
        vals = []
        if getattr(r, "Data", None):
            try:
                vals = list(r.Data[0])
            except Exception:
                vals = []
        ss = pd.to_numeric(pd.Series(vals), errors="coerce").dropna()
        if not ss.empty:
            px = float(ss.iloc[-1])
            if np.isfinite(px) and px > 0:
                return (px, wind_code) if return_fetch_code else px
        attempts.append(f"{wind_code}: empty")
    if return_fetch_code:
        return float("nan"), ""
    return float("nan")


def fetch_wind_intraday_minute(code: str, date: str, bar_size: int = 1, price_adj: str = DEFAULT_PRICE_ADJ) -> pd.DataFrame:
    """Fetch Wind minute bars, trying HK Wind code variants automatically.

    带分钟级持久化缓存：同一 (代码, bar_size, 交易日) 只要本地有非空 parquet 就直接返回，
    不再消耗 WSI 配额；未命中才请求并落盘。
    """
    code = norm_code(code)
    bar_size = int(bar_size)
    cache_path: Optional[Path] = None
    if _MINUTE_CACHE["enabled"]:
        cache_path = _minute_cache_path(code, bar_size, date)
        if not _MINUTE_CACHE["refresh"]:
            cached = _read_minute_cache(cache_path)
            if cached is not None and not cached.empty:
                return cached

    w = ensure_wind()
    market = guess_market(code)
    parts = []
    used_codes = []
    opt = f"BarSize={bar_size}"
    if str(price_adj or "").strip():
        opt += f";PriceAdj={price_adj}"
    candidates = wind_code_candidates(code)
    for st, ed in intraday_window(market, date):
        tmp = None
        used_code = ""
        for wind_code in candidates:
            r = w.wsi(wind_code, "high,low,close,volume,amt", st, ed, opt)
            if getattr(r, "ErrorCode", -1) != 0:
                r = w.wsi(wind_code, "close,volume,amt", st, ed, opt)
                if getattr(r, "ErrorCode", -1) != 0:
                    continue
                candidate = pd.DataFrame(np.array(r.Data).T, columns=["close", "volume", "amount"])
            else:
                candidate = pd.DataFrame(np.array(r.Data).T, columns=["high", "low", "close", "volume", "amount"])
            candidate["date"] = pd.to_datetime(r.Times)
            candidate = normalize_intraday_df(candidate)
            if not candidate.empty:
                tmp = candidate
                used_code = wind_code
                break
        if tmp is not None and not tmp.empty:
            tmp.attrs["wind_fetch_code"] = used_code
            parts.append(tmp)
            used_codes.append(used_code)
    if not parts:
        return pd.DataFrame(columns=["date", "high", "low", "close", "volume", "amount"])
    result = normalize_intraday_df(pd.concat(parts, ignore_index=True))
    result.attrs["wind_fetch_code"] = used_codes[-1] if used_codes else ""
    if cache_path is not None and not result.empty:
        _write_minute_cache(cache_path, result)
    return result


def load_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError("file must be .xlsx, .xls, or .csv")


def parse_levels(s: Optional[str]) -> List[Dict[str, float]]:
    if s is None or str(s).strip() == "":
        return []
    s = str(s).strip()
    path = Path(s)
    if path.exists():
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            df = load_table(path)
            data = df.to_dict("records")
    else:
        data = json.loads(s)
    return [{"price": float(x["price"]), "target_pos": float(x["target_pos"])} for x in data]
