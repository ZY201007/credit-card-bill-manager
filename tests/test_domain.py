"""日期边界测试：跨月、大小月 28/29/30/31 与闰年。"""
from datetime import date

from cardbill.domain import (
    billing_cycle_start,
    cycle_max_interest_free_days,
    due_date,
    interest_free_days,
    last_day_of_month,
    previous_statement_date,
    statement_date,
    statement_month_for_date,
)


class TestLastDayOfMonth:
    def test_leap_and_common_year(self):
        assert last_day_of_month(2024, 2) == 29  # 闰年
        assert last_day_of_month(2025, 2) == 28  # 平年
        assert last_day_of_month(2000, 2) == 29  # 世纪闰年
        assert last_day_of_month(1900, 2) == 28  # 世纪平年

    def test_30_and_31(self):
        assert last_day_of_month(2025, 4) == 30
        assert last_day_of_month(2025, 1) == 31


class TestStatementDate:
    def test_clamp_to_month_end(self):
        assert statement_date(2025, 2, 31) == date(2025, 2, 28)  # 平年 2 月无 31 号
        assert statement_date(2024, 2, 31) == date(2024, 2, 29)  # 闰年 2 月
        assert statement_date(2025, 4, 31) == date(2025, 4, 30)  # 小月
        assert statement_date(2025, 4, 30) == date(2025, 4, 30)  # 恰好存在

    def test_normal(self):
        assert statement_date(2025, 9, 5) == date(2025, 9, 5)

    def test_invalid_day(self):
        import pytest

        with pytest.raises(ValueError):
            statement_date(2025, 2, 0)


class TestDueDate:
    def test_cross_month(self):
        assert due_date(date(2025, 1, 31), 20) == date(2025, 2, 20)
        assert due_date(date(2025, 12, 25), 20) == date(2026, 1, 14)  # 跨年

    def test_leap_february(self):
        # 2024 闰年：2 月有 29 天，2/28 + 20 天 = 3/19
        assert due_date(date(2024, 2, 28), 20) == date(2024, 3, 19)
        # 2025 平年：2/28 + 20 天 = 3/20
        assert due_date(date(2025, 2, 28), 20) == date(2025, 3, 20)


class TestCycleBoundaries:
    def test_previous_statement_and_cycle_start(self):
        stmt = date(2025, 1, 5)
        assert previous_statement_date(stmt, 5) == date(2024, 12, 5)
        assert billing_cycle_start(stmt, 5) == date(2024, 12, 6)

    def test_cycle_start_when_statement_day_clamped(self):
        # 账单日 31 在 2 月吸附到 28；上月账单日是 1/31，周期从 2/1 开始
        stmt = statement_date(2025, 2, 31)  # 2025-02-28
        assert previous_statement_date(stmt, 31) == date(2025, 1, 31)
        assert billing_cycle_start(stmt, 31) == date(2025, 2, 1)


class TestStatementMonthForDate:
    def test_boundary(self):
        # 账单日=5：交易日 <= 5 归当月，> 5 归下月
        assert statement_month_for_date(date(2025, 1, 5), 5) == "2025-01"
        assert statement_month_for_date(date(2025, 1, 6), 5) == "2025-02"
        # 跨年
        assert statement_month_for_date(date(2025, 12, 6), 5) == "2026-01"

    def test_clamped_statement_day(self):
        # 账单日=31：2 月实际账单日是 28，但归属规则仍按 31 判定
        assert statement_month_for_date(date(2025, 2, 28), 31) == "2025-02"
        assert statement_month_for_date(date(2025, 2, 1), 31) == "2025-02"


class TestInterestFreeDays:
    def test_single_transaction(self):
        due = due_date(date(2025, 1, 5), 20)  # 2025-01-25
        assert interest_free_days(date(2025, 1, 3), due) == 22

    def test_cycle_max(self):
        stmt = date(2025, 1, 5)
        due = due_date(stmt, 20)  # 2025-01-25
        # 周期起始 2024-12-06 -> 2025-01-25 = 50 天
        assert cycle_max_interest_free_days(stmt, 5, due) == 50
