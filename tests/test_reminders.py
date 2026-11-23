"""提醒引擎测试：两种策略、幂等、已还停推、重复推送 3~5 -> 0。"""
from datetime import date

from cardbill.db import Database
from cardbill.models import Bill, STRATEGY_FULL, STRATEGY_MINIMUM
from cardbill.money import D
from cardbill.reminders import check_reminders, count_would_be_pushes


def _add_bill(db: Database, due: date = date(2025, 2, 25), month="2025-02") -> Bill:
    bill = Bill(
        card_id="CARD_A",
        billing_month=month,
        statement_date=date(2025, 2, 5),
        due_dt=due,
        previous_balance=D("100.00"),
        new_transactions=D("900.00"),
        installment_amount=D("0"),
        installment_fee_amount=D("0"),
        total_due_amount=D("1000.00"),
        minimum_payment_amount=D("100.00"),
    )
    db.upsert_bill(bill)
    return bill


class TestTwoStrategies:
    def test_both_strategies_fired_with_correct_amounts(self, db):
        _add_bill(db)
        reminders = check_reminders(db, date(2025, 2, 18), lead_days=7)
        assert len(reminders) == 2
        by_strategy = {r.strategy: r.amount for r in reminders}
        assert by_strategy[STRATEGY_FULL] == D("1000.00")
        assert by_strategy[STRATEGY_MINIMUM] == D("100.00")

    def test_single_strategy_mode(self, db):
        _add_bill(db)
        reminders = check_reminders(
            db, date(2025, 2, 18), lead_days=7, strategies=(STRATEGY_MINIMUM,)
        )
        assert len(reminders) == 1
        assert reminders[0].strategy == STRATEGY_MINIMUM
        assert db.count_reminders(strategy=STRATEGY_FULL) == 0


class TestIdempotency:
    def test_repeated_same_day_produces_no_new_reminders(self, db):
        _add_bill(db)
        today = date(2025, 2, 18)

        first = check_reminders(db, today)
        assert len(first) == 2

        for _ in range(4):
            assert check_reminders(db, today) == []

        assert db.count_reminders() == 2  # 台账始终只有 2 条

    def test_repeated_across_window_days(self, db):
        """整个提醒窗口内每天运行，累计仍只有 2 条。"""
        _add_bill(db)
        for day in range(18, 26):  # 2025-02-18 .. 2025-02-25
            check_reminders(db, date(2025, 2, day))
        assert db.count_reminders() == 2


class TestPaidStop:
    def test_paid_bill_stops_reminding(self, db):
        _add_bill(db)
        assert len(check_reminders(db, date(2025, 2, 18))) == 2

        db.mark_paid("CARD_A", "2025-02", "2025-02-19T10:00:00")

        # 已还后继续调度，不再产生任何新提醒
        assert check_reminders(db, date(2025, 2, 20)) == []
        assert check_reminders(db, date(2025, 2, 22)) == []
        assert db.count_reminders() == 2  # 旧的 2 条保留作审计，不再新增

    def test_paid_before_first_reminder(self, db):
        _add_bill(db)
        db.mark_paid("CARD_A", "2025-02", "2025-02-17T10:00:00")
        assert check_reminders(db, date(2025, 2, 18)) == []
        assert db.count_reminders() == 0


class TestReminderWindow:
    def test_before_window(self, db):
        _add_bill(db)
        # 距到期 8 天 > lead_days=7
        assert check_reminders(db, date(2025, 2, 17), lead_days=7) == []

    def test_on_due_date(self, db):
        _add_bill(db)
        assert len(check_reminders(db, date(2025, 2, 25))) == 2

    def test_after_due_date(self, db):
        _add_bill(db)
        assert check_reminders(db, date(2025, 2, 26)) == []


class TestDuplicatePushesDropToZero:
    def test_duplicate_pushes_drop_to_zero(self, db):
        """复现简历指标：同一期内多次调度，重复推送由 3~5 次降为 0。

        朴素调度每次运行都会推送（无台账），一天内跑 5 次 => 单策略重复 4 次；
        幂等引擎 + 唯一约束后：首次推送 1 条，后续 4 次均被拦截，重复 = 0。
        """
        _add_bill(db)
        today = date(2025, 2, 18)

        # 旧行为：每次调度都直接推送（这里用 5 次模拟一天内多次触发）
        naive_pushes = [count_would_be_pushes(db, today) for _ in range(5)]
        assert sum(naive_pushes) == 10  # 5 次 × 2 策略
        assert naive_pushes[0] == 2

        # 新行为：只有第一次产生推送
        results = [check_reminders(db, today) for _ in range(5)]
        assert len(results[0]) == 2
        assert all(len(r) == 0 for r in results[1:])

        # 每策略台账 1 条 => 重复推送 = 0
        assert db.count_reminders(strategy=STRATEGY_FULL) == 1
        assert db.count_reminders(strategy=STRATEGY_MINIMUM) == 1
        duplicates = sum(naive_pushes) - db.count_reminders()
        assert duplicates == 8  # 10 次朴素推送 -> 2 条真实台账，重复被完全消除

    def test_duplicate_even_after_mark_paid_then_unpaid_scenario(self, db):
        """即使账单状态变化，台账仍阻止同周期同策略的二次推送。"""
        _add_bill(db)
        check_reminders(db, date(2025, 2, 18))  # 首次推送 2 条
        db.mark_paid("CARD_A", "2025-02", "2025-02-19T00:00:00")
        # 假设业务上又撤销已还（本系统未开放该操作，仅演示幂等键仍生效）
        db.conn.execute(
            "UPDATE bills SET is_paid = 0 WHERE card_id = 'CARD_A' AND billing_month = '2025-02'"
        )
        db.conn.commit()
        assert check_reminders(db, date(2025, 2, 19)) == []
        assert db.count_reminders() == 2
