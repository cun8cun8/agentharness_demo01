import asyncio
from types import SimpleNamespace
import pytest
from app.api import integrations

@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled', 'paused'])
def test_terminal_repair_delivery_does_not_start_another_run(monkeypatch, status):
    job = SimpleNamespace(status=status, result_json={'agent_run_id': 'existing'}, cancel_requested=False)
    monkeypatch.setattr(integrations.store, 'read_job', lambda _: job)
    def unexpected(*args):
        raise AssertionError('terminal delivery must not access repository or create run')
    monkeypatch.setattr(integrations.store, 'get_repository_connection', unexpected)
    result = asyncio.run(integrations._execute_repository_repair_job('repo', 'job', {}))
    assert result['status'] == status
    assert result['agent_run_id'] == 'existing'

def test_cancelled_queued_repair_never_creates_run(monkeypatch):
    job = SimpleNamespace(status='queued', result_json={}, cancel_requested=True)
    monkeypatch.setattr(integrations.store, 'read_job', lambda _: job)
    updates = []
    monkeypatch.setattr(integrations.store, 'update_job', lambda *args: updates.append(args))
    result = asyncio.run(integrations._execute_repository_repair_job('repo', 'job', {}))
    assert result['status'] == 'cancelled'
    assert updates == [('job', 'cancelled', 'CANCELLED_BY_OPERATOR')]

def test_repair_recovery_reuses_completed_run(monkeypatch):
    job = SimpleNamespace(status='running', result_json={}, cancel_requested=False, metadata={'agent_run_id':'same-run'})
    run = SimpleNamespace(id='same-run', task_id='same-task', status=SimpleNamespace(value='completed'), metrics={}, total_tokens=1, total_cost=0, duration_ms=1, tool_call_count=1)
    repo = SimpleNamespace(id='repo',provider='local',local_path='repo-path')
    monkeypatch.setattr(integrations.store,'read_job',lambda _:job)
    monkeypatch.setattr(integrations.store,'read_run',lambda _:run)
    monkeypatch.setattr(integrations.store,'get_repository_connection',lambda _:repo)
    monkeypatch.setattr(integrations,'_repository_health',lambda _:SimpleNamespace(cache_path='repo-path',path_exists=True))
    updates=[]
    monkeypatch.setattr(integrations.store,'update_job',lambda *a,**kw:updates.append((a,kw)))
    monkeypatch.setattr(integrations.store,'add_audit_log',lambda **kw:None)
    def unexpected(*a,**kw):
        raise AssertionError('recovery must reuse recorded run')
    monkeypatch.setattr(integrations.store,'create_task',unexpected)
    monkeypatch.setattr(integrations.store,'create_run',unexpected)
    result=asyncio.run(integrations._execute_repository_repair_job('repo','job',{}))
    assert result['status']=='completed'
    assert any(kw.get('result_json',{}).get('agent_run_id')=='same-run' for _,kw in updates)


def test_repair_with_no_changes_skips_publish(monkeypatch):
    job = SimpleNamespace(status='running', result_json={}, cancel_requested=False, metadata={'agent_run_id': 'same-run'})
    run = SimpleNamespace(
        id='same-run',
        task_id='same-task',
        status=SimpleNamespace(value='completed'),
        metrics={'changed_files': 0},
        total_tokens=2,
        total_cost=0.01,
        duration_ms=5,
        tool_call_count=2,
        policy_version_id='policy_default_v1',
    )
    repo = SimpleNamespace(id='repo', provider='local', local_path='repo-path')
    monkeypatch.setattr(integrations.store, 'read_job', lambda _: job)
    monkeypatch.setattr(integrations.store, 'read_run', lambda _: run)
    monkeypatch.setattr(integrations.store, 'get_repository_connection', lambda _: repo)
    monkeypatch.setattr(integrations, '_repository_health', lambda _: SimpleNamespace(cache_path='repo-path', path_exists=True, is_git_repo=True))
    monkeypatch.setattr(integrations.store, 'list_artifacts', lambda _: [])
    updates = []
    monkeypatch.setattr(integrations.store, 'update_job', lambda *args, **kwargs: updates.append((args, kwargs)) or job)
    monkeypatch.setattr(integrations.store, 'add_audit_log', lambda **kwargs: None)

    result = asyncio.run(integrations._execute_repository_repair_job('repo', 'job', {'publish': True}))

    assert result['status'] == 'completed', result
    assert result['publish']['status'] == 'skipped'
    assert result['publish']['reason'] == 'NO_CHANGES'


def test_repair_jobs_are_retryable():
    from app.api.jobs import RETRYABLE_JOB_KINDS

    assert "repository_repair" in RETRYABLE_JOB_KINDS


def test_repair_can_explicitly_use_cached_repository_after_sync_failure(monkeypatch):
    job = SimpleNamespace(status='running', result_json={}, cancel_requested=False, metadata={'agent_run_id': 'same-run'})
    run = SimpleNamespace(
        id='same-run',
        task_id='same-task',
        status=SimpleNamespace(value='completed'),
        metrics={'changed_files': 0},
        total_tokens=2,
        total_cost=0.01,
        duration_ms=5,
        tool_call_count=2,
        policy_version_id='policy_default_v1',
    )
    repo = SimpleNamespace(id='repo', provider='github', local_path='repo-path', workspace_id='workspace_default')
    monkeypatch.setattr(integrations.store, 'read_job', lambda _: job)
    monkeypatch.setattr(integrations.store, 'read_run', lambda _: run)
    monkeypatch.setattr(integrations.store, 'get_repository_connection', lambda _: repo)
    monkeypatch.setattr(integrations.store, 'create_job', lambda **kwargs: SimpleNamespace(id='sync-job'))
    async def failed_sync(*_):
        return {'status': 'failed', 'error': 'timeout'}
    monkeypatch.setattr(integrations, '_execute_repository_sync_job', failed_sync)
    monkeypatch.setattr(integrations, '_repository_health', lambda _: SimpleNamespace(cache_path='repo-path', path_exists=True, is_git_repo=True))
    monkeypatch.setattr(integrations.store, 'list_artifacts', lambda _: [])
    updates = []
    monkeypatch.setattr(integrations.store, 'update_job', lambda *args, **kwargs: updates.append((args, kwargs)) or job)
    monkeypatch.setattr(integrations.store, 'add_audit_log', lambda **kwargs: None)

    result = asyncio.run(integrations._execute_repository_repair_job('repo', 'job', {'allow_cached_on_sync_failure': True}))

    assert result['status'] == 'completed', result
    assert result['sync_fallback']['status'] == 'cached'
    assert result['sync_fallback']['reason'] == 'REMOTE_SYNC_FAILED'


def test_remote_sync_retries_and_opens_circuit(monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setenv('RESEARCHFORGE_GITHUB_SYNC_MAX_RETRIES', '1')
    monkeypatch.setenv('RESEARCHFORGE_GITHUB_SYNC_RETRY_BACKOFF_SECONDS', '0')
    monkeypatch.setenv('RESEARCHFORGE_GITHUB_SYNC_CIRCUIT_BREAKER_SECONDS', '60')
    get_settings.cache_clear()
    integrations._sync_circuit_open_until.clear()
    repository = SimpleNamespace(id='repo-sync-test')
    calls = 0

    def eventually_succeeds(_repository):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('temporary network failure')
        return tmp_path

    monkeypatch.setattr(integrations, '_sync_remote_repository_once', eventually_succeeds)
    assert integrations._sync_remote_repository(repository) == tmp_path
    assert calls == 2

    def always_fails(_repository):
        raise RuntimeError('remote unavailable')

    monkeypatch.setattr(integrations, '_sync_remote_repository_once', always_fails)
    failing = SimpleNamespace(id='repo-sync-failing')
    with pytest.raises(RuntimeError, match='remote unavailable'):
        integrations._sync_remote_repository(failing)
    with pytest.raises(RuntimeError, match='REMOTE_SYNC_CIRCUIT_OPEN'):
        integrations._sync_remote_repository(failing)
    get_settings.cache_clear()


@pytest.mark.parametrize("command, reason", [("pytest -q && python verify.py", "COMMAND_BLOCKED"), ("curl https://example.test", "COMMAND_NOT_ALLOWED")])
def test_invalid_test_command_is_rejected_before_creating_a_repair_job(monkeypatch, command, reason):
    from fastapi import HTTPException
    from app.domain.schemas import PolicyVersion, RepositoryRepairRequest
    monkeypatch.setattr(integrations.store, "get_strategy", lambda _: object())
    monkeypatch.setattr(integrations.store, "get_policy", lambda _: PolicyVersion(id="policy_default_v1"))
    def unexpected(*args, **kwargs):
        raise AssertionError("invalid configuration must not create a job or consume model budget")
    monkeypatch.setattr(integrations.store, "create_job_with_idempotency", unexpected)
    repo = SimpleNamespace(id="repo", provider="local", local_path="repo")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(integrations._queue_repository_repair(repo, RepositoryRepairRequest(goal="test", test_command=command), "operator"))
    assert exc.value.status_code == 400
    assert exc.value.detail == reason
