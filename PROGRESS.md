# 项目进度 / 跨会话交接说明

> 本文件用于跨会话交接。**下次继续时先读这里**,再读 README.md 和代码。

## 一、项目是什么

均线拐点(MA13/MA35)全自动交易机器人,接入 OKX AI Builder Program 拿返佣(申请即 35%、最高 50%)。

- **本地路径**:`F:\okx-ma-turnpoint-bot`
- **GitHub**:https://github.com/IAMBANMA/okx-ma-turnpoint-bot
- **源策略**:移植自 `F:\交易\OKX_5M_SlopeZero_Monitor.py` 的拐点信号(`check_turn_point`),原系统是纯信号监控、不下单。

## 二、当前进度(截至 2026-09-18)

### 已完成 ✅
- **Phase 0** 脚手架:8 个 py 模块 + README + .env + logo + GitHub 建仓推送
- **Phase 1** 下单引擎:`okx_cli.py` 封装 `okx swap place/close` + `--aiBuilderCode` 归因注入
- **Phase 2** 信号接入:`signal.py` 拐点检测 + `engine.py` 动作映射(去重/MA35优先/冲突跳过)
- **Phase 3** 风控:硬止损双保险(交易所端 SL + 程序轮询)、总亏损熔断、余额检查
- **附加**:项目 logo(争鸣投资)、追踪止损(`TRAIL_PCT`)、1h 趋势过滤

### 阻塞中 ⚠️(下次从这里继续)
**Phase 4 模拟盘验证**卡在两个前置条件:
1. **OAuth 登录失败** —— 会话过期,且本次重新授权用户侧失败,需重新 `okx auth login`
2. **AI Builder Code 未申请到** —— 需到 https://www.okx.com/agent-tradekit/builder 申请,填 `.env` 的 `OKX_AI_BUILDER_CODE=`

### 未开始 ⏳
- Phase 5 实盘(需补 `live:trade` 授权 + Code)
- 可选优化:③止损按 ATR 自适应、④震荡过滤(ADX)、严格历史回测(趋势过滤用历史时点数据)

## 三、下一步操作(按顺序)

1. `okx auth login`(或 `okx auth login --manual --site global`)重新授权,确认 scope 含 **demo:trade**
2. 申请 Builder Code 填 `.env` 的 `OKX_AI_BUILDER_CODE=`
3. `python main.py --selftest` 验证模拟盘下单链路(已改:demo 下允许空 Code 下单,订单不带归因)
4. `python main.py` 挂机验证「拐点信号 → 下单 → 归因」全链路
5. 实盘前:重新授权补 `live:trade` + 核对 `settleCcy` 保证金币种

## 四、关键账号/环境信息(已实测,勿重复调查)

- **okx 账户**:`posMode=net_mode`(净仓,下单**不带 posSide**);`settleCcy=USDC`(注意保证金币种,不是 USDT)
- **OAuth scope**:有 `demo:trade`、**无 `live:trade`**(实盘前必须补授权)
- **GitHub**:账号 `IAMBANMA`;`git push` 需走 Clash 代理(仓库已配 `http.proxy=127.0.0.1:7890`)
- **gh CLI**:已装 2.101.0,软链到 `/c/nvm4w/nodejs/gh.exe`(winget 装的原位置不在 PATH)
- **git 身份**:本仓库本地配置 `User <user@example.com>`(与 F:\交易 一致)

## 五、okx CLI 关键坑(已适配进代码,勿回退)

- Windows 下 `okx` 是 `.cmd` shim,`subprocess` 需经 `cmd /c` 执行(否则 FileNotFoundError)
- `--json` 输出是**解包数组**(不是 `{"code":"0","data":[...]}` 包装);失败时纯文本 `Error: ... Code: 51001` 且 rc 非 0
- 下单命令是 `okx swap place`(不是 `okx trade order`);`market instruments` 需 `--instType SWAP`
- `--sz` 用 `--tgtCcy margin` 才是保证金 USDT(默认是张数)
- `--slOrdPx=-1` 必须等号写法
- 归因参数 `--aiBuilderCode <code>`(camelCase,1-16 位)已确认存在于 place/close 命令

## 六、策略逻辑速览

- **信号**:5m MA13/MA35 斜率拐点(见顶回落=偏空 / 见底回升=偏多),单根斜率进近零带 + 此前有真实单边趋势
- **入场**:空仓 + 拐点 → 开多/开空;先过 **1h 趋势过滤**(只做顺趋势);MA35 优先、MA13 补充、方向冲突跳过
- **离场三层**:①硬止损 `SL_PCT=2%`(交易所端挂单)→ ②追踪止损 `TRAIL_PCT=1.5%`(极值回撤)→ ③反向拐点
- **风控**:固定保证金 100U×3 杠杆、最多 3 仓、余额检查(≥100U×1.2)、总亏损熔断 300U 自动暂停
- **配置**:全在 `.env`(敏感信息 Code/webhook 不进 git)

## 七、文件结构

| 文件 | 作用 |
|---|---|
| `config.py` | 加载 .env,集中所有常量 |
| `market.py` | 行情获取(公开 API,代理+直连降级) |
| `signal.py` | 均线计算 + 拐点检测 |
| `okx_cli.py` | okx CLI 封装(place/close/leverage/查询 + 归因注入) |
| `state_store.py` | 持仓状态机 + JSON 原子持久化(`state/state.json`) |
| `engine.py` | 信号→动作映射、风控护栏、硬止损、熔断、追踪止损、趋势过滤 |
| `notify.py` | 钉钉通知 |
| `main.py` | 入口:`run` / `--selftest` / `--backtest` |

## 八、验证命令备忘

```bash
python main.py --backtest     # 历史回放(DRY_RUN,不下单,验证信号→动作映射)
python main.py --selftest     # 模拟盘最小仓下单→成交→平仓(验证下单链路)
python main.py                # 正常运行(DRY_RUN/DEMO 由 .env 控制)
okx auth status --json        # 看登录状态与 scope
okx swap orders --instId BTC-USDT-SWAP --json   # 核对订单是否带 aiBuilderCode
```
