"""金额工具：统一使用 Decimal，规避二进制浮点误差。

简历中「金额计算零差错」的关键实现：所有金额从输入解析开始就转成 Decimal，
所有运算（加减乘、费率计算、四舍五入）都在此基础上进行，最后统一量化到「分」。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# 货币最小单位：分（0.01）
CENT = Decimal("0.01")


def D(value) -> Decimal:
    """把任意常见输入安全地转换为 Decimal。

    - Decimal / int：直接转换；
    - str：先 strip 再转（避免 int/float 中转导致的精度丢失）；
    - float：先转成 str 再转 Decimal，避免 0.1 -> 0.1000000000000000055 问题。
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(str(value).strip())


def quantize(value) -> Decimal:
    """按「分」四舍五入（银行家常用 ROUND_HALF_UP，即四舍五入）。"""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_str(value) -> str:
    """把金额序列化为定点字符串，便于 SQLite 无损存储。"""
    return format(quantize(value), "f")


def money_sum(values) -> Decimal:
    """对一组金额求和并返回量化后的 Decimal。"""
    total = Decimal("0")
    for value in values:
        total += D(value)
    return quantize(total)
