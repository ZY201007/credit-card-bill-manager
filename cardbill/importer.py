"""CSV 账单导入与去重。

CSV 列（首行为表头，顺序固定）：
    card_id,txn_date,amount,description,signature

去重键 = 卡号 + 归属账单月 + 金额(分) + 交易签名。
其中「归属账单月」由交易日 + 该卡账单日规则推导（见 domain.statement_month_for_date），
因此同一笔交易即使被重复导入、或日期写法不同（只要落在同一账单月），都会被去重。

保留签名（用于把账单级信息随交易一起导入，供 rebuild_bills 使用）：
    __PREV_BALANCE__      -> 上期余额，金额写入 previous_balance
    __INSTALLMENT__       -> 分期本金，description 格式 "每期费率,期数"，如 "0.006,6"
                             摊销手续费由 domain.installment_fee 计算
    __INSTALLMENT_FEE__   -> 直接给定分期手续费（金额即手续费），无需费率/期数
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .db import Database
from .domain import statement_month_for_date
from .money import D
from .models import Transaction

EXPECTED_HEADER = ["card_id", "txn_date", "amount", "description", "signature"]

SIG_PREV_BALANCE = "__PREV_BALANCE__"
SIG_INSTALLMENT = "__INSTALLMENT__"
SIG_INSTALLMENT_FEE = "__INSTALLMENT_FEE__"


@dataclass
class ImportStats:
    """一次导入的统计结果。"""

    total_rows: int = 0
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _looks_like_header(row: dict) -> bool:
    return "card_id" in row and "txn_date" in row


def parse_row(row: dict, db: Database) -> tuple[Transaction | None, str | None]:
    """把一行 CSV 解析为 Transaction。返回 (txn, error)。"""
    card_id = (row.get("card_id") or "").strip()
    card = db.get_card(card_id)
    if card is None:
        return None, f"未知卡号: {card_id}"

    try:
        txn_date = date.fromisoformat((row.get("txn_date") or "").strip())
    except ValueError:
        return None, f"非法日期: {row.get('txn_date')}"

    try:
        amount = D(row.get("amount") or "0")
    except Exception:
        return None, f"非法金额: {row.get('amount')}"

    signature = (row.get("signature") or "").strip()
    if not signature:
        return None, "签名为空"

    stmt_month = statement_month_for_date(txn_date, card.statement_day)
    return (
        Transaction(
            card_id=card_id,
            txn_date=txn_date,
            amount=amount,
            signature=signature,
            description=(row.get("description") or "").strip(),
            statement_month=stmt_month,
        ),
        None,
    )


def import_csv(db: Database, source: str | Path | io.StringIO) -> ImportStats:
    """从 CSV 文件（或文件对象）导入交易，自动去重。"""
    stats = ImportStats()

    if isinstance(source, io.StringIO):
        handle = source
        should_close = False
    else:
        handle = open(Path(source), "r", encoding="utf-8-sig", newline="")
        should_close = True

    try:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            stats.errors.append("CSV 缺少表头")
            return stats
        # 校验表头
        missing = [c for c in EXPECTED_HEADER if c not in reader.fieldnames]
        if missing:
            stats.errors.append(f"CSV 表头缺少列: {', '.join(missing)}")
            return stats

        for row in reader:
            stats.total_rows += 1
            if not any((v or "").strip() for v in row.values()):
                stats.skipped += 1
                continue
            txn, err = parse_row(row, db)
            if err:
                stats.errors.append(f"第 {stats.total_rows + 1} 行: {err}")
                stats.skipped += 1
                continue
            if txn is not None and db.insert_transaction(txn):
                stats.imported += 1
            else:
                stats.duplicates += 1
    finally:
        if should_close:
            handle.close()

    return stats
