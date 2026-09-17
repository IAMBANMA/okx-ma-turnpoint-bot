"""钉钉通知(从斜率归零系统移植)。"""
import threading
import time
from datetime import datetime

import requests

import config


def send(title, msg):
    """同步发一条钉钉 markdown 消息;未配置 webhook 直接返回 False。"""
    if not config.DING_WEBHOOK:
        return False
    text = f"## {title}\n---\n{config.DING_KEYWORD}\n---\n{msg}"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    try:
        resp = requests.post(config.DING_WEBHOOK, json=payload, timeout=10)
        if resp.status_code == 200 and resp.json().get("errcode", -1) == 0:
            return True
    except Exception:
        pass
    return False


# 简单去重:同 key 5s 内只发一次(防信号刷屏)
_last = {}
_lock = threading.Lock()


def log_and_notify(title, msg, dedup_key=None):
    """打印日志 + 钉钉(后台线程,不阻塞主循环;同 key 5s 去重)。"""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {title} | {msg}"
    print(line, flush=True)
    if not config.DING_WEBHOOK:
        return
    key = dedup_key or title
    now = time.monotonic()
    with _lock:
        if now - _last.get(key, 0) < 5:
            return
        _last[key] = now
    threading.Thread(target=send, args=(f"{config.PROGRAM_NAME} | {title}", msg), daemon=True).start()
