"""Restore the application into a fresh Compose project and verify a SIGKILL repair.

Only resources created for this UUID project may be removed. The live stack is
read-only. Model credentials are passed in process environment, never reports.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

import boto3
import psycopg
from restore_drill import digest_records, pg_tool


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, timeout=kwargs.pop('timeout', 180), **kwargs)
    if result.returncode:
        # Compose errors may contain interpolated credentials: retain only type.
        raise RuntimeError('COMMAND_FAILED_' + Path(args[0]).name)
    return result.stdout


def wait_api(base, path, headers=None):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers or {}), timeout=10) as response:
                return response.read()
        except Exception:
            time.sleep(2)
    raise TimeoutError('APPLICATION_NOT_READY')


def resume(root):
    import re
    from delivery_acceptance import auth_probe
    report_file = root / 'report.json'
    previous = json.loads(report_file.read_text(encoding='utf-8-sig'))
    project = previous['project']
    if not re.fullmatch(r'rf-restore-[0-9a-f]{12}', project) or root.name != project:
        raise ValueError('RESUME_PROJECT_NOT_OWNED')
    drill = json.loads((root / 'kill-drill.json').read_text(encoding='utf-8-sig'))
    if drill['status'] != 'passed' or not drill.get('automatic_redelivery'):
        raise RuntimeError('RESUME_REQUIRES_COMPLETED_SIGKILL_EVIDENCE')
    for service in ('postgres', 'redis', 'minio', 'api', 'worker', 'frontend'):
        info = json.loads(command(['docker', 'inspect', project + '-' + service + '-1']))[0]
        if info['Config']['Labels'].get('com.docker.compose.project') != project:
            raise ValueError('CONTAINER_PROJECT_MISMATCH')
    if not (root / 'report-initial-failure.json').exists():
        (root / 'report-initial-failure.json').write_bytes(report_file.read_bytes())
    source = json.loads(command(['docker', 'inspect', 'researchforge-local-api-1']))[0]
    env = dict(os.environ)
    source_env = dict(item.split('=', 1) for item in source['Config']['Env'] if '=' in item)
    env.update({key: value for key, value in source_env.items() if key.startswith('RESEARCHFORGE_')
                or key in ('OPENAI_API_KEY', 'DASHSCOPE_API_KEY', 'MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD')})
    cli = ['docker', 'compose', '--project-name', project, '-f', str(root / 'compose.json')]
    document = json.loads((root / 'compose.json').read_text(encoding='utf-8-sig'))
    api_port = document['services']['api']['ports'][0].split(':')[1]
    frontend_port = document['services']['frontend']['ports'][0].split(':')[1]
    api_base = 'http://127.0.0.1:' + api_port
    front_base = 'http://127.0.0.1:' + frontend_port
    report = dict(previous)
    report.update(status='failed', resumed_at=datetime.now(timezone.utc).isoformat(),
                  initial_failure=previous.get('error_type'), cleaned=False)
    report.pop('error_type', None)
    try:
        command(cli + ['up', '-d', '--wait'], env=env)
        key = Path('.run/local-full/api-key').read_text(encoding='utf-8-sig').strip()
        headers = {'X-API-Key': key}
        ready = json.loads(wait_api(api_base, '/health/ready', headers))
        report['checks']['readiness'] = ready['ready']
        wait_api(front_base, '/tasks')
        report['developer_session'] = auth_probe(front_base, Path('.run/local-full/api-key'),
                                                 Path('.run/acceptance/stable-queue-20261004.patch'))
        report['checks']['developer_session'] = all(report['developer_session'].values())
        job = json.loads(wait_api(api_base, '/api/v1/jobs/' + drill['job_id'], headers))
        run = json.loads(wait_api(api_base, '/api/v1/runs/' + drill['run_id'], headers))
        report['checks']['new_repair_and_sigkill'] = (job['status'] == 'completed' and run['status'] == 'completed'
            and job['metadata']['agent_run_id'] == drill['run_id']
            and drill['before_restart']['metadata']['agent_run_id'] == drill['run_id'])
        report['checks']['patch_applied_once'] = run['metrics'].get('autonomous_patch_attempts') == 1
        report['checks']['tests_passed'] = run['metrics'].get('tests_passed') == 2
        pending = json.loads(command(['docker', 'exec', project + '-redis-1', 'redis-cli', '--json',
            'XPENDING', 'researchforge:jobs:stream', 'researchforge-workers']))
        report['checks']['queue_acknowledged'] = pending[0] == 0
        report['queue_pending'] = pending
        report['run_id'] = drill['run_id']
        report['recovery_seconds'] = drill['recovery_seconds']
        if not all(report['checks'].values()):
            raise RuntimeError('RESUMED_APPLICATION_CHECK_FAILED')
        report['status'] = 'passed'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        try:
            if report['status'] == 'passed':
                command(cli + ['down', '--volumes'], env=env)
                report['cleaned'] = True
            else:
                command(cli + ['stop'], env=env)
        except Exception as exc:
            report['status'] = 'failed'
            report['cleanup_error_type'] = type(exc).__name__
            raise
        finally:
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            report_file.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-root', required=True)
    parser.add_argument('--output-dir', default='.run/acceptance/app-restore')
    parser.add_argument('--api-port', type=int, default=18011)
    parser.add_argument('--frontend-port', type=int, default=14010)
    parser.add_argument('--postgres-port', type=int, default=15434)
    parser.add_argument('--s3-port', type=int, default=19030)
    parser.add_argument('--visibility-seconds', type=int, default=60)
    parser.add_argument('--resume-dir', help='Revalidate a retained UUID project after a report-stage failure')
    args = parser.parse_args()
    if args.resume_dir:
        retained = Path(args.resume_dir).resolve()
        prior = json.loads((retained / 'report.json').read_text(encoding='utf-8-sig'))
        if Path(prior['backup_root']).resolve() != Path(args.backup_root).resolve():
            parser.error('Resume backup provenance must match the retained report')
        resume(retained)
        return
    project = 'rf-restore-' + uuid4().hex[:12]
    root = Path(args.output_dir).resolve() / project
    root.mkdir(parents=True, exist_ok=False)
    backup = Path(args.backup_root).resolve()
    workspace = backup / 'workspace'
    db_report_path = next((backup / 'database').rglob('report.json'))
    db_report = json.loads(db_report_path.read_text(encoding='utf-8-sig'))
    db_dump = db_report_path.parent / 'database.dump'
    artifact_root = next((backup / 'artifacts').glob('rf-restore-drill-*'))
    for port in (args.api_port, args.frontend_port, args.postgres_port, args.s3_port):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
    live = json.loads(command(['docker', 'inspect', 'researchforge-local-api-1']))[0]
    inherited = dict(item.split('=', 1) for item in live['Config']['Env'] if '=' in item)
    env = dict(os.environ)
    selected = {key: value for key, value in inherited.items() if key.startswith('RESEARCHFORGE_')
                or key in ('OPENAI_API_KEY', 'DASHSCOPE_API_KEY', 'MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD')}
    env.update(selected)
    settings = {key: '${' + key + '}' for key in selected}
    settings.update({
        'RESEARCHFORGE_ENV': 'production', 'RESEARCHFORGE_AUTH_MODE': 'production',
        'RESEARCHFORGE_API_WORKERS': '1', 'RESEARCHFORGE_JOB_WORKER_ENABLED': '0',
        'RESEARCHFORGE_POSTGRES_DSN': 'postgresql://researchforge:researchforge@postgres:5432/researchforge',
        'RESEARCHFORGE_REDIS_URL': 'redis://redis:6379/0', 'RESEARCHFORGE_EVENT_BUS_URL': 'redis://redis:6379/0',
        'RESEARCHFORGE_EVENT_BUS_BACKEND': 'redis', 'RESEARCHFORGE_OTEL_ENABLED': '0',
        'RESEARCHFORGE_JOB_VISIBILITY_TIMEOUT_SECONDS': str(args.visibility_seconds),
        'RESEARCHFORGE_SANDBOX_WORKSPACE_VOLUME': project + '_workspace-data',
        'RESEARCHFORGE_CORS_ORIGINS': f'http://127.0.0.1:{args.frontend_port}',
        'RESEARCHFORGE_FRONTEND_PUBLIC_URL': f'http://127.0.0.1:{args.frontend_port}/',
        'RESEARCHFORGE_ARTIFACT_STORE_ENDPOINT_URL': 'http://minio:9000',
        'RESEARCHFORGE_KNOWLEDGE_BACKEND': 'local',
    })
    volumes = ['workspace-data:/var/lib/researchforge/workspaces', '/var/run/docker.sock:/var/run/docker.sock']
    api = {'image': 'researchforge-local-api:latest', 'pull_policy': 'never', 'environment': settings,
           'volumes': volumes, 'secrets': ['api_key', 'metrics_token'],
           'ports': [f'127.0.0.1:{args.api_port}:8001']}
    worker = {**api, 'image': 'researchforge-local-worker:latest', 'ports': [],
              'command': ['python', '-m', 'app.worker']}
    compose = {'services': {
        'postgres': {'image': 'postgres:16', 'environment': {'POSTGRES_USER': 'researchforge',
                     'POSTGRES_PASSWORD': 'researchforge', 'POSTGRES_DB': 'researchforge'},
                     'ports': [f'127.0.0.1:{args.postgres_port}:5432'], 'volumes': ['postgres-data:/var/lib/postgresql/data'],
                     'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U researchforge -d researchforge'],
                                     'interval': '2s', 'timeout': '3s', 'retries': 30}},
        'redis': {'image': 'redis:7', 'command': ['redis-server', '--appendonly', 'yes'],
                  'volumes': ['redis-data:/data'], 'healthcheck': {'test': ['CMD', 'redis-cli', 'ping'],
                  'interval': '2s', 'timeout': '3s', 'retries': 30}},
        'minio': {'image': 'minio/minio:RELEASE.2025-04-22T22-12-26Z', 'command': ['server', '/data'],
                  'environment': {'MINIO_ROOT_USER': '${MINIO_ROOT_USER}', 'MINIO_ROOT_PASSWORD': '${MINIO_ROOT_PASSWORD}'},
                  'ports': [f'127.0.0.1:{args.s3_port}:9000'], 'volumes': ['minio-data:/data']},
        'api': api, 'worker': worker,
        'frontend': {'image': 'researchforge-local-frontend:latest', 'pull_policy': 'never',
                     'ports': [f'127.0.0.1:{args.frontend_port}:3010']}},
        'volumes': {name: {} for name in ('postgres-data', 'redis-data', 'minio-data', 'workspace-data')},
        'secrets': {'api_key': {'file': str(Path('.run/local-full/api-key').resolve())},
                    'metrics_token': {'file': str(Path('.run/local-full/metrics-token').resolve())}}}
    compose_file = root / 'compose.json'
    compose_file.write_text(json.dumps(compose, indent=2), encoding='utf-8')
    cli = ['docker', 'compose', '--project-name', project, '-f', str(compose_file)]
    report = {'status': 'failed', 'created_at': datetime.now(timezone.utc).isoformat(), 'project': project, 'backup_root': str(backup),
              'visibility_seconds': args.visibility_seconds, 'checks': {}, 'cleaned': False,
              'images': {name: json.loads(command(['docker', 'image', 'inspect', image]))[0]['Id']
                         for name, image in [('api', 'researchforge-local-api:latest'),
                          ('worker', 'researchforge-local-worker:latest'), ('frontend', 'researchforge-local-frontend:latest')]}}
    created = False
    try:
        created = True
        command(cli + ['up', '-d', '--wait', 'postgres', 'redis', 'minio'], env=env)
        command(cli + ['create', 'api', 'worker', 'frontend'], env=env)
        dsn = f'postgresql://researchforge:researchforge@127.0.0.1:{args.postgres_port}/researchforge'
        with db_dump.open('rb') as stream:
            pg_tool(['pg_restore', '--exit-on-error', '--no-owner', '--no-acl', '--dbname', 'researchforge'],
                    dsn, stdin=stream, stdout=subprocess.DEVNULL, container=project + '-postgres-1')
        with psycopg.connect(dsn) as connection:
            report['checks']['database_restore'] = digest_records(connection) == db_report['expected']
        if not report['checks']['database_restore']:
            raise RuntimeError('RESTORED_DATABASE_MISMATCH')
        client = boto3.client('s3', endpoint_url=f'http://127.0.0.1:{args.s3_port}',
                             aws_access_key_id=env['MINIO_ROOT_USER'], aws_secret_access_key=env['MINIO_ROOT_PASSWORD'])
        deadline = time.monotonic() + 60
        while True:
            try:
                client.create_bucket(Bucket='researchforge')
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise TimeoutError('RESTORED_OBJECT_STORE_NOT_READY')
                time.sleep(2)
        records = json.loads((artifact_root / 'manifest.json').read_text(encoding='utf-8-sig'))
        for item in records:
            content = (artifact_root / item['file']).read_bytes()
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                raise RuntimeError('BACKUP_BLOB_HASH_MISMATCH')
            client.put_object(Bucket='researchforge', Key=item['key'], Body=content,
                              ContentType=item['content_type'], Metadata=item['metadata'])
        report['checks']['artifact_restore_count'] = len(records)
        restore_code = "import tarfile,json;from pathlib import Path;from workspace_drill import manifest;tarfile.open('/backup/workspace.tar.gz').extractall('/restore',filter='data');assert manifest(Path('/restore/workspace'))==json.loads(Path('/backup/manifest.json').read_text(encoding='utf-8-sig'))"
        command(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python',
                 '--mount', f'type=volume,source={project}_workspace-data,target=/restore/workspace',
                 '--mount', f'type=bind,source={workspace},target=/backup,readonly',
                 '--mount', f'type=bind,source={Path("backend/scripts/workspace_restore_drill.py").resolve()},target=/opt/researchforge/backend/workspace_drill.py,readonly',
                 'researchforge-local-api:latest', '-c', restore_code])
        report['checks']['workspace_restore'] = True
        command(cli + ['up', '-d', 'api', 'worker', 'frontend'], env=env)
        api_base = f'http://127.0.0.1:{args.api_port}'
        front_base = f'http://127.0.0.1:{args.frontend_port}'
        key = Path('.run/local-full/api-key').read_text(encoding='utf-8-sig').strip()
        headers = {'X-API-Key': key}
        ready = json.loads(wait_api(api_base, '/health/ready', headers))
        report['checks']['readiness'] = ready['ready']
        wait_api(front_base, '/tasks')
        report['checks']['frontend'] = True
        history = json.loads(wait_api(front_base, '/api/v1/runs/run_b38e3e3573d8438197dd1e262dc462f8', headers))
        report['checks']['historical_run'] = history['status'] == 'completed'
        download = wait_api(front_base, '/api/v1/artifacts/artifact_e2269a8982104744b7f26d7e5d12f24a/download', headers)
        original = Path('.run/acceptance/stable-queue-20261004.patch').read_bytes()
        report['checks']['historical_download'] = download == original
        from delivery_acceptance import auth_probe
        report['developer_session'] = auth_probe(front_base, Path('.run/local-full/api-key'), Path('.run/acceptance/stable-queue-20261004.patch'))
        report['checks']['developer_session'] = all(report['developer_session'].values())
        request = json.loads(Path('.run/acceptance/restart-20261004-request.json').read_text(encoding='utf-8-sig'))
        request['title'] = '隔离恢复后强制终止验收-' + project
        request_file = root / 'repair-request.json'
        request_file.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        command([sys.executable, str(Path('backend/scripts/worker_restart_drill.py').resolve()),
                 '--api', api_base, '--repository', 'repo_conn_a47d200325cb45df98c767dfa9554b1a',
                 '--request-file', str(request_file), '--output', str(root / 'kill-drill.json'),
                 '--failure-mode', 'kill', '--worker', project + '-worker-1', '--redis-container', project + '-redis-1'], timeout=2400)
        drill = json.loads((root / 'kill-drill.json').read_text(encoding='utf-8-sig'))
        report['checks']['new_repair_and_sigkill'] = drill['status'] == 'passed'
        report['run_id'] = drill['run_id']
        report['recovery_seconds'] = drill['recovery_seconds']
        report['checks']['patch_applied_once'] = drill['run']['metrics'].get('autonomous_patch_attempts') == 1
        report['checks']['tests_passed'] = drill['run']['metrics'].get('tests_passed') == 2
        if not all(value for value in report['checks'].values()):
            raise RuntimeError('APPLICATION_RESTORE_CHECK_FAILED')
        report['status'] = 'passed'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        # The UUID project and compose file were generated by this invocation.
        # Failed targets remain stopped for diagnosis; successful targets are removed.
        try:
            if created:
                if report['status'] == 'passed':
                    command(cli + ['down', '--volumes'], env=env)
                    report['cleaned'] = True
                else:
                    command(cli + ['stop'], env=env)
        except Exception as exc:
            report['cleanup_error_type'] = type(exc).__name__
            report['status'] = 'failed'
            raise
        finally:
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
