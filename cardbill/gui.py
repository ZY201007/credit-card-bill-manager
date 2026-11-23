"""信用卡账单管理与还款提醒系统 —— tkinter 桌面图形界面。

设计原则：本模块只负责「界面呈现 + 交互」，所有计算与存储都复用既有领域逻辑
（db / importer / service / reminders / domain），不重写任何业务规则。

启动方式：::

    python -m cardbill.gui              # 推荐
    python cardbill/gui.py              # 直接运行亦可
    python -m cardbill.gui --selftest   # 无显示器环境下验证按钮回调逻辑
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from decimal import InvalidOperation
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "cardbill.db"

# 兼容两种启动方式：python -m cardbill.gui 与 python cardbill/gui.py
if __package__:
    from .db import Database
    from .domain import cycle_max_interest_free_days
    from .importer import ImportStats, import_csv
    from .models import CreditCard
    from .money import D
    from .reminders import check_reminders
    from .service import rebuild_bills
else:  # pragma: no cover - 仅在直接执行脚本时触发
    sys.path.insert(0, str(PROJECT_ROOT))
    from cardbill.db import Database
    from cardbill.domain import cycle_max_interest_free_days
    from cardbill.importer import ImportStats, import_csv
    from cardbill.models import CreditCard
    from cardbill.money import D
    from cardbill.reminders import check_reminders
    from cardbill.service import rebuild_bills


def _fmt(value) -> str:
    """金额统一格式化到分。"""
    return f"{value:.2f}"


def _strategy_name(code: str) -> str:
    return {"FULL": "全额 FULL", "MINIMUM": "最低 MINIMUM"}.get(code, code)


class CardBillController:
    """界面与领域逻辑之间的薄封装。

    每个 ``handle_*`` 方法对应界面上的一个按钮动作：读取输入 -> 调用既有领域函数
    -> 返回「结构化数据 + 人类可读反馈」。GUI 层只负责把返回值画到表格/文本区，
    因此这些方法可以在无显示器的 ``--selftest`` 模式下被直接验证。
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = str(db_path)
        self._db: Database | None = None

    @property
    def db(self) -> Database:
        if self._db is None:
            parent = Path(self.db_path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
            self._db = Database(self.db_path)
            self._db.init_schema()
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # ---------------------------------------------------------------- 卡片管理
    def handle_add_card(
        self,
        card_id: str,
        name: str,
        statement_day: int,
        due_offset_days: int,
        min_rate: str = "0.10",
        min_floor: str = "10.00",
    ) -> str:
        """按钮「添加卡片」的业务核心。"""
        card_id = (card_id or "").strip()
        name = (name or "").strip()
        if not card_id or not name:
            return "添加失败：卡号与卡名均不能为空。"
        try:
            statement_day = int(statement_day)
            due_offset_days = int(due_offset_days)
        except (TypeError, ValueError):
            return "添加失败：账单日与宽限期必须是整数。"
        if not 1 <= statement_day <= 31:
            return "添加失败：账单日必须在 1~31 之间。"
        if due_offset_days < 0:
            return "添加失败：宽限期不能为负数。"
        try:
            rate = str(D(str(min_rate)))
            floor = str(D(str(min_floor)))
        except (InvalidOperation, ValueError):
            return "添加失败：最低还款比例/下限必须是合法数字。"
        if self.db.get_card(card_id) is not None:
            return f"添加失败：卡号 {card_id} 已存在。"
        self.db.add_card(
            CreditCard(
                id=card_id,
                name=name,
                statement_day=statement_day,
                due_offset_days=due_offset_days,
                min_payment_rate=rate,
                min_payment_floor=floor,
            )
        )
        return f"已添加卡片：{card_id} {name}（账单日 {statement_day} 号，宽限期 {due_offset_days} 天）。"

    def handle_list_cards(self) -> list[dict]:
        """按钮「刷新卡片列表」的业务核心。"""
        return [
            {
                "id": c.id,
                "name": c.name,
                "statement_day": c.statement_day,
                "due_offset_days": c.due_offset_days,
                "min_payment_rate": c.min_payment_rate,
                "min_payment_floor": c.min_payment_floor,
                "currency": c.currency,
            }
            for c in self.db.list_cards()
        ]

    # ---------------------------------------------------------------- 账单导入
    def handle_import(self, csv_path: str | Path) -> tuple[ImportStats, str]:
        """按钮「导入 CSV」的业务核心：导入后自动重算账单。"""
        path = Path(str(csv_path).strip().strip('"'))
        if not path.is_file():
            return ImportStats(), f"导入失败：找不到文件 {path}。"
        stats: ImportStats = import_csv(self.db, path)
        bill_count = len(rebuild_bills(self.db))
        feedback = (
            f"导入完成【{path.name}】：总行 {stats.total_rows}，新增 {stats.imported}，"
            f"重复 {stats.duplicates}，跳过 {stats.skipped}；已自动重算 {bill_count} 期账单。"
        )
        for err in stats.errors or []:
            feedback += f"\n  ! {err}"
        return stats, feedback

    def handle_rebuild(self) -> tuple[int, str]:
        """按钮「重算账单」的业务核心。"""
        bills = rebuild_bills(self.db)
        return len(bills), f"已由交易重算 {len(bills)} 期账单（保留原有已还状态）。"

    # ---------------------------------------------------------------- 账单查询
    def handle_query_bills(
        self, card_id: str | None = None, month: str | None = None
    ) -> list[dict]:
        """按钮「查询账单」的业务核心：按卡片/月份筛选并补全免息期。"""
        card_id = (card_id or "").strip() or None
        month = (month or "").strip() or None
        if card_id == "全部":
            card_id = None
        if month == "全部":
            month = None
        rows: list[dict] = []
        for bill in self.db.list_bills():
            if card_id and bill.card_id != card_id:
                continue
            if month and bill.billing_month != month:
                continue
            card = self.db.get_card(bill.card_id)
            free_days = 0
            if card is not None:
                free_days = cycle_max_interest_free_days(
                    bill.statement_date, card.statement_day, bill.due_dt
                )
            rows.append(
                {
                    "card_id": bill.card_id,
                    "billing_month": bill.billing_month,
                    "statement_date": bill.statement_date.isoformat(),
                    "due_date": bill.due_dt.isoformat(),
                    "free_days": free_days,
                    "total_due": _fmt(bill.total_due_amount),
                    "minimum_payment": _fmt(bill.minimum_payment_amount),
                    "installment_fee": _fmt(bill.installment_fee_amount),
                    "paid": "已还" if bill.is_paid else "未还",
                }
            )
        return rows

    # ---------------------------------------------------------------- 还款提醒
    def handle_check_reminders(
        self, today_str: str = "", lead_days: int = 7
    ) -> tuple[list[dict], str]:
        """按钮「检查今日提醒」的业务核心：幂等，已还的不再返回。"""
        today = date.fromisoformat(today_str.strip()) if today_str.strip() else date.today()
        reminders = check_reminders(self.db, today, lead_days=int(lead_days))
        rows = [
            {
                "card_id": r.card_id,
                "billing_cycle": r.billing_cycle,
                "strategy": _strategy_name(r.strategy),
                "amount": _fmt(r.amount),
                "days_to_due": r.days_to_due,
                "due_date": r.due_dt.isoformat(),
            }
            for r in reminders
        ]
        if rows:
            feedback = f"检查日期 {today.isoformat()}：本次新增 {len(rows)} 条提醒（FULL/MINIMUM 各独立幂等）。"
        else:
            feedback = (
                f"检查日期 {today.isoformat()}：今日无新提醒——"
                "要么是窗口内无到期账单，要么已推送过/已标记已还（幂等生效）。"
            )
        return rows, feedback

    def handle_mark_paid(self, card_id: str, billing_month: str) -> str:
        """按钮「标记已还」的业务核心：此后该期不再产生提醒。"""
        card_id = (card_id or "").strip()
        billing_month = (billing_month or "").strip()
        if not card_id or not billing_month:
            return "标记失败：请填写卡号与账单月（YYYY-MM）。"
        ok = self.db.mark_paid(
            card_id, billing_month, datetime.now().isoformat(timespec="seconds")
        )
        return (
            f"已标记 {card_id} {billing_month} 为已还，此后停止提醒。"
            if ok
            else f"标记失败：未找到账单 {card_id} {billing_month}。"
        )

    def handle_list_reminders(self) -> list[dict]:
        """按钮「刷新提醒台账」的业务核心。"""
        return [
            {
                "card_id": r["card_id"],
                "billing_cycle": r["billing_cycle"],
                "strategy": _strategy_name(r["strategy"]),
                "due_date": r["due_date"],
                "amount": _fmt(D(r["amount"])),
                "created_at": r["created_at"],
            }
            for r in self.db.list_reminders()
        ]


def _build_app():
    """构造 tkinter 应用；单独抽出便于 --selftest 不触发任何窗口创建。"""
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    class App(tk.Tk):
        """主窗口：4 个功能页签 + 底部统一日志/状态区。"""

        def __init__(self, db_path: str = DEFAULT_DB):
            super().__init__()
            self.controller = CardBillController(db_path)
            self._card_rows: list[dict] = []
            self._bill_rows: list[dict] = []
            self.title("信用卡账单管理与还款提醒系统")
            self.geometry("1120x800")
            self.minsize(980, 700)
            self._init_style()
            self._build_widgets()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(100, self.refresh_all)

        def _init_style(self) -> None:
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure(".", font=("Microsoft YaHei UI", 10))
            style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
            style.configure("TButton", padding=4)
            style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))

        # ---------------------------------------------------------- 控件工具
        def _tree(self, parent, columns: list[tuple[str, str, int]]):
            """columns: [(字段名, 表头, 宽度), ...]，返回带滚动条的 Treeview。"""
            frame = ttk.Frame(parent)
            frame.pack(fill="both", expand=True, padx=8, pady=6)
            names = [c[0] for c in columns]
            tree = ttk.Treeview(frame, columns=names, show="headings", height=9)
            for name, heading, width in columns:
                tree.heading(name, text=heading)
                tree.column(name, width=width, anchor="center", stretch=True)
            vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")
            return tree

        def _status(self, text: str) -> None:
            self.var_status.set(text)

        def _log(self, text: str) -> None:
            stamp = datetime.now().strftime("%H:%M:%S")
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", f"[{stamp}] {text}\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")

        def _safe(self, fn, *args, **kwargs):
            """统一捕获异常，保证任何按钮都不会让界面无响应。"""
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 界面层兜底
                self._log(f"[错误] {type(exc).__name__}: {exc}")
                self._status(f"操作失败：{exc}")
                return None

        # ---------------------------------------------------------- 布局
        def _build_widgets(self) -> None:
            self.var_status = tk.StringVar(value="就绪")
            self.var_db = tk.StringVar(value=self.controller.db_path)
            self.var_card_id = tk.StringVar()
            self.var_card_name = tk.StringVar()
            self.var_stmt_day = tk.StringVar(value="5")
            self.var_due_offset = tk.StringVar(value="20")
            self.var_min_rate = tk.StringVar(value="0.10")
            self.var_min_floor = tk.StringVar(value="10.00")
            self.var_csv_path = tk.StringVar()
            self.var_query_card = tk.StringVar(value="全部")
            self.var_query_month = tk.StringVar()
            self.var_today = tk.StringVar()
            self.var_lead_days = tk.StringVar(value="7")
            self.var_paid_card = tk.StringVar()
            self.var_paid_month = tk.StringVar()

            # 顶部：数据库信息
            top = ttk.Frame(self, padding=(10, 8))
            top.pack(fill="x")
            ttk.Label(top, text="数据库：").pack(side="left")
            ttk.Entry(top, textvariable=self.var_db, width=70, state="readonly").pack(
                side="left", padx=(0, 8)
            )
            ttk.Button(top, text="刷新全部", command=lambda: self._safe(self.refresh_all)).pack(
                side="left", padx=(0, 8)
            )
            ttk.Button(top, text="退出", command=self.on_close).pack(side="left")

            # 四个功能页签
            nb = ttk.Notebook(self)
            nb.pack(fill="both", expand=True, padx=10, pady=6)
            self.nb = nb
            self._build_cards_tab(nb)
            self._build_import_tab(nb)
            self._build_query_tab(nb)
            self._build_reminder_tab(nb)

            # 底部状态 + 日志
            bottom = ttk.Frame(self, padding=(10, 4))
            bottom.pack(fill="both")
            ttk.Label(bottom, textvariable=self.var_status, foreground="#1a6f4a").pack(
                anchor="w"
            )
            self.txt_log = ScrolledText(bottom, height=7, font=("Consolas", 10))
            self.txt_log.pack(fill="both", expand=True, pady=(4, 0))
            self.txt_log.configure(state="disabled")

        def _build_cards_tab(self, nb) -> None:
            tab = ttk.Frame(nb, padding=6)
            nb.add(tab, text="① 卡片管理")

            form = ttk.LabelFrame(tab, text="新增信用卡", padding=10)
            form.pack(fill="x", pady=(0, 8))
            fields = [
                ("卡号 / ID", self.var_card_id, 18),
                ("卡片名称", self.var_card_name, 22),
                ("账单日(1-31)", self.var_stmt_day, 12),
                ("宽限期(天)", self.var_due_offset, 12),
                ("最低比例", self.var_min_rate, 12),
                ("最低下限", self.var_min_floor, 12),
            ]
            for i, (label, var, width) in enumerate(fields):
                row, col = divmod(i, 3)
                cell = ttk.Frame(form)
                cell.grid(row=row, column=col, sticky="w", padx=(0, 18), pady=6)
                ttk.Label(cell, text=label).pack(anchor="w")
                ttk.Entry(cell, textvariable=var, width=width).pack(anchor="w", pady=(2, 0))
            btns = ttk.Frame(form)
            btns.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
            ttk.Button(btns, text="添加卡片", command=self.on_add_card).pack(side="left")
            ttk.Button(btns, text="清空输入", command=self.on_clear_card_form).pack(
                side="left", padx=8
            )
            ttk.Label(
                btns,
                text="提示：账单日若当月不存在（如 31 号遇 2 月），系统自动吸附到月末。",
                foreground="#666666",
            ).pack(side="left", padx=12)

            lst = ttk.LabelFrame(tab, text="已有卡片列表（点击「刷新全部」更新）", padding=6)
            lst.pack(fill="both", expand=True)
            self.cards_tree = self._tree(
                lst,
                [
                    ("id", "卡号", 150),
                    ("name", "卡片名称", 240),
                    ("statement_day", "账单日", 90),
                    ("due_offset_days", "宽限期(天)", 110),
                    ("min_payment_rate", "最低比例", 100),
                    ("min_payment_floor", "最低下限", 100),
                    ("currency", "币种", 70),
                ],
            )

        def _build_import_tab(self, nb) -> None:
            tab = ttk.Frame(nb, padding=6)
            nb.add(tab, text="② 账单导入")

            box = ttk.LabelFrame(tab, text="选择 CSV 文件导入（自动去重）", padding=12)
            box.pack(fill="x")
            row = ttk.Frame(box)
            row.pack(fill="x", pady=(0, 8))
            ttk.Label(row, text="文件路径：").pack(side="left")
            ttk.Entry(row, textvariable=self.var_csv_path, width=78).pack(side="left", padx=(0, 8))
            ttk.Button(row, text="浏览...", command=self.on_browse_csv).pack(side="left")
            row2 = ttk.Frame(box)
            row2.pack(fill="x")
            ttk.Button(row2, text="导入 CSV", command=self.on_import).pack(side="left")
            ttk.Label(
                row2,
                text="表头：card_id,txn_date,amount,description,signature；导入后自动重算账单。",
                foreground="#666666",
            ).pack(side="left", padx=12)

            result = ttk.LabelFrame(tab, text="导入结果 / 去重统计", padding=6)
            result.pack(fill="both", expand=True, pady=(10, 0))
            self.txt_import = ScrolledText(result, height=16, font=("Consolas", 10))
            self.txt_import.pack(fill="both", expand=True)
            self.txt_import.configure(state="disabled")
            ttk.Label(
                result,
                text="样例文件：data/sample_bills/CARD_A.csv / CARD_B.csv / CARD_C.csv",
                foreground="#666666",
            ).pack(anchor="w", pady=(4, 0))

        def _build_query_tab(self, nb) -> None:
            tab = ttk.Frame(nb, padding=6)
            nb.add(tab, text="③ 账单查询")

            bar = ttk.LabelFrame(tab, text="筛选条件", padding=10)
            bar.pack(fill="x", pady=(0, 8))
            ttk.Label(bar, text="卡片：").pack(side="left")
            self.cmb_query_card = ttk.Combobox(
                bar, textvariable=self.var_query_card, width=24, state="readonly"
            )
            self.cmb_query_card.pack(side="left", padx=(0, 14))
            ttk.Label(bar, text="账单月：").pack(side="left")
            ttk.Entry(bar, textvariable=self.var_query_month, width=14).pack(
                side="left", padx=(0, 14)
            )
            ttk.Label(bar, text="（留空=全部，格式 YYYY-MM）", foreground="#666666").pack(
                side="left", padx=(0, 14)
            )
            ttk.Button(bar, text="查询账单", command=self.on_query_bills).pack(side="left", padx=(0, 8))
            ttk.Button(bar, text="重算账单", command=self.on_rebuild).pack(side="left", padx=(0, 8))
            ttk.Button(bar, text="标记选中账单已还", command=self.on_mark_paid_from_bills).pack(
                side="left"
            )

            lst = ttk.LabelFrame(tab, text="账单列表（应还 / 最低 / 手续费 / 免息期）", padding=6)
            lst.pack(fill="both", expand=True)
            self.bills_tree = self._tree(
                lst,
                [
                    ("card_id", "卡号", 110),
                    ("billing_month", "账单月", 100),
                    ("statement_date", "账单日", 110),
                    ("due_date", "到期日", 110),
                    ("free_days", "最长免息期(天)", 130),
                    ("total_due", "应还", 120),
                    ("minimum_payment", "最低还款", 110),
                    ("installment_fee", "手续费", 100),
                    ("paid", "状态", 70),
                ],
            )
            self.bills_tree.bind("<Double-1>", self.on_bill_double_click)

        def _build_reminder_tab(self, nb) -> None:
            tab = ttk.Frame(nb, padding=6)
            nb.add(tab, text="④ 还款提醒")

            top = ttk.LabelFrame(tab, text="检查今日到期提醒（幂等：重复点击不会重复推送）", padding=10)
            top.pack(fill="x")
            ttk.Label(top, text="今日(YYYY-MM-DD，留空=系统当天)：").pack(side="left")
            ttk.Entry(top, textvariable=self.var_today, width=18).pack(side="left", padx=(0, 12))
            ttk.Label(top, text="提前天数：").pack(side="left")
            ttk.Entry(top, textvariable=self.var_lead_days, width=6).pack(side="left", padx=(0, 12))
            ttk.Button(top, text="检查今日提醒", command=self.on_check_reminders).pack(
                side="left", padx=(0, 8)
            )
            ttk.Button(top, text="刷新提醒台账", command=self.on_refresh_reminders).pack(side="left")

            mid = ttk.LabelFrame(tab, text="本次新增提醒（FULL / MINIMUM）", padding=6)
            mid.pack(fill="both", expand=True, pady=(8, 0))
            self.rem_tree = self._tree(
                mid,
                [
                    ("card_id", "卡号", 120),
                    ("billing_cycle", "账单周期", 110),
                    ("strategy", "策略", 150),
                    ("amount", "金额", 120),
                    ("days_to_due", "距到期(天)", 110),
                    ("due_date", "到期日", 120),
                ],
            )

            paid = ttk.LabelFrame(tab, text="标记已还（此后该期停止提醒）", padding=10)
            paid.pack(fill="x", pady=(8, 0))
            ttk.Label(paid, text="卡号：").pack(side="left")
            ttk.Entry(paid, textvariable=self.var_paid_card, width=18).pack(side="left", padx=(0, 12))
            ttk.Label(paid, text="账单月(YYYY-MM)：").pack(side="left")
            ttk.Entry(paid, textvariable=self.var_paid_month, width=14).pack(side="left", padx=(0, 12))
            ttk.Button(paid, text="标记已还", command=self.on_mark_paid).pack(side="left")

            ledger = ttk.LabelFrame(tab, text="历史提醒台账（审计用，标记已还不会删除）", padding=6)
            ledger.pack(fill="both", expand=True, pady=(8, 0))
            self.ledger_tree = self._tree(
                ledger,
                [
                    ("card_id", "卡号", 110),
                    ("billing_cycle", "账单周期", 110),
                    ("strategy", "策略", 150),
                    ("due_date", "到期日", 110),
                    ("amount", "金额", 110),
                    ("created_at", "创建时间", 190),
                ],
            )

        # ---------------------------------------------------------- 刷新
        def refresh_all(self) -> None:
            self._render_cards()
            self._refresh_query_options()
            self.on_query_bills()
            self.on_refresh_reminders()
            self._status("已刷新全部数据")
            self._log("界面数据已刷新。")

        def _refresh_query_options(self) -> None:
            cards = self.controller.handle_list_cards()
            values = ["全部"] + [c["id"] for c in cards]
            self.cmb_query_card.configure(values=values)
            if self.var_query_card.get() not in values:
                self.var_query_card.set("全部")

        def _render_cards(self) -> None:
            rows = self._safe(self.controller.handle_list_cards)
            if rows is None:
                return
            self._card_rows = rows
            self.cards_tree.delete(*self.cards_tree.get_children())
            for i, r in enumerate(rows):
                self.cards_tree.insert(
                    "", "end", iid=str(i),
                    values=(
                        r["id"], r["name"], r["statement_day"], r["due_offset_days"],
                        r["min_payment_rate"], r["min_payment_floor"], r["currency"],
                    ),
                )

        def _render_bills(self, rows: list[dict]) -> None:
            self._bill_rows = rows
            self.bills_tree.delete(*self.bills_tree.get_children())
            for i, r in enumerate(rows):
                self.bills_tree.insert(
                    "", "end", iid=str(i),
                    values=(
                        r["card_id"], r["billing_month"], r["statement_date"],
                        r["due_date"], r["free_days"], r["total_due"],
                        r["minimum_payment"], r["installment_fee"], r["paid"],
                    ),
                )

        def _render_reminders(self, rows: list[dict]) -> None:
            self.rem_tree.delete(*self.rem_tree.get_children())
            for i, r in enumerate(rows):
                self.rem_tree.insert(
                    "", "end", iid=str(i),
                    values=(
                        r["card_id"], r["billing_cycle"], r["strategy"],
                        r["amount"], r["days_to_due"], r["due_date"],
                    ),
                )

        def _render_ledger(self, rows: list[dict]) -> None:
            self.ledger_tree.delete(*self.ledger_tree.get_children())
            for i, r in enumerate(rows):
                self.ledger_tree.insert(
                    "", "end", iid=str(i),
                    values=(
                        r["card_id"], r["billing_cycle"], r["strategy"],
                        r["due_date"], r["amount"], r["created_at"],
                    ),
                )

        # ---------------------------------------------------------- 按钮回调
        def on_add_card(self) -> None:
            msg = self._safe(
                self.controller.handle_add_card,
                self.var_card_id.get(),
                self.var_card_name.get(),
                self.var_stmt_day.get(),
                self.var_due_offset.get(),
                self.var_min_rate.get(),
                self.var_min_floor.get(),
            )
            if msg is None:
                return
            self._log(msg)
            self._status(msg)
            if "已添加" in msg:
                self.var_card_id.set("")
                self.var_card_name.set("")
                self._render_cards()
                self._refresh_query_options()

        def on_clear_card_form(self) -> None:
            for var, default in (
                (self.var_card_id, ""), (self.var_card_name, ""),
                (self.var_stmt_day, "5"), (self.var_due_offset, "20"),
                (self.var_min_rate, "0.10"), (self.var_min_floor, "10.00"),
            ):
                var.set(default)
            self._log("已清空卡片输入表单。")

        def on_browse_csv(self) -> None:
            from tkinter import filedialog

            path = filedialog.askopenfilename(
                title="选择账单 CSV 文件",
                filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            )
            if path:
                self.var_csv_path.set(path)

        def on_import(self) -> None:
            path = self.var_csv_path.get()
            if not path.strip():
                self._log("请先选择 CSV 文件，或点击「浏览...」。")
                self._status("未选择文件")
                return
            result = self._safe(self.controller.handle_import, path)
            if result is None:
                return
            stats, msg = result
            self.txt_import.configure(state="normal")
            self.txt_import.insert("end", msg + "\n")
            self.txt_import.see("end")
            self.txt_import.configure(state="disabled")
            self._log(msg)
            self._status(f"导入：新增 {stats.imported} / 重复 {stats.duplicates}")
            self._render_cards()
            self.on_query_bills()

        def on_rebuild(self) -> None:
            result = self._safe(self.controller.handle_rebuild)
            if result is None:
                return
            count, msg = result
            self._log(msg)
            self._status(f"已重算 {count} 期账单")
            self.on_query_bills()

        def on_query_bills(self) -> None:
            rows = self._safe(
                self.controller.handle_query_bills,
                self.var_query_card.get(),
                self.var_query_month.get(),
            )
            if rows is None:
                return
            self._render_bills(rows)
            self._log(f"查询账单：{len(rows)} 期（卡片={self.var_query_card.get()}，月份={self.var_query_month.get() or '全部'}）。")
            self._status(f"共 {len(rows)} 期账单")

        def on_check_reminders(self) -> None:
            lead = self.var_lead_days.get() or "7"
            result = self._safe(
                self.controller.handle_check_reminders, self.var_today.get(), lead
            )
            if result is None:
                return
            rows, msg = result
            self._render_reminders(rows)
            for r in rows:
                self._log(
                    f"[新提醒] {r['card_id']} 周期={r['billing_cycle']} "
                    f"策略={r['strategy']} 金额={r['amount']} 距到期={r['days_to_due']}天"
                )
            self._log(msg)
            self._status(f"本次新增提醒 {len(rows)} 条")
            self.on_refresh_reminders()

        def on_refresh_reminders(self) -> None:
            rows = self._safe(self.controller.handle_list_reminders)
            if rows is None:
                return
            self._render_ledger(rows)

        def on_mark_paid(self) -> None:
            msg = self._safe(
                self.controller.handle_mark_paid,
                self.var_paid_card.get(),
                self.var_paid_month.get(),
            )
            if msg is None:
                return
            self._log(msg)
            self._status(msg)
            if "已标记" in msg:
                self.on_query_bills()
                self.on_refresh_reminders()

        def on_mark_paid_from_bills(self) -> None:
            selected = self.bills_tree.selection()
            if not selected:
                self._log("请先在账单列表中选中一期账单，再点击「标记选中账单已还」。")
                self._status("未选中账单")
                return
            row = self._bill_rows[int(selected[0])]
            self.var_paid_card.set(row["card_id"])
            self.var_paid_month.set(row["billing_month"])
            self.nb.select(3)  # 切到还款提醒页签
            self.on_mark_paid()

        def on_bill_double_click(self, _event) -> None:
            """双击账单行：把卡号/账单月带入「标记已还」输入框并切换页签。"""
            selected = self.bills_tree.selection()
            if not selected:
                return
            row = self._bill_rows[int(selected[0])]
            self.var_paid_card.set(row["card_id"])
            self.var_paid_month.set(row["billing_month"])
            self.nb.select(3)

        def on_close(self) -> None:
            self.controller.close()
            self.destroy()

    return App


def _run_gui_mock_smoke() -> list[str]:
    """用轻量 mock 的 tkinter 控件跑一遍真实 App：验证布局与所有按钮回调。

    本机 Python 可能未编译 tkinter（标准库可选组件），无法真正创建窗口；
    这里在 sys.modules 中注入一套最小控件桩，构造真实的 App 实例并逐个触发
    按钮回调，确保界面代码本身没有属性/方法错误，且回调能返回正确结果。
    """
    import sys
    import types

    class Var:
        def __init__(self, master=None, value=None):
            self._value = value

        def get(self):
            return self._value

        def set(self, value):
            self._value = value

    class Base:
        def __init__(self, master=None, **kw):
            self.kw = kw
            self.lines: list[str] = []

        def pack(self, **kw):
            return self

        def grid(self, **kw):
            return self

        def configure(self, **kw):
            self.kw.update(kw)
            return self

        def bind(self, *args, **kw):
            return self

    class Tk(Base):
        def __init__(self):
            super().__init__()
            self._title = ""

        def title(self, value=None):
            if value is None:
                return self._title
            self._title = value

        def geometry(self, *a):
            pass

        def minsize(self, *a):
            pass

        def protocol(self, *a):
            pass

        def after(self, _ms, fn):
            fn()  # 立即触发初始 refresh_all，覆盖首屏渲染

        def mainloop(self):
            pass

        def destroy(self):
            pass

    class Entry(Base):
        def get(self):
            return str(self.kw.get("textvariable", Var()).get() or "")

        def set(self, v):
            self.kw["textvariable"].set(v)

    class Treeview(Base):
        def __init__(self, master=None, **kw):
            super().__init__(master, **kw)
            self._items: dict[str, tuple] = {}
            self._selection: tuple[str, ...] = ()

        def heading(self, *a, **kw):
            pass

        def column(self, *a, **kw):
            pass

        def insert(self, parent, index, iid=None, values=None, **kw):
            self._items[str(iid)] = values or ()

        def delete(self, *iids):
            if not iids:
                self._items.clear()
            else:
                for iid in iids:
                    self._items.pop(str(iid), None)

        def get_children(self):
            return list(self._items)

        def selection(self):
            return self._selection

        def yview(self, *a):
            pass

    class Notebook(Base):
        def __init__(self, master=None, **kw):
            super().__init__(master, **kw)
            self._tabs = []

        def add(self, child, **kw):
            self._tabs.append(child)

        def tabs(self):
            return list(self._tabs)

        def select(self, _idx=None):
            pass

    class Text(Base):
        def insert(self, _index, text):
            self.lines.append(text)

        def delete(self, *a):
            self.lines.clear()

        def see(self, *a):
            pass

    class Style:
        def __init__(self, *a, **kw):
            pass

        def theme_use(self, *a):
            pass

        def configure(self, *a, **kw):
            pass

    class Scrollbar(Base):
        def set(self, *a):
            pass

    tk = types.ModuleType("tkinter")
    tk.Tk, tk.StringVar, tk.TclError = Tk, Var, Exception
    ttk = types.ModuleType("tkinter.ttk")
    for name, cls in (
        ("Frame", Base), ("Label", Base), ("Button", Base), ("Entry", Entry),
        ("LabelFrame", Base), ("Treeview", Treeview), ("Notebook", Notebook),
        ("Scrollbar", Scrollbar), ("Combobox", Entry), ("Style", Style),
    ):
        setattr(ttk, name, cls)
    scrolledtext = types.ModuleType("tkinter.scrolledtext")
    scrolledtext.ScrolledText = Text
    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askopenfilename = lambda **kw: ""
    tk.ttk, tk.scrolledtext, tk.filedialog = ttk, scrolledtext, filedialog

    keys = ("tkinter", "tkinter.ttk", "tkinter.scrolledtext", "tkinter.filedialog")
    saved = {k: sys.modules.get(k) for k in keys}
    sys.modules.update(
        {
            "tkinter": tk,
            "tkinter.ttk": ttk,
            "tkinter.scrolledtext": scrolledtext,
            "tkinter.filedialog": filedialog,
        }
    )
    results: list[str] = []
    try:
        App = _build_app()
        app = App(":memory:")
        results.append(f"[OK] mock GUI 构造 -> 标题={app.title()}，页签={len(app.nb.tabs())}")

        # 添加卡片按钮
        app.var_card_id.set("CARD_A")
        app.var_card_name.set("测试卡")
        app.var_stmt_day.set("5")
        app.var_due_offset.set("20")
        app.on_add_card()
        assert any("已添加" in line for line in app.txt_log.lines)
        results.append("[OK] mock 按钮：添加卡片 -> 日志与卡片表格已更新")

        # 导入按钮（复用样例文件）
        app.var_csv_path.set(str(PROJECT_ROOT / "data" / "sample_bills" / "CARD_A.csv"))
        app.on_import()
        assert len(app._bill_rows) == 12
        results.append("[OK] mock 按钮：导入 CSV -> 账单表格 12 期")

        # 查询按钮
        app.var_query_month.set("2025-02")
        app.on_query_bills()
        assert len(app._bill_rows) == 1
        results.append("[OK] mock 按钮：查询账单 -> 按月份筛选命中 1 期")

        # 检查提醒按钮（双策略）
        app.var_query_month.set("")
        app.var_today.set("2025-02-18")
        app.on_check_reminders()
        assert len(app.rem_tree._items) == 2
        results.append("[OK] mock 按钮：检查今日提醒 -> 渲染 FULL/MINIMUM 两行")

        # 从账单列表标记已还（双击/按钮两条路径）
        target = next(
            i for i, r in enumerate(app._bill_rows) if r["billing_month"] == "2025-02"
        )
        app.bills_tree._selection = (str(target),)
        app.on_mark_paid_from_bills()
        assert any("已标记" in line for line in app.txt_log.lines)
        results.append("[OK] mock 按钮：标记选中账单已还 -> 状态同步且切换页签")

        # 再次检查提醒（同周期已还 -> 0 条）
        app.var_today.set("2025-02-20")
        app.on_check_reminders()
        assert len(app.rem_tree._items) == 0
        results.append("[OK] mock 按钮：再次检查提醒 -> 已还停推（0 条）")

        app.on_refresh_reminders()
        app.on_clear_card_form()
        app.on_rebuild()
        app.on_close()
        results.append("[OK] mock 按钮：台账刷新/清空表单/重算/退出 -> 均无异常")
    finally:
        for k in keys:
            if saved[k] is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = saved[k]
    return results


def run_selftest() -> int:
    """无显示器环境下的回归自测：直接调用「按钮回调」对应的 controller 方法。

    用一个内存数据库重放完整流程，验证每个按钮动作都能跑通并返回正确数据：
    添加卡片 -> 列表 -> 导入(去重) -> 重算 -> 查询(免息期) -> 检查提醒(幂等)
    -> 标记已还(停推) -> 提醒台账。
    """
    from io import StringIO

    checks: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        if not condition:
            raise AssertionError(f"{label} 失败：{detail}")
        checks.append(f"[OK] {label}" + (f" -> {detail}" if detail else ""))

    ctl = CardBillController(":memory:")
    try:
        # ① 添加卡片
        msg = ctl.handle_add_card("CARD_A", "招商银行经典白金卡", 5, 20, "0.10", "10.00")
        check("添加卡片按钮", "已添加" in msg, msg)
        dup = ctl.handle_add_card("CARD_A", "重复卡", 5, 20)
        check("添加卡片按钮（重复卡号被拒）", "已存在" in dup, dup)
        bad = ctl.handle_add_card("BAD", "非法账单日", 32, 20)
        check("添加卡片按钮（账单日校验）", "账单日" in bad, bad)

        # ② 卡片列表
        cards = ctl.handle_list_cards()
        check("卡片列表按钮", len(cards) == 1 and cards[0]["id"] == "CARD_A", str(cards))

        # ③④ 导入 CSV + 去重（用样例文件；不存在时退化为内联 CSV）
        csv_path = PROJECT_ROOT / "data" / "sample_bills" / "CARD_A.csv"
        stats, msg = ctl.handle_import(csv_path)
        check("导入 CSV 按钮", stats.imported > 0 and stats.duplicates == 0, msg.strip().splitlines()[0])
        first_imported = stats.imported
        stats2, _ = ctl.handle_import(csv_path)
        check(
            "导入 CSV 按钮（重复导入被去重）",
            stats2.imported == 0 and stats2.duplicates == first_imported,
            f"新增={stats2.imported}, 重复={stats2.duplicates}",
        )

        # ⑤ 重算账单
        count, msg = ctl.handle_rebuild()
        check("重算账单按钮", count == 12, f"{count} 期，{msg}")

        # ⑥ 查询账单：含免息期与金额字段
        rows = ctl.handle_query_bills("CARD_A", None)
        check("查询账单按钮（全部月份）", len(rows) == 12, f"{len(rows)} 期")
        one = rows[0]
        check(
            "查询账单按钮（字段完整）",
            all(k in one for k in (
                "statement_date", "due_date", "free_days",
                "total_due", "minimum_payment", "installment_fee", "paid",
            )) and one["free_days"] > 0,
            str(one),
        )
        filtered = ctl.handle_query_bills("全部", "2025-02")
        check("查询账单按钮（按月份筛选）", len(filtered) == 1, str(filtered))

        # ⑦ 检查今日提醒：FULL + MINIMUM 两种策略
        new_rows, msg = ctl.handle_check_reminders("2025-02-18", 7)
        strategies = {r["strategy"] for r in new_rows}
        check(
            "检查今日提醒按钮（双策略）",
            len(new_rows) == 2 and strategies == {"全额 FULL", "最低 MINIMUM"},
            f"{strategies}, {msg}",
        )
        again, msg2 = ctl.handle_check_reminders("2025-02-18", 7)
        check("检查今日提醒按钮（同日重复调用幂等）", len(again) == 0, msg2)
        ledger = ctl.handle_list_reminders()
        check("提醒台账按钮", len(ledger) == 2, f"台账 {len(ledger)} 条")

        # ⑧ 标记已还 -> 该期不再推送
        paid_msg = ctl.handle_mark_paid("CARD_A", "2025-02")
        check("标记已还按钮", "已标记" in paid_msg, paid_msg)
        after_paid, msg3 = ctl.handle_check_reminders("2025-02-20", 7)
        check("标记已还按钮（该期停推）", len(after_paid) == 0, msg3)
        bills_after = ctl.handle_query_bills("CARD_A", "2025-02")
        check("标记已还按钮（账单状态同步）", bills_after[0]["paid"] == "已还", str(bills_after))

        # ⑨ mock tkinter 控件，构造真实 App 并逐个触发按钮回调
        checks.extend(_run_gui_mock_smoke())

        # 内联 StringIO 导入路径也能工作（验证 importer 复用无副作用）
        inline = StringIO(
            "card_id,txn_date,amount,description,signature\n"
            "CARD_A,2025-03-03,9.90,测试消费,SELFTXN-1\n"
        )
        s = import_csv(ctl.db, inline)
        check("导入回调（文件对象兼容）", s.imported == 1, f"新增={s.imported}")
    finally:
        ctl.close()

    print("\n".join(checks))
    print(f"\nGUI selftest: {len(checks)} 项全部通过。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cardbill.gui", description="信用卡账单管理与还款提醒系统（图形界面）"
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB), help=f"SQLite 数据库路径（默认 {DEFAULT_DB}）"
    )
    parser.add_argument(
        "--selftest", action="store_true", help="无显示器环境下验证按钮回调，不弹出窗口"
    )
    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest()
    App = _build_app()
    app = App(args.db)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
