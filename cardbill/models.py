"""数据模型：使用 dataclass 描述领域对象，与持久化层解耦。

金额字段统一用 Decimal；入库 / 出库时由 db.py 负责与字符串互转。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import D


@dataclass(frozen=True)
class CreditCard:
    """信用卡配置。

    id: 卡片唯一标识
    name: 卡片名称
    statement_day: 账单日日号（1~31，月末自动吸附）
    due_offset_days: 账单日到到期日的宽限期天数
    min_payment_rate: 最低还款比例（Decimal 字符串，如 "0.10"）
    min_payment_floor: 最低还款下限（Decimal 字符串，如 "10.00"）
    currency: 币种
    """

    id: str
    name: str
    statement_day: int
    due_offset_days: int
    min_payment_rate: str = "0.10"
    min_payment_floor: str = "10.00"
    currency: str = "CNY"


@dataclass(frozen=True)
class Transaction:
    """一笔交易（消费）。

    signature: 交易签名，用于去重（如商户流水号）；同一张卡同月同金额同签名视为重复。
    statement_month: 归属账单月 'YYYY-MM'，由交易日 + 账单日规则计算得出。
    """

    card_id: str
    txn_date: date
    amount: Decimal
    signature: str
    description: str = ""
    statement_month: str = ""

    @property
    def dedup_key(self) -> str:
        """去重键：卡号 + 账单月 + 金额(分) + 签名。"""
        return "|".join(
            [self.card_id, self.statement_month, f"{self.amount:.2f}", self.signature]
        )


@dataclass
class Bill:
    """一期账单（应还汇总）。"""

    card_id: str
    billing_month: str
    statement_date: date
    due_dt: date
    previous_balance: Decimal = D("0")
    new_transactions: Decimal = D("0")
    installment_amount: Decimal = D("0")
    installment_fee_amount: Decimal = D("0")
    total_due_amount: Decimal = D("0")
    minimum_payment_amount: Decimal = D("0")
    is_paid: bool = False
    paid_at: str | None = None
    transaction_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class Reminder:
    """一条提醒记录（对应 check_reminders 的产出）。"""

    card_id: str
    billing_cycle: str
    strategy: str  # 'FULL' 或 'MINIMUM'
    due_dt: date
    amount: Decimal
    days_to_due: int


# 提醒策略常量
STRATEGY_FULL = "FULL"  # 全额提醒
STRATEGY_MINIMUM = "MINIMUM"  # 最低提醒
ALL_STRATEGIES = (STRATEGY_FULL, STRATEGY_MINIMUM)
