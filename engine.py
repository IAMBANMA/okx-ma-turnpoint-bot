"""交易引擎:信号→动作映射 + 风控护栏 + 硬止损。

持仓判定以交易所实时查询为准(真实模式),DRY_RUN 下用本地虚拟持仓,保证状态机
「开多→平多」能完整流转用于验证。
"""
import time

import config
import market
import notify
import okx_cli
import state_store


def _items(res):
    """兼容 okx CLI 输出:解包数组(list)或 dict 包装(含 data),统一返回 data 列表。"""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        return res.get("data") or []
    return []


class TradingEngine:
    def __init__(self):
        self.state = state_store.load_state()
        self.pos_mode = None  # net_mode 净仓 / long_short_mode 对冲

    # ========== 启动对账 ==========
    def sync_with_exchange(self):
        """查账户 posMode(决定下单是否带 --posSide)。"""
        try:
            cfg = okx_cli.get_account_config()
            items = _items(cfg)
            if items:
                self.pos_mode = items[0].get("posMode")
                notify.log_and_notify("启动", f"posMode={self.pos_mode}")
            else:
                notify.log_and_notify("启动", f"查账户配置失败: {cfg}")
        except okx_cli.OkxCliError as e:
            notify.log_and_notify("启动", f"查账户配置异常: {e}")

    # ========== 持仓 ==========
    def live_positions(self):
        """交易所实时持仓 {instId: side}。"""
        out = {}
        try:
            res = okx_cli.get_positions()
            for p in _items(res):
                inst_id = p.get("instId")
                side = p.get("posSide")
                if inst_id and side:
                    out[inst_id] = side
        except okx_cli.OkxCliError as e:
            notify.log_and_notify("持仓查询", f"失败: {e}")
        return out

    def live_side(self, inst_id):
        """当前该合约的持仓方向。DRY_RUN 下用本地虚拟持仓。"""
        if config.DRY_RUN:
            return state_store.position_side(self.state, inst_id)
        return self.live_positions().get(inst_id)

    # ========== 止损/止盈价 ==========
    @staticmethod
    def _calc_sl(entry, side):
        if config.SL_PCT <= 0:
            return None
        return entry * (1 - config.SL_PCT) if side == "long" else entry * (1 + config.SL_PCT)

    @staticmethod
    def _calc_tp(entry, side):
        if config.TP_PCT <= 0:
            return None
        return entry * (1 + config.TP_PCT) if side == "long" else entry * (1 - config.TP_PCT)

    # ========== 下单/平仓 ==========
    def _pos_side(self, side):
        """对冲模式需 posSide,净仓模式省略。"""
        return side if self.pos_mode == "long_short_mode" else None

    def _open(self, inst_id, side, ref_price):
        """开仓 side='long'/'short'。返回 (ok, msg)。"""
        okx_side = "buy" if side == "long" else "sell"
        sl = self._calc_sl(ref_price, side)
        tp = self._calc_tp(ref_price, side)
        if config.DRY_RUN:
            self.state["positions"][inst_id] = {
                "side": side, "entry_price": ref_price,
                "sz_margin": config.MARGIN_PER_TRADE, "ord_id": "dry",
                "ts": int(time.time() * 1000),
            }
            state_store.save_state(self.state)
            return True, f"[DRY_RUN] 开{'多' if side == 'long' else '空'} {inst_id} margin={config.MARGIN_PER_TRADE} sl={sl} tp={tp}"
        try:
            res = okx_cli.place_market(
                inst_id, okx_side, sz=config.MARGIN_PER_TRADE, tgt_ccy="margin",
                pos_side=self._pos_side(side), sl_trigger_px=sl, tp_trigger_px=tp)
        except okx_cli.OkxCliError as e:
            return False, f"下单异常: {e}"
        if okx_cli.ok(res):
            data = _items(res)
            ord_id = data[0].get("ordId") if data else None
            self.state["positions"][inst_id] = {
                "side": side, "entry_price": ref_price,
                "sz_margin": config.MARGIN_PER_TRADE, "ord_id": ord_id,
                "ts": int(time.time() * 1000),
            }
            state_store.save_state(self.state)
            return True, f"开{'多' if side == 'long' else '空'}成功 ordId={ord_id} sl={sl}"
        return False, f"开仓失败 code={res.get('code') if isinstance(res, dict) else '?'} msg={res.get('msg') if isinstance(res, dict) else res}"

    def _close(self, inst_id, side, close_price=None):
        """平仓 side='long'/'short'。close_price 用于估算已实现盈亏(熔断用)。返回 (ok, msg)。"""
        entry = self.state["positions"].get(inst_id, {}).get("entry_price")
        if config.DRY_RUN:
            self.state["positions"].pop(inst_id, None)
            state_store.save_state(self.state)
            return True, f"[DRY_RUN] 平{'多' if side == 'long' else '空'} {inst_id}"
        try:
            res = okx_cli.close_position(inst_id, self._pos_side(side))
        except okx_cli.OkxCliError as e:
            return False, f"平仓异常: {e}"
        if okx_cli.ok(res):
            self.state["positions"].pop(inst_id, None)
            if close_price and entry:
                self._record_pnl(side, entry, close_price)
            else:
                state_store.save_state(self.state)
            return True, f"平{'多' if side == 'long' else '空'}成功"
        return False, f"平仓失败 code={res.get('code')} msg={res.get('msg')}"

    def _record_pnl(self, side, entry, close):
        """估算一次平仓的已实现盈亏并累计;达到熔断阈值则暂停开仓。"""
        notional = config.MARGIN_PER_TRADE * config.LEVERAGE
        pnl = ((close - entry) / entry * notional) if side == "long" else ((entry - close) / entry * notional)
        self.state["realized_pnl"] = round(self.state.get("realized_pnl", 0.0) + pnl, 4)
        if self.state["realized_pnl"] <= -config.MAX_TOTAL_LOSS:
            self.state["paused"] = True
            notify.log_and_notify("熔断", f"累计已实现亏损 {self.state['realized_pnl']:.2f} USDT 达阈值 -{config.MAX_TOTAL_LOSS},已暂停开仓(需手动复位 paused)")
        state_store.save_state(self.state)

    # ========== 风控护栏 ==========
    def _preflight_open(self, inst_id):
        if self.state["paused"] or state_store.is_stopped():
            return False, "杀开关/熔断中,拒绝开仓"
        live = self.live_positions()
        if inst_id not in live and len(live) >= config.MAX_OPEN_POSITIONS:
            return False, f"已达最大持仓数 {config.MAX_OPEN_POSITIONS}"
        # 余额检查:可用保证金 ≥ 每单保证金×1.2(读不到有效值则跳过,靠 OKX 下单失败兜底)
        avail = self._available_margin()
        if avail is not None and avail < config.MARGIN_PER_TRADE * 1.2:
            return False, f"可用保证金不足 {avail:.2f} < {config.MARGIN_PER_TRADE * 1.2:.2f}"
        return True, ""

    def _available_margin(self):
        """尽力读取可用保证金(USDT/USDC),读不到有效值返回 None。"""
        try:
            res = okx_cli.get_balance()
            items = _items(res)
            if not items:
                return None
            top = items[0]
            for key in ("availEq", "totalEq", "eq", "availBal", "cashBal"):
                v = top.get(key)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            for d in top.get("details") or []:
                if d.get("ccy") in ("USDT", "USDC"):
                    v = d.get("availBal") or d.get("cashBal") or d.get("eq")
                    if v not in (None, ""):
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            continue
        except okx_cli.OkxCliError:
            pass
        return None

    # ========== 信号处理 ==========
    def on_signals(self, inst_id, signals):
        """处理同一根 K 线的拐点信号列表(可能含 MA13/MA35 各一个)。"""
        if not signals:
            return False, ""
        if not config.trade_enabled(inst_id):
            return False, "不在交易白名单"
        ts = signals[0].ts
        if self.state["last_signal_ts"].get(inst_id, 0) >= ts:
            return False, "已处理(去重)"
        # 先标记,防这根 K 线被重复处理(无论决策结果如何)
        state_store.mark_signal(self.state, inst_id, ts)

        # 优先级:MA35 优先;同 ts 内 MA13+MA35 方向相反 → 冲突跳过
        sig_35 = [s for s in signals if s.ma == config.MA_LONG]
        sig_13 = [s for s in signals if s.ma == config.MA_MID]
        if sig_35 and sig_13 and sig_35[0].direction != sig_13[0].direction:
            notify.log_and_notify("方向冲突", f"{inst_id} MA13/MA35 拐点方向相反,本轮跳过",
                                  dedup_key=f"conflict:{inst_id}")
            return False, "方向冲突跳过"
        sig = (sig_35 or sig_13)[0]

        live_side = self.live_side(inst_id)
        ok, msg = self._dispatch(inst_id, live_side, sig)
        if ok:
            notify.log_and_notify("交易动作", f"{inst_id} {msg}", dedup_key=f"act:{inst_id}:{ts}")
        return ok, msg

    def _dispatch(self, inst_id, live_side, sig):
        """根据交易所实时持仓 + 信号方向决定动作。"""
        want = "long" if sig.direction == "bottom" else "short"
        if live_side is None:
            ok, msg = self._preflight_open(inst_id)
            if not ok:
                return False, msg
            ok, msg = self._open(inst_id, want, sig.price)
            if ok and config.REVERSE_ON_SIGNAL:
                return ok, msg
            return ok, msg
        if live_side == want:
            return False, f"已持{'多' if want == 'long' else '空'}仓,同向不动"
        # 反向信号 → 平仓(可配置反手)
        ok, msg = self._close(inst_id, live_side, close_price=sig.price)
        if ok and config.REVERSE_ON_SIGNAL:
            ok2, msg2 = self._open(inst_id, want, sig.price)
            return ok2, msg + " → 反手:" + msg2
        return ok, msg

    # ========== 止损/TP 轮询兜底(方案 b) ==========
    def poll_stop(self):
        """程序端兜底:交易所端 SL 已挂(方案 a),这里兜 TP 与漏挂 SL 的场景。
        以交易所 avgPx 为基准,现价越界即市价平仓。"""
        if config.DRY_RUN:
            return
        try:
            res = okx_cli.get_positions()
            for p in _items(res):
                inst_id = p.get("instId")
                side = p.get("posSide")
                try:
                    avg_px = float(p.get("avgPx") or 0)
                except (TypeError, ValueError):
                    continue
                if not inst_id or not side or avg_px <= 0:
                    continue
                df = market.fetch_candles(inst_id, bar="5m", limit=2)
                if df is None or len(df) == 0:
                    continue
                price = float(df.iloc[-1]["close"])
                sl = self._calc_sl(avg_px, side)
                tp = self._calc_tp(avg_px, side)
                reason = None
                if side == "long":
                    if sl and price <= sl:
                        reason = "硬止损"
                    elif tp and price >= tp:
                        reason = "止盈"
                else:
                    if sl and price >= sl:
                        reason = "硬止损"
                    elif tp and price <= tp:
                        reason = "止盈"
                if reason:
                    ok, msg = self._close(inst_id, side, close_price=price)
                    notify.log_and_notify(reason, f"{inst_id} {msg} 现价={price} avgPx={avg_px}",
                                          dedup_key=f"{reason}:{inst_id}")
        except okx_cli.OkxCliError as e:
            notify.log_and_notify("止损轮询", f"失败: {e}")
