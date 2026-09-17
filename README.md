# OKX 均线拐点自动交易机器人（OKX MA Turnpoint Bot）

基于 **MA13/MA35 均线斜率拐点**的全自动交易机器人，接入 [OKX AI Builder Program](https://www.okx.com/zh-hans/help/ai-builder-program-integration-guide) 获得返佣归因（申请即 35%、最高 50%）。

> ⚠️ **免责声明**：这是**全自动实盘交易**程序，可能造成资金损失。请在模拟盘充分验证后再考虑实盘，盈亏自负。本程序不构成任何投资建议。

## 核心逻辑

- **信号源**：OKX USDT-SWAP 永续合约 5 分钟 K 线，检测 MA13/MA35 均线斜率的「拐点」——单根斜率进入近零带、且此前有真实单边趋势（沿用斜率归零监控系统的 `check_turn_point` 判定）。
  - `bottom`（见底回升，偏多）→ 开多
  - `top`（见顶回落，偏空）→ 开空
- **平仓**：反向拐点信号平仓；开仓时附带交易所端硬止损（`--slTriggerPx`），程序侧另做止损/止盈轮询兜底。
- **仓位**：每单固定保证金（`--tgtCcy margin`），实际仓位 = 保证金 × 杠杆。
- **归因**：每笔下单强制注入 `--aiBuilderCode <你的码>`，用于 AI Builder 返佣归因。

## 文件结构

```
├── config.py        # 加载 .env，集中所有配置
├── market.py        # 行情获取（OKX 公开 API，走代理+直连降级）
├── signal.py        # 均线计算 + 拐点检测
├── okx_cli.py       # okx CLI 封装（place/close/leverage/查询，注入 --aiBuilderCode）
├── state_store.py   # 持仓状态机 + JSON 原子持久化
├── engine.py        # 信号→动作映射、风控护栏、硬止损、熔断
├── notify.py        # 钉钉通知
└── main.py          # 入口（run / --selftest / --backtest）
```

## 快速开始

### 1. 安装依赖

```bash
# okx CLI（下单/查询用，凭证由 OAuth 存本地，代码零密钥）
npm install -g @okx_ai/okx-trade-cli
okx auth login          # 首次登录：浏览器 OAuth 授权（模拟盘需 demo:trade，实盘需 live:trade）

# Python 依赖
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env   # 然后编辑 .env 填入你的值
```

关键配置见下表。

### 3. 运行

```bash
python main.py --selftest    # 先验证下单 + 归因链路（demo 最小仓）
python main.py --backtest    # 历史回放验证信号→动作映射（不下单）
python main.py               # 正常运行（DRY_RUN/DEMO 由 .env 控制）
```

## 配置项（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `OKX_AI_BUILDER_CODE` | 空 | **必填**，AI Builder 归因码（1-16 位字母数字），申请 AI Builder Program 后从控制台拿 |
| `OKX_DEMO` | `true` | `true`=模拟盘 / `false`=实盘 |
| `DRY_RUN` | `true` | `true`=只打日志不真下单 / `false`=真下单 |
| `SYMBOLS` | 空 | 监控合约，逗号分隔（如 `BTC-USDT-SWAP,ETH-USDT-SWAP`） |
| `TRADE_WHITELIST` | 空 | 交易白名单（空=全部 SYMBOLS 可交易） |
| `MARGIN_PER_TRADE` | `100` | 每单固定保证金（USDT） |
| `LEVERAGE` | `3` | 杠杆倍数 |
| `TD_MODE` | `cross` | `cross` 全仓 / `isolated` 逐仓 |
| `SL_PCT` | `0.02` | 硬止损百分比（2%） |
| `TP_PCT` | `0` | 止盈百分比（0=关闭，让趋势跑） |
| `REVERSE_ON_SIGNAL` | `false` | 反向信号是否反手（默认只平仓） |
| `MAX_OPEN_POSITIONS` | `3` | 同时最大持仓合约数 |
| `MAX_TOTAL_LOSS` | `300` | 总亏损熔断阈值（USDT） |
| `DING_WEBHOOK` / `DING_KEYWORD` | 空 | 钉钉通知（不填则只打日志） |

均线/拐点参数 `MA_MID=13` / `MA_LONG=35` / `TURN_LOOKBACK=5` / `TURN_MIN=0.08` / `TURN_BAND=0.03` 沿用斜率归零监控系统的实测值，**别凭感觉改**。

## 从模拟盘到实盘

1. **申请 AI Builder Code**：到 [AI Builder 控制台](https://www.okx.com/agent-tradekit/builder) 申请，填 `OKX_AI_BUILDER_CODE`。
2. **模拟盘验证**：`OKX_DEMO=true`，`DRY_RUN=false`，`MARGIN_PER_TRADE=10~20` 小仓，跑真实拐点信号，确认「拐点→下单→归因」全链路。
3. **核对归因**：`okx swap orders --instId <id> --json` 看订单是否带 `aiBuilderCode`；再到 AI Builder 控制台确认出现归因成交/返佣。
4. **重新授权实盘**：`okx auth login` 补齐 `live:trade` 权限（首次 OAuth 默认可能只有 `demo:trade`，用 `okx auth status --json` 核对 scope）。
5. **实盘**：`OKX_DEMO=false`，先 1-2 个合约白名单、最小仓验证，监控 24-48h。

## 风控与安全

- **杀开关**：在 `state/` 下创建 `stop_trading` 文件（或把 `state.json` 的 `paused` 置 `true`）立即停止新开仓。
- **防重复开仓**：同一根 K 线信号只处理一次（`last_signal_ts` 持久化，跨重启有效）；下单前以交易所实时持仓为准对账。
- **硬止损双保险**：交易所端 `--slTriggerPx`（不依赖程序存活）+ 程序侧轮询兜底。
- **限频**：下单间隔 ≥0.15s（远低于 OKX 60 单/2s 上限）。
- **敏感信息**：AI Builder Code、钉钉 webhook 只放 `.env`（已 gitignore），**严禁**贴进 commit/issue/截图。

## 已知注意事项

- 下单 `--sz` 用 `--tgtCcy margin`（保证金 USDT），不是张数；实际仓位 = 保证金 × 杠杆。
- `--posSide` 是否必填取决于账户持仓模式（`okx account config` 的 `posMode`：`long_short_mode` 对冲必填 / `net_mode` 净仓省略），程序启动时自动探测。
- Windows 下若控制台中文乱码/崩溃，程序顶部已做 GBK→UTF-8 修复。
- 行情走代理 `127.0.0.1:7890`（Clash），失败自动降级直连；okx CLI 自身网络走本机已配置的代理。

## 许可

MIT
