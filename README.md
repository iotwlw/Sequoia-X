# Sequoia-X：A 股量化选股系统

Sequoia-X 是一个 Python 3.10+ 的收盘后批处理应用：它把 baostock 日 K 行情保存到本地
SQLite，依次运行多种选股策略，再把非空结果推送到飞书。定增公告策略另行使用 akshare。

面向零基础的完整源码导学、运行路线和二次开发实战见：
[docs/sequoia-x-guided-learning.html](docs/sequoia-x-guided-learning.html)。

## 三种运行模式

```powershell
uv run python main.py --doctor      # 离线诊断：不访问行情服务，也不发送飞书
uv run python main.py --backfill    # 首次建库或断点续跑：回填历史日 K
uv run python main.py               # 日常模式：增量补数、运行策略、飞书推送
```

建议第一次接触项目时先执行 `--doctor`，确认 Python、`.env`、SQLite 和 baostock 配额状态，
再决定是回填历史数据还是直接运行日常流程。

## Windows PowerShell 快速开始

### 1. 安装依赖

```powershell
Set-Location E:\Code\Stock\Sequoia-X
python --version
uv --version
uv sync --extra dev
```

Python 必须为 3.10 或更高版本。若尚未安装 uv，请先按 uv 官方 Windows 安装说明执行：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

项目运行时依赖包括 pandas、baostock、akshare、pydantic-settings、requests 和 rich；开发依赖
包括 pytest、Hypothesis、pytest-mock 与 Ruff。项目不需要 GPU 或编译 CUDA。

### 2. 创建本地配置

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少把 `FEISHU_WEBHOOK_URL` 换成真实地址。不要提交 `.env` 或 Webhook URL。

### 3. 执行安全诊断

```powershell
uv run python main.py --doctor
```

诊断只读取本地状态，会给出“通过 / 提醒 / 失败”结论。数据库不存在时，它会提示下一步运行
`uv run python main.py --backfill`；数据库过旧时，它会提示运行日常增量。

### 4. 验证开发环境

```powershell
uv run pytest
uv run ruff check .
```

### 5. 首次建库与日常运行

```powershell
uv run python main.py --backfill
uv run python main.py
```

回填和日常运行都会访问外部数据源。耗时取决于股票数量、网络、已有断点和 baostock 服务状态，
不应依赖固定分钟数。回填可重复执行，已是最新的股票会跳过。

## 内置策略

| 策略类 | 作用 |
|---|---|
| `MaVolumeStrategy` | 5 日均线上穿 20 日均线，并配合成交量放大 |
| `TurtleTradeStrategy` | 20 日新高、成交额和阳线过滤，候选按流通市值降序 |
| `HighTightFlagStrategy` | 强动量后的窄幅、缩量整理 |
| `LimitUpShakeoutStrategy` | 涨停后的洗盘回踩确认 |
| `UptrendLimitDownStrategy` | 上升趋势中的放量跌停 / 错杀形态 |
| `RpsBreakoutStrategy` | 全市场横截面 RPS 相对强度突破 |
| `PrivatePlacementStrategy` | 通过 akshare 监控近期定向增发公告 |

所有策略继承 `sequoia_x/strategy/base.py` 中的 `BaseStrategy`，统一实现
`run() -> list[str]`。新增策略通常只需要创建策略文件、在 `main.py` 注册、配置 Webhook，
再补一条不访问真实网络的测试。

## 主线架构

```text
[.env]
  -> main.py / get_settings()
  -> RunLock（用户级单实例）
  -> DataEngine（baostock -> SQLite）
  -> strategy.run()
  -> FeishuNotifier.send()
  -> 飞书 Webhook
```

主要目录：

```text
Sequoia-X/
├── main.py
├── pyproject.toml
├── .env.example
├── docs/
│   └── sequoia-x-guided-learning.html
├── sequoia_x/
│   ├── core/
│   │   ├── config.py
│   │   ├── doctor.py
│   │   ├── logger.py
│   │   ├── run_lock.py
│   │   └── runtime_state.py
│   ├── data/
│   │   ├── baostock_guard.py
│   │   └── engine.py
│   ├── strategy/
│   │   └── *.py
│   └── notify/
│       └── feishu.py
└── tests/
    └── test_*.py
```

## 数据与运行时状态

- 行情库默认位于 `data/sequoia_v2.db`，表 `stock_daily` 以 `(symbol, date)` 唯一。
- 行情价格使用后复权数据，策略主要读取本地 SQLite，而不是每次重新下载全量行情。
- baostock 不允许项目并发连接，且每日查询请求上限为 50,000 次。
- `sequoia_x/core/run_lock.py` 使用操作系统文件锁阻止同一用户启动多个实例。
- `sequoia_x/data/baostock_guard.py` 使用 SQLite 原子事务领取查询额度，并识别黑名单错误码
  `10001011`；配额状态损坏时会保守停止查询，不会把计数归零。
- 锁和配额默认共享在用户目录 `~/.sequoia-x/`。测试或受管部署可用
  `SEQUOIA_X_STATE_DIR` 指向隔离目录，但同一台机器上的生产任务应保持一致。

## 配置

`.env.example` 中的核心参数：

- `DB_PATH`：SQLite 行情库路径。
- `START_DATE`：首次回填的起始日期。
- `FEISHU_WEBHOOK_URL`：默认飞书机器人，必填。
- `STRATEGY_WEBHOOK_<KEY>`：可选的策略专属机器人；缺失时回退到默认地址。
- `SEQUOIA_X_STATE_DIR`：可选的共享锁和配额目录，默认 `~/.sequoia-x/`。

## 开发验证

```powershell
uv run pytest
uv run ruff check .
uv run ruff format .
```

网络、文件系统和 Webhook 测试应使用 mock 或临时目录。尤其不要让测试访问真实 baostock、
akshare 或飞书地址。
