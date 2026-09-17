"""入口:启动对账 → 主循环调度 → 轮询止损。

用法:
  python main.py                # 正常运行(DRY_RUN/DEMO 由 .env 控制)
  python main.py --selftest     # 验证 okx CLI 下单 + aiBuilderCode 归因(demo)
  python main.py --backtest     # 历史回放验证信号→动作映射(强制 DRY_RUN)
"""
import sys
import time
from datetime import datetime

# Windows GBK 修复(放在最前)
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import config
import market
import notify
import okx_cli
import signal as signal_mod
import state_store
from engine import TradingEngine


def _banner(title):
    print("=" * 56)
    print(title)
    print("=" * 56)


def selftest():
    """Phase 1:验证 okx CLI 下单 + aiBuilderCode 归因链路(demo,最小仓)。"""
    _banner(f"selftest — 验证下单 + 归因链路(demo={config.DEMO})")
    if not config.AI_BUILDER_CODE:
        print("⚠ 未配置 OKX_AI_BUILDER_CODE,请先在 .env 填入(申请 AI Builder Program 后从控制台拿)")
        return
    eng = TradingEngine()
    eng.sync_with_exchange()
    inst = config.SYMBOLS[0] if config.SYMBOLS else "BTC-USDT-SWAP"
    print(f"\n[1] 账户配置 posMode:")
    print("   ", okx_cli.get_account_config())
    print(f"\n[2] 设置杠杆 {config.LEVERAGE}x:")
    print("   ", okx_cli.set_leverage(inst, config.LEVERAGE))
    print(f"\n[3] 市价开多(保证金 1 USDT):")
    res = okx_cli.place_market(inst, "buy", sz=1, tgt_ccy="margin",
                               pos_side=eng._pos_side("long"))
    print("   ", res)
    if not okx_cli.ok(res):
        print("\n✗ 下单失败,终止(检查 demo 账户余额/授权/网络)")
        return
    ord_id = (res.get("data") or [{}])[0].get("ordId")
    time.sleep(2)
    print(f"\n[4] 当前持仓:")
    print("   ", okx_cli.get_positions())
    print(f"\n[5] 平多:")
    print("   ", okx_cli.close_position(inst, eng._pos_side("long")))
    print(f"\n[6] 成交订单(核对 aiBuilderCode):")
    print("   ", okx_cli.get_orders(inst))
    _banner("selftest 完成 —— 到 OKX AI Builder 控制台核对归因")


def backtest():
    """Phase 2:历史回放验证「拐点→动作」映射(不真下单,用 DRY_RUN 状态机)。"""
    _banner("backtest — 历史回放(DRY_RUN 状态机)")
    config.DING_WEBHOOK = ""  # 回放不发钉钉,只打日志
    if not config.SYMBOLS:
        print("SYMBOLS 为空,请在 .env 配置监控合约")
        return
    eng = TradingEngine()
    eng.sync_with_exchange()
    for inst_id in config.SYMBOLS:
        df = market.fetch_history(inst_id, days=config.HISTORY_DAYS, bar="5m")
        if df is None or len(df) < config.MA_LONG + 5:
            print(f"\n{inst_id}: 历史数据不足,跳过")
            continue
        print(f"\n{inst_id}: 回放 {len(df)} 根 5m K 线")
        actions = []
        for i in range(config.MA_LONG + 2, len(df) - 1):
            sub = df.iloc[:i + 1].reset_index(drop=True)
            sigs = signal_mod.detect_signals(sub, idx=-2)
            if sigs:
                ok, msg = eng.on_signals(inst_id, sigs)
                if ok:
                    t = datetime.fromtimestamp(sigs[0].ts / 1000).strftime('%m-%d %H:%M')
                    actions.append(f"{t}  {msg}")
        for a in actions:
            print("   " + a)
        print(f"{inst_id}: 共 {len(actions)} 个动作")
    _banner("backtest 完成")


def run():
    """正常主循环:扫描信号 → 引擎决策下单;轮询止损兜底。"""
    print(f"{config.PROGRAM_NAME} 启动 | DRY_RUN={config.DRY_RUN} DEMO={config.DEMO}")
    for w in config.validate():
        notify.log_and_notify("配置警告", w)
    eng = TradingEngine()
    eng.sync_with_exchange()
    if config.DRY_RUN:
        notify.log_and_notify("模式", "DRY_RUN=True,只打日志不下单")
    elif config.DEMO:
        notify.log_and_notify("模式", "OKX_DEMO=True,模拟盘真下单")

    while True:
        for inst_id in config.SYMBOLS:
            try:
                df = market.fetch_candles(inst_id, bar="5m")
                if df is None or len(df) < config.MA_LONG + 2:
                    continue
                sigs = signal_mod.detect_signals(df, idx=-2)
                if sigs:
                    ok, msg = eng.on_signals(inst_id, sigs)
                    if ok:
                        print(f"  ✓ {inst_id}: {msg}")
            except Exception as e:
                print(f"[{datetime.now()}] {inst_id} 扫描异常: {e}")
        # 轮询止损/TP 兜底(Phase 3)
        eng.poll_stop()
        time.sleep(config.SCAN_INTERVAL)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="验证下单+归因链路")
    ap.add_argument("--backtest", action="store_true", help="历史回放验证信号映射")
    args = ap.parse_args()
    if args.selftest:
        selftest()
    elif args.backtest:
        backtest()
    else:
        run()
