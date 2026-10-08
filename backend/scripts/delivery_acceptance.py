"""Unified local delivery checks with explicit pass/fail/not_verified results.

By default executes an isolated application restore + SIGKILL drill from an
existing consistent backup. --application-report aggregates prior evidence and
labels it as reused; it never silently turns an unexecuted check into a pass.
"""
import argparse
from datetime import datetime, timezone
from http.cookies import SimpleCookie
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]


def auth_probe(base_url, key_file, patch_file):
    key = Path(key_file).read_text(encoding='utf-8-sig').strip()
    result = {}

    def get(path, headers=None, body=None):
        request = urllib.request.Request(base_url.rstrip('/') + path, headers=headers or {}, data=body)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    status, content, _ = get('/api/v1/auth/session', {'X-API-Key': 'invalid-delivery-check-key'})
    result['invalid_key_rejected'] = status == 401
    status, _, _ = get('/api/v1/runs')
    result['anonymous_rejected'] = status == 401
    status, content, headers = get('/api/v1/auth/session', {'X-API-Key': key})
    result['developer_login'] = status == 200 and json.loads(content).get('user', {}).get('id') == 'user_admin'
    cookies = SimpleCookie()
    for cookie in headers.get_all('Set-Cookie', []):
        cookies.load(cookie)
    session = cookies.get('researchforge_user_id')
    csrf = cookies.get('researchforge_csrf')
    result['session_cookie_flags'] = bool(session and session['httponly'] and session['secure'])
    cookie_header = '; '.join(f'{name}={value.value}' for name, value in cookies.items())
    status, content, _ = get('/api/v1/runs/run_b38e3e3573d8438197dd1e262dc462f8', {'Cookie': cookie_header})
    result['cookie_session_access'] = status == 200 and json.loads(content).get('status') == 'completed'
    path = '/api/v1/artifacts/artifact_e2269a8982104744b7f26d7e5d12f24a/download'
    status, content, headers = get(path, {'Cookie': cookie_header})
    result['download_bytes_and_filename'] = (status == 200 and content == Path(patch_file).read_bytes()
                                            and 'filename="fix.patch"' in headers.get('Content-Disposition', ''))
    status, _, _ = get(path)
    result['anonymous_download_rejected'] = status == 401
    status, _, _ = get('/api/v1/auth/logout', {'Cookie': cookie_header,
        'X-CSRF-Token': csrf.value if csrf else '', 'Origin': base_url.rstrip('/'),
        'Content-Type': 'application/json'}, b'{}')
    result['logout'] = status == 200
    status, _, _ = get('/api/v1/runs', {'Cookie': cookie_header})
    result['revoked_session_rejected'] = status == 401
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-root', required=True)
    parser.add_argument('--application-report', help='Explicitly reuse an existing application restore report')
    parser.add_argument('--frontend', default='http://127.0.0.1:13010')
    parser.add_argument('--api', default='http://127.0.0.1:18001')
    parser.add_argument('--output', default='.run/acceptance/delivery-latest.json')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = Path(args.backup_root).resolve()
    report = {'status': 'failed', 'created_at': datetime.now(timezone.utc).isoformat(), 'checks': {},
              'not_verified': ['host_power_loss', 'cross_host_rebuild', 'primary_stack_sigkill', 'kafka', 'enterprise_sso', 'observability_stack', 'browser_visual_checks'],
              'application_execution': 'reused' if args.application_report else 'executed'}
    try:
        with urllib.request.urlopen(args.api.rstrip('/') + '/health', timeout=30) as response:
            report['checks']['live_health'] = {'status': 'passed' if json.load(response)['status'] == 'ok' else 'failed'}
        security = auth_probe(args.frontend, ROOT / '.run/local-full/api-key', ROOT / '.run/acceptance/stable-queue-20261004.patch')
        report['checks']['developer_access'] = {'status': 'passed' if all(security.values()) else 'failed', 'details': security}
        restored = []
        for file in backup.rglob('report.json'):
            data = json.loads(file.read_text(encoding='utf-8-sig'))
            restored.append({'path': str(file), 'status': data['status'], 'target_removed': data.get('target_removed')})
        report['checks']['backup_integrity'] = {'status': 'passed' if len(restored) == 3 and all(
            x['status'] == 'passed' and x['target_removed'] for x in restored) else 'failed', 'details': restored}
        if args.application_report:
            application_report = Path(args.application_report).resolve()
        else:
            destination = output.parent / 'application-drills'
            process = subprocess.run([sys.executable, str(ROOT / 'backend/scripts/application_restore_drill.py'),
                '--backup-root', str(backup), '--output-dir', str(destination)], capture_output=True, timeout=3000)
            candidates = sorted(destination.glob('rf-restore-*/report.json'), key=lambda file: file.stat().st_mtime)
            if not candidates:
                raise RuntimeError('APPLICATION_REPORT_MISSING')
            application_report = candidates[-1]
            if process.returncode:
                report['application_process_failed'] = True
        application = json.loads(application_report.read_text(encoding='utf-8-sig'))
        session_file = application_report.parent / 'developer-session.json'
        session_checks = application.get('developer_session')
        if not session_checks and session_file.exists():
            session_data = json.loads(session_file.read_text(encoding='utf-8-sig'))
            if session_data.get('project') == application['project']:
                session_checks = session_data['checks']
        report['checks']['restored_developer_session'] = {'status': 'passed' if session_checks and all(session_checks.values()) else 'not_verified', 'details': session_checks}
        valid = (Path(application['backup_root']).resolve() == backup and application['status'] == 'passed'
                 and application['cleaned'] and not report.get('application_process_failed'))
        report['checks']['application_restore_and_sigkill'] = {'status': 'passed' if valid else 'failed',
            'evidence': str(application_report), 'details': application}
        live_info = json.loads(subprocess.run(['docker', 'inspect', 'researchforge-local-worker-1'],
            capture_output=True, check=True, timeout=30).stdout)[0]
        live_env = dict(item.split('=', 1) for item in live_info['Config']['Env'] if '=' in item)
        report['live_visibility_seconds'] = int(live_env['RESEARCHFORGE_JOB_VISIBILITY_TIMEOUT_SECONDS'])
        report['live_event_bus_backend'] = live_env['RESEARCHFORGE_EVENT_BUS_BACKEND']
        passed = all(item['status'] == 'passed' for item in report['checks'].values())
        report['status'] = 'passed' if passed else 'failed'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks': {key: value['status'] for key, value in report['checks'].items()},
                      'not_verified': report['not_verified']}, indent=2))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
