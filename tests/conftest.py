"""pytest 公共夹具：统一准备内存数据库与三张测试卡。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cardbill.db import Database  # noqa: E402
from cardbill.models import CreditCard  # noqa: E402
from scripts.generate_fixtures import CARDS  # noqa: E402


def make_db() -> Database:
    """创建一个已建表、已写入三张卡的内存数据库。"""
    db = Database(":memory:")
    db.init_schema()
    for c in CARDS:
        db.add_card(
            CreditCard(
                id=c["id"],
                name=c["name"],
                statement_day=c["statement_day"],
                due_offset_days=c["due_offset"],
                min_payment_rate=c["rate"],
                min_payment_floor=c["floor"],
            )
        )
    return db


@pytest.fixture()
def db() -> Database:
    database = make_db()
    yield database
    database.close()
