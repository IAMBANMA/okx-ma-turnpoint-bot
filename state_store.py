"""持仓状态机 + JSON 原子持久化(跨重启保留)。"""
import json
import os
import threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(_BASE_DIR, "state")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
STOP_FILE = os.path.join(STATE_DIR, "stop_trading")

_lock = threading.RLock()


def _default_state():
    return {
        "positions": {},       # instId -> {side, entry_price, sz_margin, ord_id, ts}
        "last_signal_ts": {},  # instId -> 已处理过的最后信号 K 线 ts(防重启重复交易)
        "paused": False,       # 总熔断/杀开关
        "total_loss": 0.0,
    }


def load_state():
    """加载状态;文件缺失/损坏则返回默认状态。"""
    with _lock:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                st = _default_state()
                if isinstance(data, dict):
                    st.update(data)
                return st
            except Exception:
                pass
        return _default_state()


def save_state(state):
    """原子写:临时文件 + os.replace。"""
    with _lock:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)


# ===== 杀开关(紧急停止全自动交易) =====
def is_stopped():
    return os.path.exists(STOP_FILE)


def set_stop():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STOP_FILE, "w") as f:
        f.write("stop")


def clear_stop():
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)


# ===== 持仓辅助 =====
def position_side(state, inst_id):
    """本地记录的持仓方向: 'long'/'short'/None。"""
    p = state["positions"].get(inst_id)
    return p["side"] if p else None


def set_position(state, inst_id, pos):
    """写持仓(pos=None 删除)并落盘。"""
    if pos is None:
        state["positions"].pop(inst_id, None)
    else:
        state["positions"][inst_id] = pos
    save_state(state)


def mark_signal(state, inst_id, ts):
    """记录已处理的信号 K 线 ts,防重启/重复处理。"""
    state["last_signal_ts"][inst_id] = int(ts)
    save_state(state)
