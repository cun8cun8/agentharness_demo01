"""Normalize common test-runner summaries without treating log text as truth.

JUnit XML is preferred whenever a test command emits it. Text parsing remains a
best-effort compatibility path for existing repositories and is labelled as
such in the Tool Call evidence.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree


def parse_test_result(stdout: str, stderr: str = "") -> dict[str, int | str]:
    text = f"{stdout}\n{stderr}"
    pytest = _pytest_summary(text)
    if pytest is not None:
        return {**pytest, "result_format": "pytest_text"}
    maven = _maven_summary(text)
    if maven is not None:
        return {**maven, "result_format": "maven_text"}
    jest = _jest_summary(text)
    if jest is not None:
        return {**jest, "result_format": "jest_text"}
    tap = _tap_summary(text)
    if tap is not None:
        return {**tap, "result_format": "tap_text"}
    return {
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_skipped": 0,
        "tests_total": 0,
        "result_format": "unstructured",
    }


def parse_junit_xml(path: Path) -> dict[str, int | str] | None:
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError):
        return None
    suites = [root] if root.tag.endswith("testsuite") else list(root.iter())
    total = failed = skipped = 0
    seen = False
    for suite in suites:
        if not suite.tag.endswith("testsuite"):
            continue
        seen = True
        total += _integer_attribute(suite.attrib.get("tests"))
        failed += _integer_attribute(suite.attrib.get("failures"))
        failed += _integer_attribute(suite.attrib.get("errors"))
        skipped += _integer_attribute(suite.attrib.get("skipped"))
    if not seen:
        return None
    return {
        "tests_passed": max(0, total - failed - skipped),
        "tests_failed": failed,
        "tests_skipped": skipped,
        "tests_total": total,
        "result_format": "junit_xml",
    }


def _pytest_summary(text: str) -> dict[str, int] | None:
    matches = re.findall(r"(?:(\d+)\s+passed|(?:\s|^)(\d+)\s+failed|(?:\s|^)(\d+)\s+skipped)", text, flags=re.IGNORECASE)
    if not matches:
        return None
    passed = failed = skipped = 0
    for passed_value, failed_value, skipped_value in matches:
        passed += int(passed_value or 0)
        failed += int(failed_value or 0)
        skipped += int(skipped_value or 0)
    return {
        "tests_passed": passed,
        "tests_failed": failed,
        "tests_skipped": skipped,
        "tests_total": passed + failed + skipped,
    }


def _maven_summary(text: str) -> dict[str, int] | None:
    matches = re.findall(
        r"Tests\s+run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)(?:,\s*Skipped:\s*(\d+))?",
        text,
        flags=re.IGNORECASE,
    )
    if not matches:
        return None
    total = failed = skipped = 0
    for all_tests, failures, errors, skipped_value in matches:
        total += int(all_tests)
        failed += int(failures) + int(errors)
        skipped += int(skipped_value or 0)
    return {
        "tests_passed": max(0, total - failed - skipped),
        "tests_failed": failed,
        "tests_skipped": skipped,
        "tests_total": total,
    }


def _jest_summary(text: str) -> dict[str, int] | None:
    match = re.search(
        r"Tests:\s*(?:(\d+)\s+failed,\s*)?(?:(\d+)\s+skipped,\s*)?(\d+)\s+passed,\s*(\d+)\s+total",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    failed, skipped, passed, total = (int(value or 0) for value in match.groups())
    return {
        "tests_passed": passed,
        "tests_failed": failed,
        "tests_skipped": skipped,
        "tests_total": total,
    }


def _integer_attribute(value: str | None) -> int:
    try:
        return max(0, int(value or 0))
    except ValueError:
        return 0


def _tap_summary(text: str) -> dict[str, int] | None:
    values = {}
    for name, count in re.findall(r"^#\s+(tests|pass|fail|skipped|cancelled|todo)\s+(\d+)\s*$", text, re.MULTILINE):
        values[name] = int(count)
    if not {"tests", "pass", "fail"}.issubset(values):
        return None
    skipped = values.get("skipped", 0) + values.get("todo", 0)
    failed = values["fail"] + values.get("cancelled", 0)
    if values["pass"] + failed + skipped != values["tests"]:
        return None
    return {"tests_passed": values["pass"], "tests_failed": failed, "tests_skipped": skipped, "tests_total": values["tests"]}
