"""CSV 导入去重测试。"""
import io

from cardbill.importer import import_csv

HEADER = "card_id,txn_date,amount,description,signature\n"


def _csv(*rows: str) -> io.StringIO:
    return io.StringIO(HEADER + "\n".join(rows) + "\n")


class TestDeduplication:
    def test_import_same_csv_twice(self, db):
        content = _csv(
            "CARD_A,2025-01-03,123.45,超市,TXN-1",
            "CARD_A,2025-01-04,88.00,咖啡,TXN-2",
        )
        first = import_csv(db, content)
        assert first.imported == 2 and first.duplicates == 0

        second = import_csv(db, _csv(
            "CARD_A,2025-01-03,123.45,超市,TXN-1",
            "CARD_A,2025-01-04,88.00,咖啡,TXN-2",
        ))
        assert second.imported == 0 and second.duplicates == 2
        assert len(db.list_transactions()) == 2

    def test_different_date_same_cycle_same_key_still_dedupes(self, db):
        # 账单日=5：1/3 与 1/4 同属 2025-01 账单；金额与签名一致 -> 去重
        import_csv(db, _csv("CARD_A,2025-01-03,50.00,商户, SIG-X"))
        stats = import_csv(db, _csv("CARD_A,2025-01-04,50.00,商户, SIG-X"))
        assert stats.duplicates == 1
        assert len(db.list_transactions()) == 1

    def test_different_signature_not_deduped(self, db):
        import_csv(db, _csv("CARD_A,2025-01-03,50.00,商户, SIG-X"))
        stats = import_csv(db, _csv("CARD_A,2025-01-03,50.00,商户, SIG-Y"))
        assert stats.imported == 1
        assert len(db.list_transactions()) == 2

    def test_unknown_card_is_skipped(self, db):
        stats = import_csv(db, _csv("NO_SUCH_CARD,2025-01-03,50.00,商户, SIG-1"))
        assert stats.imported == 0
        assert stats.skipped == 1
        assert stats.errors


class TestStatementMonthDerivation:
    def test_txn_after_statement_day_goes_to_next_month(self, db):
        # CARD_A 账单日=5；1/6 应归入 2025-02
        import_csv(db, _csv("CARD_A,2025-01-06,30.00,商户, SIG-1"))
        txns = db.list_transactions()
        assert txns[0].statement_month == "2025-02"

    def test_txn_on_statement_day_goes_to_current_month(self, db):
        import_csv(db, _csv("CARD_A,2025-01-05,30.00,商户, SIG-2"))
        assert db.list_transactions()[0].statement_month == "2025-01"

    def test_blank_rows_skipped(self, db):
        stats = import_csv(db, _csv(",,,,", "CARD_A,2025-01-05,30.00,商户, SIG-3"))
        assert stats.imported == 1 and stats.skipped == 1
