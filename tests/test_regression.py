"""回归夹具：3 张卡 × 12 个月 = 36 期账单，金额与日期零差错。

期望值来自 fixtures/expected_bills.csv（由独立参考实现生成，见
scripts/generate_fixtures.py），不与 cardbill.domain 共享逻辑。
"""
import csv
from pathlib import Path

from cardbill.importer import import_csv
from cardbill.money import D
from cardbill.service import rebuild_bills

ROOT = Path(__file__).resolve().parent.parent
BILL_DIR = ROOT / "data" / "sample_bills"
EXPECTED_FILE = ROOT / "fixtures" / "expected_bills.csv"

# 领域计算字段与期望 CSV 列的对应关系
FIELD_MAP = {
    "statement_date": lambda b: b.statement_date.isoformat(),
    "due_date": lambda b: b.due_dt.isoformat(),
    "previous_balance": lambda b: f"{b.previous_balance:.2f}",
    "new_transactions": lambda b: f"{b.new_transactions:.2f}",
    "installment_amount": lambda b: f"{b.installment_amount:.2f}",
    "installment_fee": lambda b: f"{b.installment_fee_amount:.2f}",
    "total_due": lambda b: f"{b.total_due_amount:.2f}",
    "minimum_payment": lambda b: f"{b.minimum_payment_amount:.2f}",
}


def _load_expected() -> dict[tuple[str, str], dict]:
    with open(EXPECTED_FILE, encoding="utf-8", newline="") as f:
        return {
            (r["card_id"], r["billing_month"]): r for r in csv.DictReader(f)
        }


def _setup(db):
    for card_file in sorted(BILL_DIR.glob("CARD_*.csv")):
        stats = import_csv(db, card_file)
        assert stats.imported == 54, stats  # 每卡 12 月 × (3 消费 + 余额 + 分期)
        assert stats.duplicates == 0
    return rebuild_bills(db)


def test_fixture_files_exist():
    assert len(list(BILL_DIR.glob("CARD_*.csv"))) == 3
    assert EXPECTED_FILE.exists()


def test_three_cards_twelve_months_zero_amount_errors(db):
    bills = _setup(db)
    assert len(bills) == 36  # 3 卡 × 12 月

    expected = _load_expected()
    assert len(expected) == 36

    errors: list[str] = []
    for bill in bills:
        key = (bill.card_id, bill.billing_month)
        exp = expected.get(key)
        if exp is None:
            errors.append(f"缺少期望值: {key}")
            continue
        for column, getter in FIELD_MAP.items():
            actual = getter(bill)
            if actual != exp[column]:
                errors.append(
                    f"{key} {column}: 实际={actual} 期望={exp[column]}"
                )

    assert errors == [], "金额/日期零差错回归失败:\n" + "\n".join(errors)


def test_reimport_is_idempotent_and_bills_stable(db):
    """重复导入同一批 CSV 不改变账单金额（去重 + upsert 幂等）。"""
    first = _setup(db)
    snapshot = {(b.card_id, b.billing_month): b.total_due_amount for b in first}

    for card_file in sorted(BILL_DIR.glob("CARD_*.csv")):
        stats = import_csv(db, card_file)
        assert stats.imported == 0 and stats.duplicates == 54
    second = rebuild_bills(db)

    assert len(second) == 36
    for bill in second:
        key = (bill.card_id, bill.billing_month)
        assert bill.total_due_amount == snapshot[key]
        assert D(snapshot[key]) == bill.total_due_amount
