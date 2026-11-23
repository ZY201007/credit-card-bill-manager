"""金额计算测试：Decimal 精度、分期手续费、应还金额、最低还款分别验证。"""
from decimal import Decimal

from cardbill.domain import (
    installment_fee,
    minimum_payment,
    per_period_installment_amount,
    total_due,
)
from cardbill.money import D, money_sum, quantize, to_str


class TestDecimalPrecision:
    def test_float_is_not_trusted(self):
        # 0.1 + 0.2 用 float 会得到 0.30000000000000004，Decimal 必须精确
        assert money_sum(["0.1", "0.2"]) == Decimal("0.30")
        assert D(0.1) + D(0.2) == Decimal("0.3")

    def test_quantize_half_up(self):
        assert quantize("2.345") == Decimal("2.35")
        assert quantize("-2.345") == Decimal("-2.35")
        assert to_str("2.345") == "2.35"


class TestInstallmentFee:
    def test_exact(self):
        # 6000 × 0.006 × 6 = 216.00
        assert installment_fee("6000.00", "0.006", 6) == Decimal("216.00")

    def test_rounding(self):
        # 100.01 × 0.003 × 3 = 0.90009 -> 0.90
        assert installment_fee("100.01", "0.003", 3) == Decimal("0.90")

    def test_per_period_principal(self):
        assert per_period_installment_amount("6000.00", 6) == Decimal("1000.00")
        assert per_period_installment_amount("100.00", 3) == Decimal("33.33")

    def test_invalid_periods(self):
        import pytest

        with pytest.raises(ValueError):
            installment_fee("100", "0.01", 0)


class TestTotalDue:
    def test_components_are_separate(self):
        result = total_due("103.00", "194.16", "6020.00", "180.60")
        assert result == Decimal("6497.76")
        # 拆分项各自独立可查
        assert D("103.00") + D("194.16") + D("6020.00") + D("180.60") == result

    def test_zero(self):
        assert total_due("0", "0", "0", "0") == Decimal("0.00")


class TestMinimumPayment:
    def test_ten_percent(self):
        # 297.16 × 10% = 29.716 -> 29.72
        assert minimum_payment("297.16") == Decimal("29.72")

    def test_floor_wins(self):
        # 50 × 10% = 5 < 下限 10 -> 10.00
        assert minimum_payment("50.00") == Decimal("10.00")

    def test_custom_rate_and_floor(self):
        # CARD_C: 5% 且下限 20.00
        assert minimum_payment("6511.26", "0.05", "20.00") == Decimal("325.56")

    def test_zero_due(self):
        assert minimum_payment("0.00") == Decimal("0.00")
