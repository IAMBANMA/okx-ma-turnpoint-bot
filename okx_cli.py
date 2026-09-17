"""okx CLI 封装——唯一触碰 `okx` 命令的层。

下单/平仓/查询全走这里;归因码 --aiBuilderCode 强制注入(缺失直接拒单)。
demo/live 由 config.DEMO 控制(全局 --demo 标志放在模块词之前)。
"""
import json
import subprocess
import sys
import threading
import time

import config


class OkxCliError(Exception):
    pass


# 下单限频:两次下单之间最小间隔(秒)。OKX 官方限速 60 单/2s/UID,信号稀疏,此处仅防 bug 刷单。
_place_lock = threading.Lock()
_last_place_ts = 0.0
_MIN_PLACE_INTERVAL = 0.15


def run_okx(args, demo=None, timeout=30):
    """执行 okx 命令并解析 JSON。失败抛 OkxCliError。"""
    if demo is None:
        demo = config.DEMO
    cmd = ["okx"]
    if demo:
        cmd.append("--demo")
    cmd += list(args) + ["--json"]
    # Windows 下 npm 全局 shim 是 .cmd,subprocess 直接执行会 FileNotFoundError,需经 cmd /c
    if sys.platform == "win32":
        cmd = ["cmd", "/c"] + cmd
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise OkxCliError("okx CLI 未安装,请先 npm install -g @okx_ai/okx-trade-cli")
    except subprocess.TimeoutExpired:
        raise OkxCliError(f"okx 命令超时: {' '.join(args)}")

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = out or (proc.stderr or "").strip()
        raise OkxCliError(f"okx 失败({proc.returncode}): {' '.join(args)} | {detail[:300]}")
    parsed = _try_json(out)
    if parsed is not None:
        return parsed
    # 失败时 CLI 输出纯文本 "Error: ... Code: 51001"(rc 仍为 0)
    err = (proc.stderr or "").strip()
    raise OkxCliError(f"okx 失败: {out[:300] or err[:300]}")


def _try_json(s):
    """尝试解析 JSON;兼容带日志/进度前缀的输出(提取首个 [ 或 { 到末个 ] 或 })。"""
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        a, b = s.find(open_ch), s.rfind(close_ch)
        if a != -1 and b > a:
            try:
                return json.loads(s[a:b + 1])
            except json.JSONDecodeError:
                pass
    return None


def ok(res):
    """兼容判断:run_okx 失败已抛异常,能拿到返回值即成功;此处保留对 code 包装格式的兜底。"""
    if isinstance(res, dict) and "code" in res:
        return str(res.get("code", "0")) == "0"
    return True


def _guard_ai_builder_code():
    if not config.AI_BUILDER_CODE:
        raise OkxCliError("缺少 OKX_AI_BUILDER_CODE,拒绝下单(归因码是返佣生命线)")


def _throttle():
    global _last_place_ts
    with _place_lock:
        wait = _MIN_PLACE_INTERVAL - (time.monotonic() - _last_place_ts)
        if wait > 0:
            time.sleep(wait)
        _last_place_ts = time.monotonic()


def place_market(inst_id, side, *, sz, tgt_ccy="margin", pos_side=None,
                 sl_trigger_px=None, tp_trigger_px=None, td_mode=None):
    """市价下单。sz 单位由 tgt_ccy 决定(margin=保证金 USDT,实际仓位=sz×杠杆)。
    返回 OKX v5 响应 dict(成功含 data[0].ordId)。"""
    _guard_ai_builder_code()
    _throttle()
    args = ["swap", "place",
            "--instId", inst_id,
            "--side", side,
            "--ordType", "market",
            "--sz", str(sz),
            "--tgtCcy", tgt_ccy,
            "--tdMode", td_mode or config.TD_MODE,
            "--aiBuilderCode", config.AI_BUILDER_CODE]
    if pos_side:
        args += ["--posSide", pos_side]
    if sl_trigger_px is not None:
        args += ["--slTriggerPx", str(sl_trigger_px), "--slOrdPx=-1"]
    if tp_trigger_px is not None:
        args += ["--tpTriggerPx", str(tp_trigger_px), "--tpOrdPx=-1"]
    return run_okx(args)


def close_position(inst_id, pos_side, td_mode=None):
    """整仓市价平仓(对冲模式需 pos_side)。返回 OKX v5 响应。"""
    _guard_ai_builder_code()
    _throttle()
    args = ["swap", "close",
            "--instId", inst_id,
            "--mgnMode", td_mode or config.TD_MODE,
            "--aiBuilderCode", config.AI_BUILDER_CODE]
    if pos_side:
        args += ["--posSide", pos_side]
    return run_okx(args)


def set_leverage(inst_id, lever, pos_side=None, mgn_mode=None):
    """设置杠杆(isolated+对冲模式 long/short 需分别设)。"""
    args = ["swap", "leverage",
            "--instId", inst_id,
            "--lever", str(lever),
            "--mgnMode", mgn_mode or config.TD_MODE]
    if pos_side:
        args += ["--posSide", pos_side]
    return run_okx(args)


def get_account_config():
    """账户配置(含 posMode: net_mode 净仓 / long_short_mode 对冲)。"""
    return run_okx(["account", "config"])


def get_positions():
    """当前持仓列表。返回 OKX v5 响应(data[] 含 instId/posSide/avgPx/pos/...)。"""
    return run_okx(["swap", "positions"])


def get_balance():
    """账户余额。"""
    return run_okx(["account", "balance"])


def get_instrument(inst_id):
    """合约信息(ctVal 面值 / tickSz 价格精度)。demo=False:公开行情不受模拟盘影响。"""
    return run_okx(["market", "instruments", "--instType", "SWAP", "--instId", inst_id], demo=False)


def get_orders(inst_id=None, state="filled"):
    """订单列表(核对归因/成交)。"""
    args = ["swap", "orders"]
    if inst_id:
        args += ["--instId", inst_id]
    if state:
        args += ["--state", state]
    return run_okx(args)
