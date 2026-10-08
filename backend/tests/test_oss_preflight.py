from backend.scripts.oss_preflight import collect_findings


def test_oss_preflight_passes_for_repository():
    result = collect_findings()
    assert result["ok"], result
