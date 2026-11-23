"""领域核心逻辑：全部为无副作用的纯函数，可脱离 UI / SQLite 单测。

包含两大类规则：

一、日期规则（处理跨月、大小月 28/29/30/31 与闰年）
    - 账单日 statement_date(year, month, statement_day)
        每张卡有固定的「账单日」日号（1~31）。当月没有该日号时（例如 2 月没有 31 号），
        自动「吸附」到当月最后一天。例：账单日=31，2025-02 -> 2025-02-28；
        2024-02（闰年）-> 2024-02-29。
    - 到期日 due_date(stmt_date, due_offset_days) = 账单日 + 宽限期天数。
        用 timedelta 自然跨月，例如 2025-01-31 + 20 天 = 2025-02-20。
    - 账单周期起始日 billing_cycle_start = 上月账单日 + 1 天。
        本期账单统计的是 (上月账单日, 本月账单日] 区间内的交易。
    - 免息期 interest_free_days(交易日, 到期日) = 到期日 - 交易日 的天数。
        周期最长免息期 = 到期日 - 周期起始日（周期首日消费享受最长免息）。

二、金额规则（全部使用 Decimal，量化到分）
    - 分期手续费 installment_fee(本金, 每期费率, 期数) = 本金 × 费率 × 期数，四舍五入到分。
    - 应还金额 total_due = 上期余额 + 本期新增消费 + 分期本金 + 分期手续费。
    - 最低还款 minimum_payment = max(应还金额 × 最低还款比例, 最低还款下限)，四舍五入到分。
      规则：比例默认 10%，下限默认 10.00 元（应还为 0 时最低还款为 0）。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from .money import D, quantize

# ---------------------------------------------------------------- 日期规则


def last_day_of_month(year: int, month: int) -> int:
    """返回指定年月的最后一天（自动识别闰年 2 月 29 天）。"""
    return calendar.monthrange(year, month)[1]


def statement_date(year: int, month: int, statement_day: int) -> date:
    """计算某月的账单日；日号超过当月天数时吸附到月末。

    例：
        statement_date(2025, 2, 31) -> date(2025, 2, 28)  # 平年 2 月
        statement_date(2024, 2, 31) -> date(2024, 2, 29)  # 闰年 2 月
        statement_date(2025, 4, 31) -> date(2025, 4, 30)  # 小月
    """
    if not 1 <= statement_day <= 31:
        raise ValueError("statement_day 必须在 1~31 之间")
    if not 1 <= month <= 12:
        raise ValueError("month 必须在 1~12 之间")
    day = min(statement_day, last_day_of_month(year, month))
    return date(year, month, day)


def due_date(stmt_date: date, due_offset_days: int) -> date:
    """到期日 = 账单日 + 宽限期天数（自然跨月）。"""
    if due_offset_days < 0:
        raise ValueError("due_offset_days 不能为负数")
    return stmt_date + timedelta(days=due_offset_days)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """把年月平移 delta 个月，返回 (year, month)。delta 可为负数。"""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def previous_statement_date(stmt_date: date, statement_day: int) -> date:
    """上一个账单日（账单周期起始的前一天）。"""
    py, pm = shift_month(stmt_date.year, stmt_date.month, -1)
    return statement_date(py, pm, statement_day)


def billing_cycle_start(stmt_date: date, statement_day: int) -> date:
    """本期账单周期起始日 = 上月账单日 + 1 天。

    本期统计区间为 [billing_cycle_start, stmt_date]（左闭右闭）。
    """
    return previous_statement_date(stmt_date, statement_day) + timedelta(days=1)


def statement_month_for_date(txn_date: date, statement_day: int) -> str:
    """判定某笔交易归属的账单月份，返回 'YYYY-MM'。

    规则：交易日在账单日当天或之前 -> 归入当月账单；
          交易日晚于账单日             -> 归入下月账单。
    """
    if txn_date.day <= statement_day:
        year, month = txn_date.year, txn_date.month
    else:
        year, month = shift_month(txn_date.year, txn_date.month, 1)
    return f"{year:04d}-{month:02d}"


def interest_free_days(transaction_date: date, due_dt: date) -> int:
    """单笔交易的免息天数 = 到期日 - 交易日。"""
    return (due_dt - transaction_date).days


def cycle_max_interest_free_days(
    stmt_date: date, statement_day: int, due_dt: date
) -> int:
    """本期账单的最长免息期（周期首日消费享受的天数）。"""
    return interest_free_days(billing_cycle_start(stmt_date, statement_day), due_dt)


# ---------------------------------------------------------------- 金额规则


def installment_fee(principal, rate, periods: int):
    """分期手续费 = 分期本金 × 每期费率 × 期数（四舍五入到分）。

    rate 为「每期费率」的十进制小数，例如 0.006 表示每期 0.6%。
    例：本金 6000.00，费率 0.006，期数 6 -> 6000 × 0.006 × 6 = 216.00。
    """
    if periods <= 0:
        raise ValueError("periods 必须为正整数")
    return quantize(D(principal) * D(rate) * periods)


def per_period_installment_amount(principal, periods: int):
    """每期应还分期本金 = 分期本金 / 期数（四舍五入到分）。"""
    if periods <= 0:
        raise ValueError("periods 必须为正整数")
    return quantize(D(principal) / periods)


def total_due(
    previous_balance,
    new_transactions,
    installment_amount,
    installment_fee_amount,
):
    """应还金额 = 上期余额 + 本期新增消费 + 分期本金 + 分期手续费。"""
    return quantize(
        D(previous_balance)
        + D(new_transactions)
        + D(installment_amount)
        + D(installment_fee_amount)
    )


def minimum_payment(total_due_amount, rate="0.10", floor="10.00"):
    """最低还款额 = max(应还金额 × 比例, 下限)，四舍五入到分。

    - rate：最低还款比例，默认 10%（"0.10"）；
    - floor：最低还款下限，默认 10.00 元；
    - 应还金额为 0 时最低还款为 0（避免下限产生「无债也需还款」的荒谬结果）。
    """
    total = D(total_due_amount)
    if total <= 0:
        return quantize(0)
    return quantize(max(total * D(rate), D(floor)))
