from __future__ import annotations


KNOWN_TASK_KINDS = {
    "date",
    "price",
    "pagination",
    "force_failure",
    "email",
    "slug",
    "inventory",
    "csv",
    "timezone",
    "retry",
}


def is_known_task_kind(task_kind: str | None) -> bool:
    return str(task_kind or "").strip().lower() in KNOWN_TASK_KINDS


def source_path_for_kind(task_kind: str) -> str | None:
    return {
        "date": "src/date_parser.py",
        "price": "src/price.py",
        "pagination": "src/pagination.py",
        "force_failure": "src/sample.py",
        "email": "src/email_normalizer.py",
        "slug": "src/slug_generator.py",
        "inventory": "src/inventory.py",
        "csv": "src/csv_counter.py",
        "timezone": "src/timezone_formatter.py",
        "retry": "src/retry.py",
    }.get(task_kind)


def patch_for_kind(task_kind: str) -> str:
    if task_kind == "force_failure":
        return (
            "diff --git a/src/sample.py b/src/sample.py\n"
            "--- a/src/sample.py\n"
            "+++ b/src/sample.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def always_wrong() -> int:\n"
            "-    return 0\n"
            "+    return 1\n"
        )
    if task_kind == "email":
        return (
            "diff --git a/src/email_normalizer.py b/src/email_normalizer.py\n"
            "--- a/src/email_normalizer.py\n"
            "+++ b/src/email_normalizer.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def normalize_email(value):\n"
            "-    return value.lower()\n"
            "+    return value.strip().lower()\n"
        )
    if task_kind == "slug":
        return (
            "diff --git a/src/slug_generator.py b/src/slug_generator.py\n"
            "--- a/src/slug_generator.py\n"
            "+++ b/src/slug_generator.py\n"
            "@@ -1,2 +1,4 @@\n"
            "+import re\n"
            "+\n"
            " def slugify(value):\n"
            "-    return value.lower().replace(\" \", \"-\")\n"
            "+    return re.sub(r\"-+\", \"-\", re.sub(r\"[^a-z0-9]+\", \"-\", value.lower())).strip(\"-\")\n"
        )
    if task_kind == "inventory":
        return (
            "diff --git a/src/inventory.py b/src/inventory.py\n"
            "--- a/src/inventory.py\n"
            "+++ b/src/inventory.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def should_reorder(stock, threshold):\n"
            "-    return stock < threshold\n"
            "+    return stock <= threshold\n"
        )
    if task_kind == "csv":
        return (
            "diff --git a/src/csv_counter.py b/src/csv_counter.py\n"
            "--- a/src/csv_counter.py\n"
            "+++ b/src/csv_counter.py\n"
            "@@ -1,2 +1,5 @@\n"
            "+import csv\n"
            "+from io import StringIO\n"
            "+\n"
            " def count_data_rows(text):\n"
            "-    return len(text.splitlines())\n"
            "+    return sum(1 for row in csv.reader(StringIO(text)) if any(row)) - 1\n"
        )
    if task_kind == "timezone":
        return (
            "diff --git a/src/timezone_formatter.py b/src/timezone_formatter.py\n"
            "--- a/src/timezone_formatter.py\n"
            "+++ b/src/timezone_formatter.py\n"
            "@@ -1,4 +1,4 @@\n"
            " def format_offset(minutes):\n"
            "-    hours = minutes // 60\n"
            "-    mins = minutes % 60\n"
            "-    return f\"{hours}:{mins}\"\n"
            "+    sign = \"+\" if minutes >= 0 else \"-\"\n"
            "+    absolute = abs(minutes)\n"
            "+    return f\"{sign}{absolute // 60:02d}:{absolute % 60:02d}\"\n"
        )
    if task_kind == "retry":
        return (
            "diff --git a/src/retry.py b/src/retry.py\n"
            "--- a/src/retry.py\n"
            "+++ b/src/retry.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def backoff_delay(attempt, base=1, cap=30):\n"
            "-    return min(cap, base * 2 ** attempt)\n"
            "+    return min(cap, base * 2 ** max(0, attempt - 1))\n"
        )
    if task_kind == "pagination":
        return (
            "diff --git a/src/pagination.py b/src/pagination.py\n"
            "--- a/src/pagination.py\n"
            "+++ b/src/pagination.py\n"
            "@@ -2 +2 @@\n"
            "-    return total_items // page_size\n"
            "+    return max(1, (total_items + page_size - 1) // page_size)\n"
        )
    if task_kind == "price":
        return (
            "diff --git a/src/price.py b/src/price.py\n"
            "--- a/src/price.py\n"
            "+++ b/src/price.py\n"
            "@@ -1,2 +1,5 @@\n"
            "+from decimal import Decimal, ROUND_HALF_UP\n"
            "+\n"
            " def total_with_tax(amount: float, tax_rate: float) -> float:\n"
            "-    return amount * (1 + tax_rate)\n"
            "+    total = Decimal(str(amount)) * (Decimal(\"1\") + Decimal(str(tax_rate)))\n"
            "+    return float(total.quantize(Decimal(\"0.01\"), rounding=ROUND_HALF_UP))\n"
        )
    return (
        "diff --git a/src/date_parser.py b/src/date_parser.py\n"
        "--- a/src/date_parser.py\n"
        "+++ b/src/date_parser.py\n"
        "@@ -1,5 +1,10 @@\n"
        "-from datetime import date\n"
        "+from datetime import date\n"
        " \n"
        " \n"
        "-def parse_date(value: str) -> date:\n"
        "-    return date.fromisoformat(value)\n"
        "+def parse_date(value: str) -> date | None:\n"
        "+    if not value:\n"
        "+        return None\n"
        "+    try:\n"
        "+        return date.fromisoformat(value)\n"
        "+    except ValueError:\n"
        "+        return None\n"
    )
