"""生成回归夹具：3 张卡 × 12 个月（2025 年）样例账单。

生成物：
    data/sample_bills/CARD_A.csv / CARD_B.csv / CARD_C.csv   待导入交易
    fixtures/expected_bills.csv                              独立参考计算的期望账单

关键点：expected_bills.csv 由本脚本内的「独立参考实现」算出，不调用 cardbill.domain，
因此回归测试比较的是「领域实现 vs 独立参考」，能有效捕捉金额/日期计算错误。

运行：python scripts/generate_fixtures.py
"""
from __future__ import annotations

import calendar
import csv
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BILL_DIR = ROOT / "data" / "sample_bills"
FIXTURE_DIR = ROOT / "fixtures"

CENT = Decimal("0.01")


def q(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# 卡配置：与 README / 测试保持一致
CARDS = [
    {"id": "CARD_A", "name": "招商银行经典白金卡", "statement_day": 5, "due_offset": 20, "rate": "0.10", "floor": "10.00"},
    {"id": "CARD_B", "name": "工商银行牡丹卡", "statement_day": 15, "due_offset": 25, "rate": "0.10", "floor": "10.00"},
    {"id": "CARD_C", "name": "建设银行龙卡", "statement_day": 28, "due_offset": 18, "rate": "0.05", "floor": "20.00"},
]

MONTHS = range(1, 13)
YEAR = 2025

# -------------------------------------------------- 独立参考实现（不依赖领域模块）


def ref_statement_date(year: int, month: int, statement_day: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(statement_day, last))


def ref_due_date(stmt: date, offset: int) -> date:
    return stmt + timedelta(days=offset)


def ref_min_payment(total: Decimal, rate: str, floor: str) -> Decimal:
    if total <= 0:
        return q(0)
    return q(max(total * Decimal(rate), Decimal(floor)))


# -------------------------------------------------- 交易数据生成


def make_transactions(card: dict, month: int) -> list[dict]:
    """返回某卡某月的全部交易行（含保留签名行）。"""
    cid = card["id"]
    idx = CARDS.index(card)
    stmt = ref_statement_date(YEAR, month, card["statement_day"])
    rows: list[dict] = []

    # 三笔普通消费，日期落在当期账单周期内
    amounts = [q(100 + idx * 50 + month * 7) + Decimal("0.11"),
               q((100 + idx * 50 + month * 7) / 2) + Decimal("0.25"),
               Decimal("33.30")]
    for i, amount in enumerate(amounts, start=1):
        txn_date = stmt - timedelta(days=[20, 10, 2][i - 1])
        rows.append({
            "card_id": cid,
            "txn_date": txn_date.isoformat(),
            "amount": f"{amount:.2f}",
            "description": f"消费-{month}月-{i}",
            "signature": f"TXN-{cid}-{month:02d}-{i}",
        })

    # 上期余额
    prev_balance = q((idx + 1) * 100 + month * 3)
    rows.append({
        "card_id": cid,
        "txn_date": stmt.isoformat(),
        "amount": f"{prev_balance:.2f}",
        "description": "上期结转",
        "signature": "__PREV_BALANCE__",
    })

    # 偶数月有分期：本金 + 费率/期数写在 description，手续费由参考实现独立计算
    if month % 2 == 0:
        principal = q(6000 + idx * 1000 + month * 10)
        fee_rate = Decimal("0.005") + Decimal(idx) * Decimal("0.001")
        periods = 6 + idx
        rows.append({
            "card_id": cid,
            "txn_date": stmt.isoformat(),
            "amount": f"{principal:.2f}",
            "description": f"{fee_rate},{periods}",
            "signature": "__INSTALLMENT__",
        })

    return rows


def ref_expected_bill(card: dict, month: int) -> dict:
    """用独立参考实现算出该期账单的期望值。"""
    cid = card["id"]
    idx = CARDS.index(card)
    stmt = ref_statement_date(YEAR, month, card["statement_day"])
    due = ref_due_date(stmt, card["due_offset"])

    new_transactions = sum(
        (Decimal(r["amount"]) for r in make_transactions(card, month)
         if not r["signature"].startswith("__")),
        Decimal("0"),
    )
    prev_balance = q((idx + 1) * 100 + month * 3)

    installment = Decimal("0")
    fee = Decimal("0")
    if month % 2 == 0:
        principal = q(6000 + idx * 1000 + month * 10)
        fee_rate = Decimal("0.005") + Decimal(idx) * Decimal("0.001")
        periods = 6 + idx
        installment = principal
        fee = q(principal * fee_rate * periods)

    total = q(prev_balance + new_transactions + installment + fee)
    minimum = ref_min_payment(total, card["rate"], card["floor"])

    return {
        "card_id": cid,
        "billing_month": f"{YEAR}-{month:02d}",
        "statement_date": stmt.isoformat(),
        "due_date": due.isoformat(),
        "previous_balance": f"{prev_balance:.2f}",
        "new_transactions": f"{new_transactions:.2f}",
        "installment_amount": f"{installment:.2f}",
        "installment_fee": f"{fee:.2f}",
        "total_due": f"{total:.2f}",
        "minimum_payment": f"{minimum:.2f}",
    }


# -------------------------------------------------- 主流程


def main() -> None:
    BILL_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    expected_rows: list[dict] = []
    for card in CARDS:
        csv_path = BILL_DIR / f"{card['id']}.csv"
        all_rows: list[dict] = []
        for month in MONTHS:
            all_rows.extend(make_transactions(card, month))
            expected_rows.append(ref_expected_bill(card, month))

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["card_id", "txn_date", "amount", "description", "signature"]
            )
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"生成 {csv_path} ({len(all_rows)} 行)")

    expected_path = FIXTURE_DIR / "expected_bills.csv"
    with open(expected_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(expected_rows[0].keys()))
        writer.writeheader()
        writer.writerows(expected_rows)
    print(f"生成 {expected_path} ({len(expected_rows)} 期账单)")


if __name__ == "__main__":
    main()
