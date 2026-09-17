"""集中加载 .env 环境变量与所有配置常量。

敏感信息(AI Builder Code、钉钉 webhook)一律放 .env,不进 git。
"""
import os
import re

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path=None):
    """极简 .env 解析(避免额外依赖):KEY=VALUE 逐行,忽略 # 注释与空行。"""
    path = path or os.path.join(_BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ.setdefault(k, v)


_load_dotenv()


def _get(key, default=None):
    return os.environ.get(key, default)


def _get_bool(key, default=False):
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _get_float(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _get_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


# ===== 交易归因(核心) =====
AI_BUILDER_CODE = _get("OKX_AI_BUILDER_CODE", "").strip()
DEMO = _get_bool("OKX_DEMO", True)   # True=模拟盘(默认) / False=实盘
DRY_RUN = _get_bool("DRY_RUN", True)  # True=只打日志不真下单

# ===== 监控合约 =====
SYMBOLS = [s.strip().upper() for s in _get("SYMBOLS", "").split(",") if s.strip()]
TRADE_WHITELIST = [s.strip().upper() for s in _get("TRADE_WHITELIST", "").split(",") if s.strip()]

# ===== 均线/拐点参数(沿用斜率归零系统实测值,勿随意改) =====
MA_MID = _get_int("MA_MID", 13)
MA_LONG = _get_int("MA_LONG", 35)
TURN_LOOKBACK = _get_int("TURN_LOOKBACK", 5)
TURN_MIN = _get_float("TURN_MIN", 0.08)
TURN_BAND = _get_float("TURN_BAND", 0.03)

# ===== 下单/仓位/风控 =====
MARGIN_PER_TRADE = _get_float("MARGIN_PER_TRADE", 100.0)  # 每单固定保证金(USDT)
LEVERAGE = _get_int("LEVERAGE", 3)
TD_MODE = _get("TD_MODE", "cross")                        # cross 全仓 / isolated 逐仓
SL_PCT = _get_float("SL_PCT", 0.02)                       # 硬止损百分比(2%)
TP_PCT = _get_float("TP_PCT", 0.0)                        # 止盈百分比(0=关闭)
REVERSE_ON_SIGNAL = _get_bool("REVERSE_ON_SIGNAL", False)  # 反向信号是否反手
MAX_OPEN_POSITIONS = _get_int("MAX_OPEN_POSITIONS", 3)
MAX_TOTAL_LOSS = _get_float("MAX_TOTAL_LOSS", 300.0)      # 总亏损熔断阈值(USDT)

# ===== 扫描 =====
SCAN_INTERVAL = _get_int("SCAN_INTERVAL", 10)
CANDLE_LIMIT = _get_int("CANDLE_LIMIT", 60)
HISTORY_DAYS = _get_int("HISTORY_DAYS", 3)

# ===== 钉钉 =====
DING_WEBHOOK = _get("DING_WEBHOOK", "")
DING_KEYWORD = _get("DING_KEYWORD", "均线拐点交易")
PROGRAM_NAME = _get("PROGRAM_NAME", "均线拐点自动交易")

# ===== 网络 =====
BASE_URL = _get("BASE_URL", "https://www.okx.com")
PROXIES = {
    "http": _get("HTTP_PROXY", "http://127.0.0.1:7890"),
    "https": _get("HTTPS_PROXY", "http://127.0.0.1:7890"),
}


def validate():
    """启动前校验关键配置,返回警告列表(不阻塞,但会打印+钉钉)。"""
    warns = []
    if not re.fullmatch(r"[A-Za-z0-9]{1,16}", AI_BUILDER_CODE):
        warns.append("未配置有效 OKX_AI_BUILDER_CODE(1-16位字母数字)——下单不带归因码,拿不到返佣")
    if not DING_WEBHOOK:
        warns.append("未配置 DING_WEBHOOK——钉钉通知不可用,仅打日志")
    if not SYMBOLS:
        warns.append("SYMBOLS 为空——没有监控/交易标的")
    return warns


def trade_enabled(inst_id):
    """该合约是否允许交易(白名单为空=全部允许)。"""
    if not TRADE_WHITELIST:
        return True
    return inst_id in TRADE_WHITELIST
