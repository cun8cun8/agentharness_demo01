from backend.scripts.validate_harness_configs import main


def test_harness_pipeline_definitions_are_safe_and_structured() -> None:
    assert main() == 0
