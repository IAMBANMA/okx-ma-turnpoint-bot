"""均线计算 + 拐点信号检测(从斜率归零系统移植的最小子集)。

拐点语义(沿用 OKX_5M_SlopeZero_Monitor.py 的 check_turn_point,勿随意改):
  top    顶部拐点——此前 MA 上行,当前单根斜率进入近零带(见顶回落,偏空)
  bottom 底部拐点——此前 MA 下行,当前单根斜率进入近零带(见底回升,偏多)
"""
from dataclasses import dataclass

import pandas as pd

import config


@dataclass
class Signal:
    direction: str   # "top"(偏空) / "bottom"(偏多)
    ma: int          # 13 | 35
    ts: int          # 判定 K 线毫秒时间戳
    price: float     # 判定 K 线收盘价


def compute_ma(df, period):
    """计算均线,返回 Series。"""
    return df["close"].rolling(window=period).mean()


def compute_ma_slope(df, period, lookback):
    """均线斜率 Series(百分比):单根 = (MA[i]-MA[i-1])/MA[i-1]*100,再 lookback 根平滑。"""
    ma = compute_ma(df, period)
    slope_per_bar = ma.diff() / ma.shift(1) * 100
    return slope_per_bar.rolling(window=lookback).mean()


def check_turn_point(slope_series, i, direction, lookback, min_pct, band):
    """检测拐点(单根斜率进入近零带,且此前有真实单边趋势)。"""
    if i < 1:
        return False
    cur = slope_series.iloc[i]
    prev = slope_series.iloc[i - 1]
    if pd.isna(cur) or pd.isna(prev):
        return False
    if direction == "top":
        if not (cur <= band and prev > 0):
            return False
        window = slope_series.iloc[max(0, i - lookback):i]
        if len(window) == 0 or window.isna().all():
            return False
        return bool(window.max() >= min_pct)
    else:
        if not (cur >= -band and prev < 0):
            return False
        window = slope_series.iloc[max(0, i - lookback):i]
        if len(window) == 0 or window.isna().all():
            return False
        return bool(window.min() <= -min_pct)


def detect_signals(df, idx=-2):
    """在 df(正序 K 线)的 idx 位置判定 MA13/MA35 拐点。

    idx=-2 表示最新一根已收盘 K 线(与斜率归零系统同口径:只判已收盘 K 线防抖动)。
    返回 list[Signal]。单根斜率 lookback=1(拐点响应最快)。
    """
    pos_idx = len(df) + idx  # 负索引转正索引(check_turn_point 的 i<1 守卫会拒绝负数)
    out = []
    for period in (config.MA_MID, config.MA_LONG):
        raw = compute_ma_slope(df, period, 1)
        for direction in ("top", "bottom"):
            if check_turn_point(raw, pos_idx, direction,
                                config.TURN_LOOKBACK, config.TURN_MIN, config.TURN_BAND):
                out.append(Signal(
                    direction=direction,
                    ma=period,
                    ts=int(df.iloc[idx]["ts"]),
                    price=float(df.iloc[idx]["close"]),
                ))
    return out
