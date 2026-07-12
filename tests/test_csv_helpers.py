from __future__ import annotations

import csv
import unittest
from datetime import date
from decimal import Decimal
from io import StringIO

from litestar.exceptions import HTTPException

from app.models import Category, CsvImportConfig
from app.schemas import CsvPreviewPayload
from app.utils import build_csv_export, build_csv_preview_rows, clean_cell, csv_export_filename, csv_export_value, parse_amount, parse_csv_date
from tests.helpers import make_category, make_expense, make_member, make_tracker, make_user


class FakeQuery:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def filter(self, *_args: object) -> FakeQuery:
        return self

    def all(self) -> list[object]:
        return self.rows


class FakeSession:
    def __init__(self, categories: list[Category]) -> None:
        self.categories = categories

    def get(self, model: type[object], object_id: int) -> object | None:
        if model is Category:
            return next((category for category in self.categories if category.id == object_id), None)
        return None

    def query(self, _model: type[object]) -> FakeQuery:
        return FakeQuery(self.categories)


class CsvParsingTests(unittest.TestCase):
    def test_clean_cell_trims_whitespace_and_wrapping_quotes(self) -> None:
        self.assertEqual(clean_cell('  "Groceries"  '), "Groceries")
        self.assertEqual(clean_cell(None), "")

    def test_parse_csv_date_supports_common_formats(self) -> None:
        self.assertEqual(parse_csv_date("2026-07-11"), date(2026, 7, 11))
        self.assertEqual(parse_csv_date("07/11/2026"), date(2026, 7, 11))
        self.assertEqual(parse_csv_date("Jul 11, 2026"), date(2026, 7, 11))

    def test_parse_amount_strips_currency_formatting_and_can_invert(self) -> None:
        self.assertEqual(parse_amount('"$1,234.56"', False), Decimal("1234.56"))
        self.assertEqual(parse_amount('"$1,234.56"', True), Decimal("-1234.56"))


class CsvPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.marcos = make_user(1, "Marcos", "marcos@example.test")
        self.gabriela = make_user(2, "Gabriela", "gabriela@example.test")
        self.tracker = make_tracker(make_member(self.marcos, 50), make_member(self.gabriela, 50), currency="CAD")
        self.groceries = make_category(1, "Groceries", "#f1b84b")
        self.travel = make_category(2, "Travel", "#0369a1")
        self.session = FakeSession([self.groceries, self.travel])

    def test_build_csv_preview_maps_columns_and_trims_values(self) -> None:
        config = CsvImportConfig(
            id=1,
            tracker_id=1,
            name="Bank",
            field_map={"date": "Date", "description": "Memo", "amount": "Amount", "category": "Category", "paid_by": "Paid By"},
            invert_amount=True,
            currency="CAD",
            created_by_id=1,
        )
        payload = CsvPreviewPayload(
            config_id=1,
            csv_text='Date,Memo,Amount,Category,Paid By\n"2026-07-11"," Coffee ","$5.25","Travel","gabriela@example.test"\n',
            fallback_category_id=1,
            fallback_paid_by_id=1,
            is_shared=False,
        )

        preview = build_csv_preview_rows(self.session, self.tracker, 1, config, payload)

        self.assertEqual(preview["skipped"], [])
        self.assertEqual(
            preview["rows"][0],
            {
                "row_number": 2,
                "date": "2026-07-11",
                "category_id": 2,
                "category": "Travel",
                "paid_by_id": 2,
                "paid_by": "Gabriela",
                "amount": -5.25,
                "currency": "CAD",
                "description": "Coffee",
                "is_shared": False,
            },
        )

    def test_build_csv_preview_falls_back_when_optional_fields_do_not_match(self) -> None:
        config = CsvImportConfig(
            id=1,
            tracker_id=1,
            name="Bank",
            field_map={"date": "Date", "description": "Memo", "amount": "Amount", "category": "Category", "paid_by": "Paid By"},
            invert_amount=False,
            currency="CAD",
            created_by_id=1,
        )
        payload = CsvPreviewPayload(
            config_id=1,
            csv_text="Date,Memo,Amount,Category,Paid By\n2026-07-11,Coffee,5.25,Unknown,Nobody\n",
            fallback_category_id=1,
            fallback_paid_by_id=1,
        )

        preview = build_csv_preview_rows(self.session, self.tracker, 1, config, payload)

        self.assertEqual(preview["rows"][0]["category_id"], 1)
        self.assertEqual(preview["rows"][0]["paid_by_id"], 1)

    def test_build_csv_preview_reports_invalid_rows_without_aborting(self) -> None:
        config = CsvImportConfig(
            id=1,
            tracker_id=1,
            name="Bank",
            field_map={"date": "Date", "amount": "Amount"},
            invert_amount=False,
            currency="CAD",
            created_by_id=1,
        )
        payload = CsvPreviewPayload(config_id=1, csv_text="Date,Amount\nnot-a-date,5.25\n", fallback_category_id=1, fallback_paid_by_id=1)

        preview = build_csv_preview_rows(self.session, self.tracker, 1, config, payload)

        self.assertEqual(preview["rows"], [])
        self.assertEqual(preview["skipped"][0]["row"], 2)
        self.assertIn("Unsupported date format", preview["skipped"][0]["reason"])


class CsvExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.marcos = make_user(1, "Marcos")
        self.category = make_category(1, "Groceries", "#f1b84b")
        self.expense = make_expense("12.345", self.marcos, self.category, description="Market, run", shared=True)

    def test_csv_export_value_formats_supported_fields(self) -> None:
        self.assertEqual(csv_export_value(self.expense, "date", False), "2026-07-11")
        self.assertEqual(csv_export_value(self.expense, "amount", False), "12.35")
        self.assertEqual(csv_export_value(self.expense, "amount", True), "-12.35")
        self.assertEqual(csv_export_value(self.expense, "is_shared", False), "Shared")

    def test_build_csv_export_uses_configured_columns_and_quotes_csv_values(self) -> None:
        config = CsvImportConfig(
            id=1,
            tracker_id=1,
            name="Bank Export",
            field_map={"date": "Date", "description": "Description", "amount": "Amount", "category": "Category", "paid_by": "Paid By", "is_shared": "Type"},
            invert_amount=False,
            currency="CAD",
            created_by_id=1,
        )

        output = build_csv_export(config, [self.expense])
        rows = list(csv.DictReader(StringIO(output)))

        self.assertEqual(output.splitlines()[0], "Date,Description,Amount,Category,Paid By,Type")
        self.assertEqual(
            rows[0],
            {
                "Date": "2026-07-11",
                "Description": "Market, run",
                "Amount": "12.35",
                "Category": "Groceries",
                "Paid By": "Marcos",
                "Type": "Shared",
            },
        )

    def test_build_csv_export_rejects_duplicate_column_names(self) -> None:
        config = CsvImportConfig(
            id=1,
            tracker_id=1,
            name="Bad",
            field_map={"date": "Value", "amount": "Value"},
            invert_amount=False,
            currency="CAD",
            created_by_id=1,
        )

        with self.assertRaises(HTTPException):
            build_csv_export(config, [self.expense])

    def test_csv_export_filename_is_safe_for_download(self) -> None:
        tracker = make_tracker(make_member(self.marcos), currency="CAD")
        tracker.name = "Home / Canada"
        config = CsvImportConfig(id=1, tracker_id=1, name="MyBank CSV", field_map={}, invert_amount=False, currency="CAD", created_by_id=1)

        self.assertEqual(csv_export_filename(tracker, config, "2026-07"), "home---canada-mybank-csv-2026-07.csv")


if __name__ == "__main__":
    unittest.main()
