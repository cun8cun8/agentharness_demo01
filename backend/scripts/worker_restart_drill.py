"""Exercise a real queued repository repair across one graceful restart or SIGKILL of the Worker.

Submits repairs through the normal API. Never manually claims or acknowledges jobs.
The optional duplicate fault injects one unchanged stream payload after takeover.
Credentials are loaded from a local key file without logging their contents.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:18001')
    parser.add_argument('--key-file', default='.run/local-full/api-key')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--request-file', required=True, help='Repair request JSON, with publish/push/PR disabled')
    parser.add_argument('--worker', default='researchforge-local-worker-1')
    parser.add_argument('--output', required=True)
    parser.add_argument('--timeout', type=int, default=2100)
    parser.add_argument('--failure-mode', choices=('restart', 'kill'), default='restart')
    parser.add_argument('--takeover-worker', help='Start a separately created standby instead of restarting the killed worker')
    parser.add_argument('--inject-duplicate', action='store_true', help='Append one unchanged queue payload after takeover')
    parser.add_argument('--visibility-seconds', type=int, default=120)
    parser.add_argument('--redis-container', help='Read-only Redis delivery observations for SIGKILL')
    args = parser.parse_args()
    if args.failure_mode == 'kill' and not args.redis_container:
        parser.error('SIGKILL drill requires --redis-container for automatic redelivery evidence')
    key = Path(args.key_file).read_text(encoding='utf-8-sig').strip()
    payload = json.loads(Path(args.request_file).read_text(encoding='utf-8-sig'))
    payload['title'] = str(payload.get('title') or 'Recovery drill') + '-' + uuid4().hex[:8]
    if any(payload.get(x) for x in ('publish', 'push', 'create_pull_request')):
        parser.error('Recovery drill requires publishing disabled')
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'failure_mode': args.failure_mode, 'restarted': False, 'observations': []}
    failure_started = None
    pending_before = None
    original_worker_restored = False
    duplicate_payload = None

    def pending():
        result = subprocess.run(['docker', 'exec', args.redis_container, 'redis-cli', '--json',
            'XPENDING', 'researchforge:jobs:stream', 'researchforge-workers', '-', '+', '10'],
            check=True, capture_output=True, text=True, timeout=20)
        return json.loads(result.stdout)

    def api(path, body=None):
        request = urllib.request.Request(args.api.rstrip('/') + '/api/v1' + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'X-API-Key': key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    try:
        submission = api('/integrations/repositories/' + args.repository + '/repair', payload)
        report['job_id'] = job_id = submission['job']['id']
        deadline = time.monotonic() + args.timeout
        original_run = None
        while time.monotonic() < deadline:
            job = api('/jobs/' + job_id)
            run_id = job.get('metadata', {}).get('agent_run_id')
            report['observations'].append({'time': time.time(), 'status': job['status'], 'run_id': run_id})
            if original_run and run_id != original_run:
                raise RuntimeError('RUN_ID_CHANGED_AFTER_RESTART')
            if run_id and job['status'] == 'running' and not report['restarted']:
                steps = api('/runs/' + run_id + '/steps')
                if steps.get('total', 0) >= 1:
                    original_run = run_id
                    report['run_id'] = run_id
                    report['before_restart'] = job
                    if args.failure_mode == 'kill':
                        pending_before = pending()
                        report['pending_before'] = pending_before
                        if args.inject_duplicate:
                            raw = subprocess.run(['docker','exec',args.redis_container,'redis-cli','--json',
                                'XRANGE','researchforge:jobs:stream',pending_before[0][0],pending_before[0][0]],
                                check=True,capture_output=True,timeout=20)
                            fields = json.loads(raw.stdout)[0][1]
                            duplicate_payload = dict(zip(fields[::2],fields[1::2]))['payload']
                        failure_started = time.monotonic()
                        report['failure_at'] = time.time()
                        subprocess.run(['docker', 'kill', '--signal', 'KILL', args.worker],
                                       check=True, timeout=30, capture_output=True)
                        subprocess.run(['docker', 'start', args.takeover_worker or args.worker],
                                       check=True, timeout=60, capture_output=True)
                    else:
                        subprocess.run(['docker', 'restart', '--timeout', '30', args.worker],
                                       check=True, timeout=120, capture_output=True)
                    report['restarted'] = True
            if report['restarted'] and args.failure_mode == 'kill' and not report.get('automatic_redelivery'):
                current_pending = pending()
                report['pending_observations'] = current_pending
                owners = {x[0]: x[1] for x in pending_before}
                if any(x[0] in owners and x[1] != owners[x[0]] and x[3] >= 2 for x in current_pending):
                    report['automatic_redelivery'] = True
                    report['recovery_seconds'] = round(time.monotonic() - failure_started, 3)
                    if args.takeover_worker:
                        subprocess.run(['docker','start',args.worker],check=True,capture_output=True,timeout=60)
                        original_worker_restored = True
                        report['takeover_worker'] = args.takeover_worker
                        report['two_workers_running'] = True
                    if args.inject_duplicate:
                        duplicate = subprocess.run(['docker','exec',args.redis_container,'redis-cli','XADD',
                            'researchforge:jobs:stream','*','payload',duplicate_payload],
                            check=True,capture_output=True,timeout=20)
                        report['duplicate_message_id'] = duplicate.stdout.decode().strip()
            if job['status'] in ('completed', 'failed', 'cancelled', 'paused'):
                report['job'] = job
                report['run'] = api('/runs/' + run_id) if run_id else None
                if job['status'] != 'completed' or not report['restarted'] or report['run']['status'] != 'completed':
                    raise RuntimeError('RESTART_DRILL_NOT_COMPLETED')
                if args.failure_mode == 'kill' and not report.get('automatic_redelivery'):
                    raise RuntimeError('AUTOMATIC_REDELIVERY_NOT_OBSERVED')
                if args.redis_container:
                    ack_deadline = time.monotonic() + max(60, args.visibility_seconds + 30)
                    while pending():
                        if time.monotonic() > ack_deadline:
                            raise TimeoutError('COMPLETED_MESSAGE_NOT_ACKNOWLEDGED')
                        time.sleep(1)
                    report['final_pending'] = []
                report['status'] = 'passed'
                break
            time.sleep(5)
        else:
            raise TimeoutError('RESTART_DRILL_TIMED_OUT')
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        try:
            if args.takeover_worker and report['restarted'] and not original_worker_restored:
                subprocess.run(['docker','start',args.worker],check=True,capture_output=True,timeout=60)
        finally:
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'job_id': report['job_id'], 'run_id': report['run_id']}))


if __name__ == '__main__':
    main()
