"""Archive a read-only workspace and verify extraction in a temporary directory."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import time


def manifest(root):
    result = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result.append({'path': relative, 'type': 'symlink', 'target': str(path.readlink())})
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            result.append({'path': relative, 'type': 'file', 'size': path.stat().st_size,
                           'sha256': digest.hexdigest()})
        elif path.is_dir():
            result.append({'path': relative, 'type': 'directory'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output_dir).resolve()
    if output == source or source in output.parents:
        parser.error('Backup output must be outside the source')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'workspace.tar.gz'
    started = time.monotonic()
    report = {'status': 'failed'}
    try:
        expected = manifest(source)
        with tarfile.open(archive, 'x:gz') as tar:
            tar.add(source, arcname='workspace')
        if manifest(source) != expected:
            raise RuntimeError('SOURCE_CHANGED_DURING_BACKUP')
        with TemporaryDirectory(prefix='rf-workspace-restore-') as target:
            with tarfile.open(archive, 'r:gz') as tar:
                tar.extractall(target, filter='data')
            actual = manifest(Path(target) / 'workspace')
            if expected != actual:
                raise RuntimeError('RESTORED_MANIFEST_MISMATCH')
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
        (output / 'manifest.json').write_text(json.dumps(expected, indent=2), encoding='utf-8')
        report.update(status='passed', entries=len(expected), manifest_sha256=digest,
                      backup_bytes=archive.stat().st_size, target_removed=True,
                      elapsed_seconds=round(time.monotonic() - started, 3))
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
