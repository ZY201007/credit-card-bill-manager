"""命令行入口：python -m cardbill.cli <command> [options]"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from .db import Database
from .importer import import_csv
from .models import CreditCard
from .reminders import check_reminders
from .service import rebuild_bills

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "cardbill.db"


def _fmt(value) -> str:
    return f"{value:.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardbill", description="信用卡账单管理与还款提醒系统"
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB), help=f"SQLite 数据库路径（默认 {DEFAULT_DB}）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="初始化数据库表结构")

    p_card = sub.add_parser("add-card", help="新增一张信用卡")
    p_card.add_argument("--id", required=True, help="卡号/唯一标识")
    p_card.add_argument("--name", required=True, help="卡片名称")
    p_card.add_argument("--statement-day", type=int, required=True, help="账单日日号 1~31")
    p_card.add_argument("--due-offset-days", type=int, required=True, help="账单日到到期日天数")
    p_card.add_argument("--min-rate", default="0.10", help="最低还款比例（默认 0.10）")
    p_card.add_argument("--min-floor", default="10.00", help="最低还款下限（默认 10.00）")

    p_imp = sub.add_parser("import", help="导入交易 CSV（自动去重）")
    p_imp.add_argument("--file", required=True, help="CSV 文件路径")

    sub.add_parser("rebuild-bills", help="由交易重算所有账单")

    sub.add_parser("list-bills", help="列出所有账单")

    p_rem = sub.add_parser("check-reminders", help="检查并写入到期提醒（可挂 cron）")
    p_rem.add_argument("--today", help="模拟今天 YYYY-MM-DD（默认系统当天）")
    p_rem.add_argument("--lead-days", type=int, default=7, help="提前提醒天数（默认 7）")

    p_paid = sub.add_parser("mark-paid", help="标记某期账单已还（此后停止提醒）")
    p_paid.add_argument("card_id", help="卡号")
    p_paid.add_argument("billing_month", help="账单月 YYYY-MM")

    return parser


def _ensure_parent(path: str) -> None:
    parent = Path(path).parent
    if parent and str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _ensure_parent(args.db)
    db = Database(args.db)
    db.init_schema()
    try:
        cmd = args.command

        if cmd == "init-db":
            print(f"数据库已初始化: {args.db}")

        elif cmd == "add-card":
            db.add_card(
                CreditCard(
                    id=args.id,
                    name=args.name,
                    statement_day=args.statement_day,
                    due_offset_days=args.due_offset_days,
                    min_payment_rate=args.min_rate,
                    min_payment_floor=args.min_floor,
                )
            )
            print(f"已添加卡片: {args.id} {args.name}")

        elif cmd == "import":
            stats = import_csv(db, args.file)
            print(
                f"导入完成: 总行={stats.total_rows} 新增={stats.imported} "
                f"重复={stats.duplicates} 跳过={stats.skipped}"
            )
            for err in stats.errors or []:
                print(f"  ! {err}")

        elif cmd == "rebuild-bills":
            bills = rebuild_bills(db)
            print(f"已重算 {len(bills)} 期账单")

        elif cmd == "list-bills":
            bills = db.list_bills()
            if not bills:
                print("（暂无账单，请先 import 再 rebuild-bills）")
            for b in bills:
                flag = "已还" if b.is_paid else "未还"
                print(
                    f"{b.card_id} {b.billing_month} 账单日={b.statement_date} "
                    f"到期={b.due_dt} 应还={_fmt(b.total_due_amount)} "
                    f"最低={_fmt(b.minimum_payment_amount)} [{flag}]"
                )

        elif cmd == "check-reminders":
            today = date.fromisoformat(args.today) if args.today else date.today()
            reminders = check_reminders(db, today, lead_days=args.lead_days)
            if not reminders:
                print("今日无新提醒")
            for r in reminders:
                print(
                    f"[新提醒] {r.card_id} 周期={r.billing_cycle} "
                    f"策略={r.strategy} 金额={_fmt(r.amount)} "
                    f"距到期={r.days_to_due}天"
                )

        elif cmd == "mark-paid":
            ok = db.mark_paid(
                args.card_id, args.billing_month, datetime.now().isoformat(timespec="seconds")
            )
            print("已标记还款" if ok else "未找到对应账单")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
