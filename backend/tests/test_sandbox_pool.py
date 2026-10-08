import pytest

from app.config import get_settings
from app.services.sandbox_pool import SandboxCapacityExceeded, SandboxPool


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_pool_enforces_capacity_and_releases_after_failure(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_MAX_CONCURRENT", "1")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_QUEUE_TIMEOUT_SECONDS", "0")
    pool = SandboxPool()
    with pool.lease("docker"):
        assert pool.status()["active"] == 1
        with pytest.raises(SandboxCapacityExceeded):
            with pool.lease("docker"):
                pass
    assert pool.status()["active"] == 0 and pool.status()["available"] == 1


def test_local_development_commands_do_not_consume_production_pool(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_MAX_CONCURRENT", "1")
    pool = SandboxPool()
    with pool.lease("local"):
        assert pool.status()["active"] == 0


def test_reconcile_only_prunes_service_labelled_ephemeral_containers(monkeypatch):
    from app.services import sandbox_pool as module
    from app.config import get_settings
    commands = []
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "docker")
    get_settings.cache_clear()
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: commands.append(args) or type("Result", (), {"returncode": 0, "stderr": ""})())
    result = SandboxPool().reconcile()
    assert result["warmed"] and result["cleaned"]
    assert ["docker", "container", "prune", "--force", "--filter", "label=researchforge.sandbox.ephemeral=true"] in commands
    get_settings.cache_clear()
