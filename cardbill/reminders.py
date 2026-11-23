"""还款提醒引擎（幂等）。

对外主入口：check_reminders(db, today) —— 供 cron / 任务计划程序每日调用。

设计要点（对应简历「重复推送由每期 3~5 次降为 0」）：
    1. 幂等：提醒台账 reminders 表以 (card_id, billing_cycle, strategy) 为唯一键，
       同一期 + 同一策略至多写入一条；重复运行直接被 INSERT OR IGNORE 拦截；
    2. 已还停推：is_paid=1 的账单被跳过；
    3. 两种策略可独立开关：FULL（全额提醒）/ MINIMUM（最低提醒），
       因此同一期最多产生 2 条提醒，且各自独立幂等。
"""
from __future__ import annotations

from datetime import date, datetime

from .db import Database
from .models import (
    ALL_STRATEGIES,
    STRATEGY_FULL,
    STRATEGY_MINIMUM,
    Reminder,
)


def _parse_today(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def check_reminders(
    db: Database,
    today: date | str,
    lead_days: int = 7,
    strategies: tuple[str, ...] = ALL_STRATEGIES,
) -> list[Reminder]:
    """返回「本次真正新增」的到期提醒（已推送过 / 已还的不再返回）。

    Parameters
    ----------
    db : Database
    today : date | str
        调度器传入的「今天」，便于用固定日期做确定性测试。
    lead_days : int
        提前提醒窗口天数；当 0 <= (到期日 - 今天) <= lead_days 时触发。
    strategies : tuple
        需执行的策略，默认全额 + 最低两种。

    Returns
    -------
    list[Reminder]
        本次新写入台账、应当被推送的提醒列表。空列表表示「今日无新提醒」。
    """
    today_d = _parse_today(today)
    created_at = datetime.now().isoformat(timespec="seconds")
    new_reminders: list[Reminder] = []

    for bill in db.list_bills(paid=False):
        days_to_due = (bill.due_dt - today_d).days
        if days_to_due < 0 or days_to_due > lead_days:
            continue

        for strategy in strategies:
            if strategy == STRATEGY_FULL:
                amount = bill.total_due_amount
            elif strategy == STRATEGY_MINIMUM:
                amount = bill.minimum_payment_amount
            else:
                continue

            reminder = Reminder(
                card_id=bill.card_id,
                billing_cycle=bill.billing_month,
                strategy=strategy,
                due_dt=bill.due_dt,
                amount=amount,
                days_to_due=days_to_due,
            )
            if db.insert_reminder(reminder, created_at):
                new_reminders.append(reminder)

    return new_reminders


def count_would_be_pushes(
    db: Database, today: date | str, lead_days: int = 7
) -> int:
    """（仅用于演示/回归）若无幂等台账，今日「本应」推送的条数。

    回归测试用它对比：朴素调度在一天内多次调用会累积 3~5 次重复推送，
    而 check_reminders + 台账后重复推送降为 0。
    """
    today_d = _parse_today(today)
    count = 0
    for bill in db.list_bills(paid=False):
        d = (bill.due_dt - today_d).days
        if 0 <= d <= lead_days:
            count += len(ALL_STRATEGIES)
    return count
