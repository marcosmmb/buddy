from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile


DEFAULT_SPREADSHEET = Path("/Users/marcos/Downloads/Monthly Expenses (Canada).xlsx")
DEFAULT_TRACKER_NAME = "Monteal 2"
DEFAULT_CURRENCY = "CAD"
EXCEL_EPOCH = date(1899, 12, 30)
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
XML_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
PALETTE = [
    "#f1b84b",
    "#d99b25",
    "#285c9d",
    "#7c3aed",
    "#be123c",
    "#0369a1",
    "#9333ea",
    "#6b7280",
    "#b45309",
    "#f4c45d",
]


@dataclass(frozen=True)
class SheetInfo:
    name: str
    path: str
    month: str


@dataclass(frozen=True)
class ParsedExpense:
    sheet: str
    row_number: int
    date: date
    category: str
    amount: Decimal
    paid_by: str
    description: str
    is_shared: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import the Monthly Expenses Canada workbook into Buddy.")
    parser.add_argument("spreadsheet", nargs="?", type=Path, default=DEFAULT_SPREADSHEET)
    parser.add_argument("--tracker-name", default=DEFAULT_TRACKER_NAME)
    parser.add_argument("--currency", default=DEFAULT_CURRENCY)
    parser.add_argument("--database-url", help="Database URL to use before importing app.db")
    parser.add_argument("--admin-email", default=os.getenv("ADMIN_EMAIL", "admin@buddy.local"))
    parser.add_argument("--payer-email-domain", default="buddy.local")
    parser.add_argument("--missing-shared", choices=("shared", "individual"), default="shared")
    parser.add_argument("--replace", action="store_true", help="Delete an existing tracker with this name before importing.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and summarize the workbook without writing to the database.")
    return parser.parse_args()


def normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def column_name(cell_ref: str) -> str:
    return "".join(ch for ch in cell_ref if ch.isalpha()).upper()


def sheet_month(sheet_name: str) -> str | None:
    match = re.fullmatch(r"([A-Za-z]{3})\s+(\d{2})", sheet_name.strip())
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return f"20{int(match.group(2)):02d}-{month:02d}"


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def decimal_amount(value: Any) -> Decimal:
    return Decimal(str(value).strip())


def parse_excel_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("blank date")
    try:
        serial = Decimal(text)
    except Exception:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"unsupported date value {text!r}") from None
    return EXCEL_EPOCH + timedelta(days=int(serial))


def parse_bool(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "shared"}:
        return True
    if text in {"0", "false", "no", "n", "individual"}:
        return False
    return default


def read_shared_strings(workbook: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for node in root.findall("m:si", XML_NS):
        strings.append("".join(part.text or "" for part in node.findall(".//m:t", XML_NS)))
    return strings


def cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    value_node = cell.find("m:v", XML_NS)
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = cell.find("m:is", XML_NS)
        return "" if inline is None else "".join(part.text or "" for part in inline.findall(".//m:t", XML_NS))
    if value_node is None:
        return ""
    raw = value_node.text or ""
    if cell_type == "s":
        return shared_strings[int(raw)]
    if cell_type == "b":
        return raw == "1"
    return raw


def worksheet_rows(workbook: ZipFile, sheet_path: str, shared_strings: list[str]) -> list[dict[str, Any]]:
    root = ET.fromstring(workbook.read(sheet_path))
    rows: list[dict[str, Any]] = []
    for row in root.findall("m:sheetData/m:row", XML_NS):
        values: dict[str, Any] = {"__row__": int(row.attrib["r"])}
        for cell in row.findall("m:c", XML_NS):
            values[column_name(cell.attrib["r"])] = cell_value(cell, shared_strings)
        rows.append(values)
    return rows


def workbook_sheets(workbook: ZipFile) -> list[SheetInfo]:
    workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
    rels_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root.findall("pr:Relationship", XML_NS)}
    sheets: list[SheetInfo] = []
    for sheet in workbook_root.findall("m:sheets/m:sheet", XML_NS):
        name = sheet.attrib["name"]
        month = sheet_month(name)
        if month is None:
            continue
        rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = targets[rel_id]
        path = target if target.startswith("xl/") else f"xl/{target}"
        sheets.append(SheetInfo(name=name, path=path, month=month))
    return sheets


def find_header(rows: list[dict[str, Any]]) -> tuple[int, dict[str, str]]:
    for index, row in enumerate(rows[:10]):
        headers: dict[str, str] = {}
        for column, value in row.items():
            if column == "__row__":
                continue
            normalized = normalize_header(value)
            if normalized and normalized not in headers:
                headers[normalized] = column
        required = {"date": "date", "category": "category", "amount": "amount", "paidby": "paid_by"}
        if all(key in headers for key in required):
            columns = {target: headers[key] for key, target in required.items()}
            if "description" in headers:
                columns["description"] = headers["description"]
            if "shared" in headers:
                columns["shared"] = headers["shared"]
            return index, columns
        if all(key in headers for key in ("category", "amount", "paidby")):
            columns = {
                "date": "A",
                "category": headers["category"],
                "amount": headers["amount"],
                "paid_by": headers["paidby"],
            }
            if "description" in headers:
                columns["description"] = headers["description"]
            if "shared" in headers:
                columns["shared"] = headers["shared"]
            return index, columns
    raise ValueError("could not find Date, Category, Amount, and Paid by headers")


def parse_share_overrides(rows: list[dict[str, Any]], payers: set[str]) -> dict[str, Decimal]:
    shares: dict[str, Decimal] = {}
    for row in rows:
        name = str(row.get("O", "") or "").strip()
        if name not in payers:
            continue
        raw = row.get("S")
        if raw in (None, ""):
            continue
        value = Decimal(str(raw))
        shares[name] = money(value * 100 if value <= 1 else value)
    return shares


def parse_workbook(path: Path, missing_shared_default: bool) -> tuple[list[ParsedExpense], dict[str, dict[str, Decimal]]]:
    expenses: list[ParsedExpense] = []
    monthly_shares: dict[str, dict[str, Decimal]] = {}
    with ZipFile(path) as workbook:
        shared_strings = read_shared_strings(workbook)
        for sheet in workbook_sheets(workbook):
            rows = worksheet_rows(workbook, sheet.path, shared_strings)
            header_index, columns = find_header(rows)
            sheet_expenses: list[ParsedExpense] = []
            for row in rows[header_index + 1 :]:
                try:
                    raw_date = row.get(columns["date"])
                    raw_category = str(row.get(columns["category"], "") or "").strip()
                    raw_amount = row.get(columns["amount"])
                    raw_paid_by = str(row.get(columns["paid_by"], "") or "").strip()
                    if not raw_date or not raw_category or raw_amount in (None, "") or not raw_paid_by:
                        continue
                    amount = decimal_amount(raw_amount)
                    if amount == 0:
                        continue
                    sheet_expenses.append(
                        ParsedExpense(
                            sheet=sheet.name,
                            row_number=int(row["__row__"]),
                            date=parse_excel_date(raw_date),
                            category=raw_category,
                            amount=amount,
                            paid_by=raw_paid_by,
                            description=str(row.get(columns.get("description", ""), "") or "").strip(),
                            is_shared=parse_bool(row.get(columns.get("shared", "")), missing_shared_default),
                        )
                    )
                except Exception as exc:
                    raise ValueError(f"{sheet.name} row {row.get('__row__')}: {exc}") from exc
            expenses.extend(sheet_expenses)
            payers = {expense.paid_by for expense in sheet_expenses}
            shares = parse_share_overrides(rows, payers)
            if shares:
                monthly_shares[sheet.month] = shares
    return expenses, monthly_shares


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", value.strip().lower()).strip(".") or "user"


def print_summary(expenses: list[ParsedExpense], monthly_shares: dict[str, dict[str, Decimal]]) -> None:
    months = sorted({expense.date.strftime("%Y-%m") for expense in expenses})
    payers = sorted({expense.paid_by for expense in expenses})
    categories = sorted({expense.category for expense in expenses})
    total = sum((expense.amount for expense in expenses), Decimal("0"))
    shared = sum((expense.amount for expense in expenses if expense.is_shared), Decimal("0"))
    print(f"Parsed {len(expenses)} expenses across {len(months)} months.")
    print(f"Total: {money(total)} CAD; shared: {money(shared)} CAD; individual: {money(total - shared)} CAD")
    print(f"Payers: {', '.join(payers)}")
    print(f"Categories ({len(categories)}): {', '.join(categories)}")
    print(f"Monthly share overrides found for {len(monthly_shares)} months.")


def run_import(args: argparse.Namespace, expenses: list[ParsedExpense], monthly_shares: dict[str, dict[str, Decimal]]) -> None:
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault("ADMIN_EMAIL", args.admin_email)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from app.db import db_session, init_database
    from app.models import Category, Expense, Tracker, TrackerMember, TrackerMonthlyShare, User
    from app.security import hash_password

    init_database()
    with db_session() as session:
        admin = session.query(User).filter(User.email == args.admin_email.lower()).one_or_none()
        if admin is None:
            raise RuntimeError(f"Admin user {args.admin_email!r} was not found")

        existing = session.query(Tracker).filter(Tracker.name == args.tracker_name).one_or_none()
        if existing is not None:
            if not args.replace:
                raise RuntimeError(f"Tracker {args.tracker_name!r} already exists. Re-run with --replace to recreate it.")
            session.delete(existing)
            session.flush()

        users_by_name = {user.name.strip().lower(): user for user in session.query(User).all()}
        users_by_email = {user.email.strip().lower(): user for user in session.query(User).all()}
        payer_names = sorted({expense.paid_by for expense in expenses})
        payer_users: dict[str, User] = {}
        for payer in payer_names:
            user = users_by_name.get(payer.lower())
            if user is None:
                base_email = f"{slug(payer)}@{args.payer_email_domain}".lower()
                email = base_email
                suffix = 2
                while email in users_by_email:
                    email = f"{slug(payer)}.{suffix}@{args.payer_email_domain}".lower()
                    suffix += 1
                user = User(
                    email=email,
                    name=payer,
                    password_hash=hash_password(secrets.token_urlsafe(24)),
                    default_currency=args.currency,
                    theme="light",
                    is_admin=False,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                users_by_name[user.name.strip().lower()] = user
                users_by_email[user.email.strip().lower()] = user
            payer_users[payer] = user

        tracker = Tracker(name=args.tracker_name, default_currency=args.currency, created_by_id=admin.id)
        session.add(tracker)
        session.flush()

        payer_user_ids = {user.id for user in payer_users.values()}
        equal_share = Decimal("100") / Decimal(len(payer_user_ids)) if payer_user_ids else Decimal("0")
        member_ids = {admin.id, *payer_user_ids}
        for user_id in sorted(member_ids):
            session.add(
                TrackerMember(
                    tracker_id=tracker.id,
                    user_id=user_id,
                    role="owner" if user_id == admin.id else "member",
                    share_percent=equal_share if user_id in payer_user_ids else Decimal("0"),
                )
            )

        categories: dict[str, Category] = {}
        for index, name in enumerate(sorted({expense.category for expense in expenses})):
            category = Category(tracker_id=tracker.id, name=name, color=PALETTE[index % len(PALETTE)])
            session.add(category)
            session.flush()
            categories[name] = category

        for month, shares in monthly_shares.items():
            for payer, percent in shares.items():
                user = payer_users.get(payer)
                if user is None:
                    continue
                session.add(
                    TrackerMonthlyShare(
                        tracker_id=tracker.id,
                        user_id=user.id,
                        month=month,
                        share_percent=percent,
                    )
                )
            if admin.id not in payer_user_ids:
                session.add(
                    TrackerMonthlyShare(
                        tracker_id=tracker.id,
                        user_id=admin.id,
                        month=month,
                        share_percent=Decimal("0"),
                    )
                )

        for expense in expenses:
            session.add(
                Expense(
                    tracker_id=tracker.id,
                    category_id=categories[expense.category].id,
                    paid_by_id=payer_users[expense.paid_by].id,
                    date=expense.date,
                    amount=expense.amount,
                    currency=args.currency,
                    description=expense.description,
                    is_shared=expense.is_shared,
                )
            )
        session.flush()
        print(f"Imported {len(expenses)} expenses into tracker {args.tracker_name!r} (id={tracker.id}).")


def main() -> None:
    args = parse_args()
    missing_shared_default = args.missing_shared == "shared"
    expenses, monthly_shares = parse_workbook(args.spreadsheet, missing_shared_default)
    print_summary(expenses, monthly_shares)
    if args.dry_run:
        return
    run_import(args, expenses, monthly_shares)


if __name__ == "__main__":
    main()
