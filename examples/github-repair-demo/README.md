# GitHub Repair Demo

This deliberately contains one failing test. The application accepts tax rates
as whole percentages, so a rate of `8` means eight percent. Run `pytest -q` to
observe the failure before asking ResearchForge to repair the implementation.

The test suite is intentionally part of the contract. A repair must change the
implementation without modifying files under `tests/`.
