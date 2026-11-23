"""SQLite 持久化层。

去重与提醒幂等都落在数据库唯一约束上，保证「无论运行多少次，结果一致」：
    - transactions.dedup_key UNIQUE            -> 同一交易只入库一次；
    - bills(card_id, billing_month) UNIQUE      -> 一期账单只汇总一次；
    - reminders(card_id, billing_cycle, strategy) UNIQUE -> 同一策略同一周期只推送一次。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .money import D, to_str
from .models import Bill, CreditCard, Reminder, Transaction

# ------------------------------------------------------------------ 建表语句

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    statement_day     INTEGER NOT NULL,
    due_offset_days   INTEGER NOT NULL,
    min_payment_rate  TEXT NOT NULL DEFAULT '0.10',
    min_payment_floor TEXT NOT NULL DEFAULT '10.00',
    currency          TEXT NOT NULL DEFAULT 'CNY'
);

CREATE TABLE IF NOT EXISTS transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id          TEXT NOT NULL,
    txn_date         TEXT NOT NULL,
    amount           TEXT NOT NULL,
    signature        TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    statement_month  TEXT NOT NULL,
    dedup_key        TEXT NOT NULL UNIQUE,
    FOREIGN KEY (card_id) REFERENCES cards(id)
);

CREATE TABLE IF NOT EXISTS bills (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id               TEXT NOT NULL,
    billing_month         TEXT NOT NULL,
    statement_date        TEXT NOT NULL,
    due_date              TEXT NOT NULL,
    previous_balance      TEXT NOT NULL DEFAULT '0.00',
    new_transactions      TEXT NOT NULL DEFAULT '0.00',
    installment_amount    TEXT NOT NULL DEFAULT '0.00',
    installment_fee       TEXT NOT NULL DEFAULT '0.00',
    total_due             TEXT NOT NULL DEFAULT '0.00',
    minimum_payment       TEXT NOT NULL DEFAULT '0.00',
    is_paid               INTEGER NOT NULL DEFAULT 0,
    paid_at               TEXT,
    UNIQUE (card_id, billing_month),
    FOREIGN KEY (card_id) REFERENCES cards(id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id        TEXT NOT NULL,
    billing_cycle  TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    due_date       TEXT NOT NULL,
    amount         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (card_id, billing_cycle, strategy),
    FOREIGN KEY (card_id) REFERENCES cards(id)
);

CREATE INDEX IF NOT EXISTS idx_txn_card_month ON transactions(card_id, statement_month);
CREATE INDEX IF NOT EXISTS idx_bills_due ON bills(due_date);
"""

# ------------------------------------------------------------------ 转换辅助


def _date_to_str(value: date) -> str:
    return value.isoformat()


def _str_to_date(value: str) -> date:
    return date.fromisoformat(value)


# ------------------------------------------------------------------ 数据库封装


class Database:
    """对 sqlite3.Connection 的薄封装，提供领域对象级别的读写接口。"""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------ 卡片

    def add_card(self, card: CreditCard) -> None:
        self.conn.execute(
            """INSERT INTO cards
               (id, name, statement_day, due_offset_days,
                min_payment_rate, min_payment_floor, currency)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                card.id,
                card.name,
                card.statement_day,
                card.due_offset_days,
                str(card.min_payment_rate),
                str(card.min_payment_floor),
                card.currency,
            ),
        )
        self.conn.commit()

    def get_card(self, card_id: str) -> CreditCard | None:
        row = self.conn.execute(
            "SELECT * FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        return _row_to_card(row) if row else None

    def list_cards(self) -> list[CreditCard]:
        rows = self.conn.execute("SELECT * FROM cards ORDER BY id").fetchall()
        return [_row_to_card(r) for r in rows]

    # ------------------------------------------------------------ 交易（去重）

    def insert_transaction(self, txn: Transaction) -> bool:
        """插入一笔交易。返回 True 表示新插入，False 表示命中去重键被忽略。"""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO transactions
               (card_id, txn_date, amount, signature, description,
                statement_month, dedup_key)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                txn.card_id,
                _date_to_str(txn.txn_date),
                to_str(txn.amount),
                txn.signature,
                txn.description,
                txn.statement_month,
                txn.dedup_key,
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def list_transactions(
        self, card_id: str | None = None, statement_month: str | None = None
    ) -> list[Transaction]:
        sql = "SELECT * FROM transactions WHERE 1=1"
        params: list = []
        if card_id is not None:
            sql += " AND card_id = ?"
            params.append(card_id)
        if statement_month is not None:
            sql += " AND statement_month = ?"
            params.append(statement_month)
        sql += " ORDER BY txn_date, id"
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_txn(r) for r in rows]

    # ------------------------------------------------------------ 账单

    def upsert_bill(self, bill: Bill) -> None:
        """写入 / 覆盖一期账单（按 card_id + billing_month 唯一）。"""
        self.conn.execute(
            """INSERT INTO bills
               (card_id, billing_month, statement_date, due_date,
                previous_balance, new_transactions, installment_amount,
                installment_fee, total_due, minimum_payment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(card_id, billing_month) DO UPDATE SET
                 statement_date=excluded.statement_date,
                 due_date=excluded.due_date,
                 previous_balance=excluded.previous_balance,
                 new_transactions=excluded.new_transactions,
                 installment_amount=excluded.installment_amount,
                 installment_fee=excluded.installment_fee,
                 total_due=excluded.total_due,
                 minimum_payment=excluded.minimum_payment""",
            (
                bill.card_id,
                bill.billing_month,
                _date_to_str(bill.statement_date),
                _date_to_str(bill.due_dt),
                to_str(bill.previous_balance),
                to_str(bill.new_transactions),
                to_str(bill.installment_amount),
                to_str(bill.installment_fee_amount),
                to_str(bill.total_due_amount),
                to_str(bill.minimum_payment_amount),
            ),
        )
        self.conn.commit()

    def get_bill(self, card_id: str, billing_month: str) -> Bill | None:
        row = self.conn.execute(
            "SELECT * FROM bills WHERE card_id = ? AND billing_month = ?",
            (card_id, billing_month),
        ).fetchone()
        return _row_to_bill(row) if row else None

    def list_bills(self, paid: bool | None = None) -> list[Bill]:
        sql = "SELECT * FROM bills WHERE 1=1"
        params: list = []
        if paid is not None:
            sql += " AND is_paid = ?"
            params.append(1 if paid else 0)
        sql += " ORDER BY due_date, card_id"
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_bill(r) for r in rows]

    def mark_paid(
        self, card_id: str, billing_month: str, paid_at: str
    ) -> bool:
        """标记账单已还；返回是否有记录被更新。"""
        cur = self.conn.execute(
            """UPDATE bills SET is_paid = 1, paid_at = ?
               WHERE card_id = ? AND billing_month = ?""",
            (paid_at, card_id, billing_month),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------ 提醒（幂等）

    def insert_reminder(self, reminder: Reminder, created_at: str) -> bool:
        """写入提醒。返回 True 表示本次新增，False 表示已存在（被唯一约束拦截）。"""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO reminders
               (card_id, billing_cycle, strategy, due_date, amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                reminder.card_id,
                reminder.billing_cycle,
                reminder.strategy,
                _date_to_str(reminder.due_dt),
                to_str(reminder.amount),
                created_at,
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def list_reminders(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reminders ORDER BY created_at, card_id, billing_cycle, strategy"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_reminders(
        self, card_id: str | None = None, strategy: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM reminders WHERE 1=1"
        params: list = []
        if card_id is not None:
            sql += " AND card_id = ?"
            params.append(card_id)
        if strategy is not None:
            sql += " AND strategy = ?"
            params.append(strategy)
        return int(self.conn.execute(sql, params).fetchone()["n"])


# ------------------------------------------------------------------ 行 -> 对象


def _row_to_card(row: sqlite3.Row) -> CreditCard:
    return CreditCard(
        id=row["id"],
        name=row["name"],
        statement_day=row["statement_day"],
        due_offset_days=row["due_offset_days"],
        min_payment_rate=row["min_payment_rate"],
        min_payment_floor=row["min_payment_floor"],
        currency=row["currency"],
    )


def _row_to_txn(row: sqlite3.Row) -> Transaction:
    return Transaction(
        card_id=row["card_id"],
        txn_date=_str_to_date(row["txn_date"]),
        amount=D(row["amount"]),
        signature=row["signature"],
        description=row["description"],
        statement_month=row["statement_month"],
    )


def _row_to_bill(row: sqlite3.Row) -> Bill:
    return Bill(
        card_id=row["card_id"],
        billing_month=row["billing_month"],
        statement_date=_str_to_date(row["statement_date"]),
        due_dt=_str_to_date(row["due_date"]),
        previous_balance=D(row["previous_balance"]),
        new_transactions=D(row["new_transactions"]),
        installment_amount=D(row["installment_amount"]),
        installment_fee_amount=D(row["installment_fee"]),
        total_due_amount=D(row["total_due"]),
        minimum_payment_amount=D(row["minimum_payment"]),
        is_paid=bool(row["is_paid"]),
        paid_at=row["paid_at"],
    )
