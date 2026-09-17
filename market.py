"""行情获取(OKX 公开 API,走代理 + 直连降级)。"""
import time

import pandas as pd
import requests

import config

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})
_adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
session.mount("https://", _adapter)


def http_get(url, timeout=5):
    """GET 请求:优先走代理,代理连不上自动降级直连。代理尝试用较短超时避免卡顿翻倍。"""
    try:
        return session.get(url, proxies=config.PROXIES, timeout=min(timeout, 2))
    except Exception:
        try:
            return session.get(url, timeout=timeout)
        except Exception:
            return None


def _to_df(data):
    df = pd.DataFrame(data, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
    df["close"] = df["close"].astype(float)
    df["ts"] = df["ts"].astype("int64")
    return df


def fetch_candles(symbol, bar="5m", limit=None):
    """获取最新 K 线(正序),失败返回 None。"""
    limit = limit or config.CANDLE_LIMIT
    url = f"{config.BASE_URL}/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
    try:
        resp = http_get(url, timeout=5)
        if resp is None:
            return None
        data = resp.json()
        if data.get("code") != "0" or not data["data"]:
            return None
        df = _to_df(data["data"])
        return df.iloc[::-1].reset_index(drop=True)  # 转时间正序
    except Exception:
        return None


def fetch_history(symbol, days=None, bar="5m"):
    """分页抓取最近 N 天 K 线(正序),用于历史回放。"""
    days = days or config.HISTORY_DAYS
    per_day = 1440 if bar == "1m" else 288
    target = days * per_day
    frames = []
    after = None
    for _ in range(12):  # 最多 12 页(每页 300 根)
        url = f"{config.BASE_URL}/api/v5/market/candles?instId={symbol}&bar={bar}&limit=300"
        if after:
            url += f"&after={after}"
        try:
            resp = http_get(url, timeout=8)
            if resp is None:
                break
            data = resp.json()
            if data.get("code") != "0" or not data["data"]:
                break
            df = _to_df(data["data"]).iloc[::-1].reset_index(drop=True)  # 正序
            frames.append(df)
            if sum(len(f) for f in frames) >= target or len(df) < 300:
                break
            after = int(df.iloc[0]["ts"])  # 最早时间戳 → 继续往前翻页
            time.sleep(0.15)
        except Exception:
            break
    if not frames:
        return None
    big = pd.concat(frames, ignore_index=True).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return big.tail(target) if len(big) > target else big
