"""业务编排：把已导入的交易汇总成账单，供 CLI 与回归测试共用。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .db import Database
from .domain import (
    due_date,
    installment_fee,
    minimum_payment,
    statement_date,
    total_due,
)
from .importer import SIG_INSTALLMENT, SIG_INSTALLMENT_FEE, SIG_PREV_BALANCE
from .models import Bill
from .money import D

_FEE_ZERO = D("0")


def rebuild_bills(db: Database) -> list[Bill]:
    """按 (卡, 账单月) 汇总交易，重算所有账单。

    金额拆分规则：
        - 普通交易 -> 本期新增消费 new_transactions
        - __PREV_BALANCE__ -> 上期余额
        - __INSTALLMENT__  -> 分期本金；description = "费率,期数"，手续费由
                              domain.installment_fee 计算（与本金分开）
        - __INSTALLMENT_FEE__ -> 直接给定的分期手续费

    日期规则：账单日按卡配置吸附月末，到期日 = 账单日 + 宽限期（跨月安全）。
    """
    bills: list[Bill] = []
    cards = {c.id: c for c in db.list_cards()}
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for txn in db.list_transactions():
        grouped[(txn.card_id, txn.statement_month)].append(txn)

    for (card_id, billing_month), txns in sorted(grouped.items()):
        card = cards.get(card_id)
        if card is None:
            continue

        year, month = (int(x) for x in billing_month.split("-"))
        stmt_date = statement_date(year, month, card.statement_day)
        due_dt = due_date(stmt_date, card.due_offset_days)

        previous_balance = D("0")
        new_transactions = D("0")
        installment_amount = D("0")
        fee = D("0")

        for txn in txns:
            sig = txn.signature
            if sig == SIG_PREV_BALANCE:
                previous_balance += txn.amount
            elif sig == SIG_INSTALLMENT:
                installment_amount += txn.amount
                try:
                    rate_str, periods_str = txn.description.split(",", 1)
                    fee += installment_fee(txn.amount, D(rate_str), int(periods_str))
                except (ValueError, TypeError):
                    # 未提供合法费率/期数时，该笔分期不产生手续费
                    pass
            elif sig == SIG_INSTALLMENT_FEE:
                fee += txn.amount
            else:
                new_transactions += txn.amount

        due_total = total_due(previous_balance, new_transactions, installment_amount, fee)
        min_pay = minimum_payment(due_total, card.min_payment_rate, card.min_payment_floor)

        # 已有则保留 paid 状态（upsert 不会覆盖 is_paid）
        existing = db.get_bill(card_id, billing_month)
        bill = Bill(
            card_id=card_id,
            billing_month=billing_month,
            statement_date=stmt_date,
            due_dt=due_dt,
            previous_balance=previous_balance,
            new_transactions=new_transactions,
            installment_amount=installment_amount,
            installment_fee_amount=fee,
            total_due_amount=due_total,
            minimum_payment_amount=min_pay,
            is_paid=existing.is_paid if existing else False,
            paid_at=existing.paid_at if existing else None,
            transaction_ids=[t.card_id for t in txns],
        )
        db.upsert_bill(bill)
        bills.append(db.get_bill(card_id, billing_month) or bill)

    return bills
