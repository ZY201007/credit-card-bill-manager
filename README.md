# 信用卡账单管理与还款提醒系统（个人项目）

统一管理多张信用卡账单：导入后自动计算账单日、到期日、免息期与应还金额，并在还款日前推送幂等提醒。

- 周期：2025.09 - 2025.11
- 角色：个人独立开发（需求 → 设计 → 实现 → 回归测试）
- 技术栈：Python 3.10+ · SQL · SQLite · 双 GUI（tkinter 桌面版 / 零依赖 Web 版，均仅用标准库）；测试使用 pytest

---

## 1. 项目特性

| 特性 | 说明 |
| --- | --- |
| 多卡统一管理 | 每张卡独立配置账单日、宽限期、最低还款比例/下限 |
| 账单导入去重 | 以「卡号 + 账单月 + 金额(分) + 交易签名」为唯一键，重复导入无副作用 |
| 日期边界处理 | 跨月、跨年、大小月 28/29/30/31、闰年 2 月 29 天自动吸附 |
| 金额分开计算 | 应还金额、最低还款、分期手续费由三个独立纯函数实现，全部使用 `Decimal` |
| 双策略提醒 | 全额提醒（FULL）/ 最低提醒（MINIMUM），各自独立幂等 |
| 已还停推 | 标记还款后不再产生任何新提醒，历史提醒保留作审计 |
| 调度器友好 | 提供 `check_reminders(today)`，可被 cron / Windows 任务计划程序直接调用 |

### 关键成果如何复现

| 简历指标 | 复现位置 | 说明 |
| --- | --- | --- |
| 3 张卡 × 12 个月样例账单回归，金额计算零差错 | `tests/test_regression.py`、`fixtures/expected_bills.csv`、`scripts/generate_fixtures.py` | 期望值由独立参考实现生成（不复用领域代码），逐字段比对 36 期账单的账单日、到期日、各金额分项 |
| 补全额/最低两种提醒策略后，重复推送 3~5 次降为 0 | `tests/test_reminders.py::TestDuplicatePushesDropToZero` | 模拟一天内调度 5 次：朴素逻辑产生 10 次推送；幂等台账后仅新增 2 条（每策略 1 条），重复为 0 |

---

## 2. 计算规则（精确公式）

所有金额以 `Decimal` 运算并按「分」四舍五入（`ROUND_HALF_UP`），杜绝浮点误差。

### 2.1 日期规则

- **账单日** `statement_date(year, month, statement_day)`
  - 当月存在该日号则取该日；不存在则吸附到当月最后一天。
  - 例：`statement_day=31` → 2025-02 账单日为 **2025-02-28**；2024-02（闰年）为 **2024-02-29**；2025-04 为 **2025-04-30**。
- **到期日** `due_date(stmt_date, due_offset_days) = stmt_date + 宽限期天数`
  - 用 `timedelta` 自然跨月/跨年。例：`2025-01-31 + 20 天 = 2025-02-20`；`2025-12-25 + 20 天 = 2026-01-14`。
- **账单周期** `[上月账单日 + 1, 本月账单日]`。
- **交易归属账单月** `statement_month_for_date(txn_date, statement_day)`
  - 交易日 ≤ 账单日 → 当月；交易日 > 账单日 → 下月。例：账单日=5，`2025-01-06` 的交易归入 `2025-02` 账单。
- **免息期** `interest_free_days(交易日, 到期日) = 到期日 - 交易日`（天）
  - 周期最长免息期 = 到期日 - 周期起始日。例：账单日=5、宽限期 20 天 → 周期首日消费（上月 6 日）享受 **50 天**免息期。

### 2.2 金额规则（`cardbill/domain.py`）

```text
分期手续费   = 分期本金 × 每期费率 × 期数                    （四舍五入到分）
每期分期本金 = 分期本金 / 期数                              （四舍五入到分）

应还金额     = 上期余额 + 本期新增消费 + 分期本金 + 分期手续费

最低还款     = max(应还金额 × 最低还款比例, 最低还款下限)      （应还为 0 时为 0）
             默认比例 10%，下限 10.00 元；示例卡 CARD_C 为 5% / 20.00 元
```

> 分期手续费与应还金额、最低还款是三个相互独立的纯函数，便于单独验证与调整规则（见 `tests/test_amounts.py`）。

### 2.3 提醒规则（`cardbill/reminders.py`）

- 触发窗口：`0 <= 到期日 - today <= lead_days`（默认提前 7 天）。
- 策略：`FULL` 推送「应还金额」，`MINIMUM` 推送「最低还款额」。
- 幂等键：`(card_id, billing_cycle, strategy)` 在 `reminders` 表上建立 `UNIQUE` 约束，写入用 `INSERT OR IGNORE`；因此同一期同一策略永远只有一条，多次调度返回空列表。
- 已还停推：`bills.is_paid = 1` 的账单被跳过。

---

## 3. 目录结构

```text
.
├── cardbill/
│   ├── __init__.py       包入口与版本
│   ├── money.py          Decimal 金额工具（零差错基础）
│   ├── domain.py         纯函数：日期与金额规则（核心，可单测）
│   ├── models.py         dataclass 领域模型
│   ├── db.py             SQLite schema 与读写仓库（唯一约束在此）
│   ├── importer.py       CSV 导入与去重
│   ├── service.py        业务编排：交易汇总成账单
│   ├── reminders.py      幂等提醒引擎 check_reminders(today)
│   ├── cli.py            argparse 命令行入口
│   ├── gui.py            tkinter 桌面图形界面（含 --selftest）
│   └── webgui.py         零依赖 Web 图形界面（http.server + 原生 HTML/CSS/JS）
├── tests/                46 个 pytest 用例（边界/金额/去重/幂等/回归）
├── scripts/
│   └── generate_fixtures.py   生成 3 卡 × 12 月样例与期望账单
├── fixtures/
│   └── expected_bills.csv     独立参考实现生成的期望值（36 期）
├── data/sample_bills/
│   ├── CARD_A.csv / CARD_B.csv / CARD_C.csv
├── requirements.txt      仅 pytest（生产代码零第三方依赖）
├── pytest.ini
└── .gitignore
```

---

## 4. 快速开始

```bash
# 1) 环境（Python 3.10+）
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # 仅安装 pytest

# 2) 生成回归夹具（已随项目提供，可跳过）
python scripts/generate_fixtures.py

# 3) 运行测试（应输出 46 passed）
python -m pytest
```

### CLI 命令

数据库默认路径：`data/cardbill.db`，可用全局参数 `--db PATH` 覆盖。

```bash
# 初始化
python -m cardbill.cli init-db

# 添加卡片（账单日=5，到期宽限期=20 天）
python -m cardbill.cli add-card --id CARD_A --name "招商银行经典白金卡" \
    --statement-day 5 --due-offset-days 20 --min-rate 0.10 --min-floor 10.00

# 导入样例（重复执行不会重复入账）
python -m cardbill.cli import --file data/sample_bills/CARD_A.csv

# 由交易重算账单
python -m cardbill.cli rebuild-bills

# 查看账单
python -m cardbill.cli list-bills

# 检查今日提醒（可指定日期，便于回溯/演练）
python -m cardbill.cli check-reminders                       # 系统当天
python -m cardbill.cli check-reminders --today 2025-02-18 --lead-days 7

# 标记已还（此后停止提醒）
python -m cardbill.cli mark-paid CARD_A 2025-02
```

典型输出：

```text
[新提醒] CARD_A 周期=2025-02 策略=FULL 金额=6511.26 距到期=7天
[新提醒] CARD_A 周期=2025-02 策略=MINIMUM 金额=651.13 距到期=7天
# 再次运行同一天：
今日无新提醒
```

---

## 5. 图形界面（GUI）

项目提供**两种可操作的图形界面**，功能一一对应，全部复用既有领域逻辑（db / importer / service / reminders / domain），**不重写任何业务计算**，且与 CLI 共用同一个 SQLite 数据库：

| 界面 | 启动方式 | 适用场景 |
| --- | --- | --- |
| 桌面版（tkinter） | `python -m cardbill.gui` | 本机装有完整标准库（python.org 官方安装包）、希望使用原生窗口的常规桌面环境 |
| Web 备选版（零依赖） | `python -m cardbill.webgui` | 精简版 / 嵌入式 Python 缺少 `tkinter`，或需要通过浏览器远程访问的环境 |

> 两者是「同一套业务的两种呈现」：数据文件相同、操作结果互通，可随时切换。

### 5.1 桌面版（tkinter）

```bash
# 在项目根目录下（推荐）
python -m cardbill.gui

# 等价的直接运行方式
python cardbill/gui.py

# 指定数据库
python -m cardbill.gui --db data/cardbill.db
```

> 需要带 `tkinter` 的 Python：python.org 官方 Windows 安装包默认包含 tkinter；若使用精简/嵌入式发行版缺少该标准库模块，桌面版无法启动，请改用下面的 Web 版（或在无界面环境使用 CLI）。

#### 界面功能

窗口分为 4 个页签 + 底部统一日志区，所有操作结果都会在表格或日志文本区给出反馈：

| 页签 | 可操作功能 |
| --- | --- |
| ① 卡片管理 | 填写卡号、卡名、账单日、宽限期、最低还款比例/下限并「添加卡片」；下方表格展示已有卡片。重复卡号与非法参数会被拒绝并提示。 |
| ② 账单导入 | 「浏览...」选择 CSV 或手动输入路径，点击「导入 CSV」；结果区展示总行数、新增、去重、跳过数量，并自动重算账单。 |
| ③ 账单查询 | 按卡片下拉框 / 账单月筛选，「查询账单」表格展示账单日、到期日、最长免息期、应还、最低还款、手续费与还款状态；支持「重算账单」和「标记选中账单已还」（双击行也可带入）。 |
| ④ 还款提醒 | 输入日期（留空为系统当天）与提前天数，「检查今日提醒」展示 FULL/MINIMUM 两种策略的新提醒；重复点击幂等；「标记已还」后该期停推；下方为审计用提醒台账。 |

底部「操作日志」按时间记录每次点击的结果，状态栏显示当前操作摘要；任何异常都会被捕获并显示在日志中，不会让界面无响应。

#### 无显示器环境下自测

GUI 逻辑可在不弹出窗口的情况下验证（无需 tkinter）：

```bash
python -m cardbill.gui --selftest
```

该模式注入轻量 mock 控件，构造真实 `App` 并逐个触发所有按钮回调，覆盖添加卡片、去重导入、账单查询、双策略提醒、幂等、标记已还停推等 25 项检查，全部通过会输出 `GUI selftest: 25 项全部通过。`。

### 5.2 Web 备选版（零依赖，无需 tkinter）

```bash
# 默认端口 8766
python -m cardbill.webgui

# 明确端口 / 监听地址 / 数据库
python -m cardbill.webgui --port 8766 --host 127.0.0.1 --db data/cardbill.db
```

启动后在浏览器打开 **http://127.0.0.1:8766** 即可操作，按 `Ctrl+C` 停止服务。默认只监听 `127.0.0.1`，不暴露到局域网。

技术构成：Python 标准库 `http.server` 提供静态首页与 JSON API，前端为单文件原生 HTML/CSS/JS（用 `fetch` 调用 API）——**不引入 Flask 等任何第三方依赖，也不导入 `tkinter`**，因此在缺少 tkinter 的精简 Python 环境中同样能真实启动并通过浏览器操作。

#### 界面功能与 API 对应

页面自上而下四个区块，与桌面版四个页签一一对应，每个操作都有结果区 / 操作日志反馈：

| 区块 | 可操作功能 | 对应 API |
| --- | --- | --- |
| ① 卡片管理 | 填写卡号、卡名、账单日、宽限期、最低还款比例/下限并「添加卡片」；表格列出已有卡片 | `GET /api/cards`、`POST /api/cards` |
| ② 账单导入 | 下拉选择样例文件或填写路径后「导入 CSV」；展示总行数/新增/去重/跳过并自动重算 | `GET /api/sample-files`、`POST /api/import`、`POST /api/rebuild` |
| ③ 账单查询 | 按卡片 / 账单月筛选，表格展示账单日、到期日、最长免息期、应还、最低还款、手续费、状态，并可直接「标记已还」 | `GET /api/bills` |
| ④ 还款提醒 | 输入日期与提前天数「检查今日提醒」（FULL/MINIMUM 双策略、幂等），「标记已还」后停推，下方为审计台账 | `POST /api/reminders/check`、`POST /api/reminders/mark-paid`、`GET /api/reminders` |

辅助 API：`GET /api/status`（数据库路径 / 版本）。所有响应均为 UTF-8 JSON；业务失败返回 HTTP 400 并带 `error` 说明，未知路由返回 404。

#### 实测验证

在本机 Python 3.13 精简版（无 tkinter）启动 `python -m cardbill.webgui --port 8766`，用 curl 验证：首页 `GET /` 返回 200（`text/html; charset=utf-8`，15 KB）；完整链路「添加卡片 → 导入 CSV → 查询账单 → 检查提醒（2 条 FULL/MINIMUM）→ 同日再次检查（幂等 0 条）→ 标记已还 → 账单状态变「已还」→ 再查提醒 0 条」全部返回 200；重复卡号 / 非法参数 / 文件不存在 / 非法 JSON / 未知路由分别正确返回 400/404；服务日志无异常堆栈。

---

## 6. CSV 导入格式

首行为表头，列顺序固定：

```csv
card_id,txn_date,amount,description,signature
CARD_A,2025-01-03,123.45,超市购物,TXN-CARD_A-01-1
CARD_A,2025-01-05,103.00,上期结转,__PREV_BALANCE__
CARD_A,2025-02-05,6020.00,"0.006,6",__INSTALLMENT__
```

- `txn_date`：`YYYY-MM-DD`；`amount`：定点小数；`signature`：交易唯一签名。
- **去重键**：`card_id + 归属账单月 + amount(到分) + signature`，由 SQLite `UNIQUE(dedup_key)` 保证；同键重复行被静默忽略并计入 `duplicates`。

### 保留签名（用于把账单级信息随交易一起导入）

| signature | 含义 | amount / description |
| --- | --- | --- |
| `__PREV_BALANCE__` | 上期余额 | `amount` 即上期余额 |
| `__INSTALLMENT__` | 分期本金 | `amount` 为本金，`description` 格式为 `每期费率,期数`（如 `0.006,6`），手续费由 `domain.installment_fee` 计算 |
| `__INSTALLMENT_FEE__` | 直接给定的分期手续费 | `amount` 即手续费 |

普通签名一律计入「本期新增消费」。

---

## 7. 提醒调度（cron / 任务计划程序）

调度器只需每天调用一次 `check_reminders`；幂等由数据库保证，重复调用不会产生重复推送。

### Linux / macOS cron

```cron
0 9 * * * cd /path/to/信用卡账单管理与还款提醒系统 && /path/to/.venv/bin/python -m cardbill.cli check-reminders >> /var/log/cardbill.log 2>&1
```

### Windows 任务计划程序

- 程序：`D:\...\信用卡账单管理与还款提醒系统\.venv\Scripts\python.exe`
- 参数：`-m cardbill.cli check-reminders`
- 起始位置：项目根目录
- 触发器：每天 09:00

### Python API（供已有调度框架集成）

```python
from datetime import date
from cardbill.db import Database
from cardbill.reminders import check_reminders

db = Database("data/cardbill.db")
db.init_schema()
new = check_reminders(db, date.today(), lead_days=7)
for r in new:  # 只返回本次新产生的提醒，直接推送即可
    print(r.card_id, r.billing_cycle, r.strategy, r.amount)
db.close()
```

---

## 8. SQLite Schema

```sql
CREATE TABLE cards (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    statement_day INTEGER NOT NULL, due_offset_days INTEGER NOT NULL,
    min_payment_rate TEXT NOT NULL, min_payment_floor TEXT NOT NULL,
    currency TEXT NOT NULL
);

CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL, txn_date TEXT NOT NULL, amount TEXT NOT NULL,
    signature TEXT NOT NULL, description TEXT NOT NULL,
    statement_month TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,          -- 导入去重
    FOREIGN KEY (card_id) REFERENCES cards(id)
);

CREATE TABLE bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL, billing_month TEXT NOT NULL,
    statement_date TEXT NOT NULL, due_date TEXT NOT NULL,
    previous_balance TEXT NOT NULL, new_transactions TEXT NOT NULL,
    installment_amount TEXT NOT NULL, installment_fee TEXT NOT NULL,
    total_due TEXT NOT NULL, minimum_payment TEXT NOT NULL,
    is_paid INTEGER NOT NULL DEFAULT 0, paid_at TEXT,
    UNIQUE (card_id, billing_month),         -- 一期账单只汇总一次
    FOREIGN KEY (card_id) REFERENCES cards(id)
);

CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL, billing_cycle TEXT NOT NULL,
    strategy TEXT NOT NULL, due_date TEXT NOT NULL,
    amount TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (card_id, billing_cycle, strategy) -- 提醒幂等（3~5 -> 0 的关键）
);
```

> 金额均以定点字符串存储（如 `"6511.26"`），读出后转回 `Decimal`，全链路无损。

---

## 9. 测试覆盖

```text
tests/test_domain.py       日期边界：跨月/跨年、大小月、闰年、账单周期、免息期
tests/test_amounts.py      金额：Decimal 精度、分期手续费、应还金额、最低还款
tests/test_importer.py     去重：重复导入、同周期同签名、不同签名、账单月归属
tests/test_reminders.py    双策略、幂等、已还停推、提醒窗口、3~5 -> 0 重复推送
tests/test_regression.py   3 卡 × 12 月金额零差错、重复导入后账单稳定
cardbill/gui.py --selftest  桌面 GUI 25 项回调自测（mock 控件，无需弹窗）
cardbill/webgui.py + curl   Web GUI 实测：首页 200 + 完整业务链路 API + 错误码
```

运行：`python -m pytest`（原有 46 个用例全部通过）。
