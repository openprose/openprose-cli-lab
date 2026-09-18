#!/usr/bin/env python3
"""Independent Unix process corpus consumer; never imports either bridge.

Example: python3 weave_host_process.py --report /tmp/report.json -- /absolute/prose
For source Bun: ... -- /absolute/bun --no-env-file /absolute/cli.ts
Non-executed assertions are explicit; they are not passes. Network is not used by
fixtures. This harness is not an OS network sandbox for an untrusted candidate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from weave_host_fixture import build_host, construct


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False


def cleanup_fixture(root):
    """Only signal live processes whose command still names this unique fixture."""
    ids = []
    try: ids.append(json.loads((root / 'observation.json').read_text())['pid'])
    except (OSError, ValueError, KeyError): pass
    try: ids.append(int((root / 'descendant.pid').read_text()))
    except (OSError, ValueError): pass
    for pid in ids:
        if not alive(pid): continue
        value = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], env={}, capture_output=True, timeout=2)
        if str(root / 'fake-host').encode() in value.stdout:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass


def execute(command, root, env, control, deadline_ms):
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    held = {}
    if control.get('closeWrapperStdout'): process.stdout.close()
    elif control.get('holdWrapperStdoutUntilExit'): held['stdout'] = process.stdout
    else: selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
    if control.get('holdWrapperStderrUntilExit'): held['stderr'] = process.stderr
    else: selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
    sent = None
    failure = None
    try:
        while selector.get_map() or held or process.poll() is None:
            if process.poll() is not None and held:
                for name, stream in held.items(): selector.register(stream, selectors.EVENT_READ, name)
                held.clear()
            now = time.monotonic()
            if now - started > deadline_ms / 1000:
                failure = 'outer-test-timeout'; break
            if control.get('signalAfter') == 'pending-file' and sent is None and (root / 'pending-file').exists():
                process.send_signal(getattr(signal, control['signal'])); sent = now
            for key, _ in selector.select(0.01):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj); key.fileobj.close(); continue
                if sum(map(len, streams.values())) + len(data) > 2 * 1024 * 1024:
                    failure = 'outer-capture-limit'; break
                streams[key.data].extend(data)
            if failure: break
        if failure and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed: stream.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=2)
    ended = time.monotonic()
    return {'exitCode': process.returncode, 'stdoutHex': streams['stdout'].hex(),
            'stderrHex': streams['stderr'].hex(), 'elapsedMs': (ended-started)*1000,
            'elapsedAfterSignalMs': None if sent is None else (ended-sent)*1000,
            'harnessFailure': failure}


def assess(selected, observed, root):
    expected = selected['expected']
    errors, limitations = [], []
    try: child = json.loads((root / 'observation.json').read_text())
    except FileNotFoundError: child = None
    observed['child'] = child
    stderr = bytes.fromhex(observed['stderrHex'])
    stdout = bytes.fromhex(observed['stdoutHex'])
    if observed['harnessFailure']: errors.append(observed['harnessFailure'])
    for key, value in expected.items():
        actual = None
        if key == 'outcomeOneOf':
            blocked_diagnostic = expected.get('diagnosticUnobservableIfBlocked') is True and selected.get('control', {}).get('holdWrapperStderrUntilExit') is True
            allowed = ({'exitCode'}, {'exitCode', 'stderrSuffixAscii'}) if blocked_diagnostic else ({'exitCode', 'stderrSuffixAscii'},)
            if not isinstance(value, list) or not value or any(set(item) not in allowed for item in value):
                errors.append('unsupported outcomeOneOf shape'); continue
            actual = any(observed['exitCode'] == item['exitCode'] and ('stderrSuffixAscii' not in item or stderr.endswith(item['stderrSuffixAscii'].encode('ascii'))) for item in value)
            value = True
        elif key == 'diagnosticUnobservableIfBlocked':
            if value is not True or selected.get('control', {}).get('holdWrapperStderrUntilExit') is not True:
                errors.append('diagnostic limitation requires held stderr control')
            limitations.append('wrapper diagnostic not asserted: stderr intentionally held unread through process exit; retained bytes include any best-effort diagnostic')
            continue
        elif key in ('exitCode', 'stdoutHex', 'stderrHex'): actual = observed[key]
        elif key in ('spawned', 'weaveHostSpawned'): actual = child is not None
        elif key == 'childArgv': actual = child and child['argv']
        elif key == 'childCwd': actual = child and child['cwd']
        elif key == 'stdinEOF': actual = child and child['stdinEOF']
        elif key == 'stderrAscii': actual = stderr; value = value.encode('ascii')
        elif key == 'stderrSuffixAscii': actual = stderr.endswith(value.encode('ascii')); value = True
        elif key == 'stdoutContains': actual = value.encode() in stdout; value = True
        elif key == 'maxElapsedMs': actual = observed['elapsedMs'] <= value; value = True
        elif key == 'maxElapsedAfterSignalMs': actual = observed['elapsedAfterSignalMs'] is not None and observed['elapsedAfterSignalMs'] <= value; value = True
        elif key == 'childOutputBytesAtMost':
            suffix = expected.get('stderrSuffixAscii', '').encode()
            actual = len(stdout) + len(stderr) - (len(suffix) if stderr.endswith(suffix) else 0) <= value; value = True
        elif key == 'pendingPreserved': actual = (root / 'pending-file').read_bytes() == b'pending-effect\n' if (root / 'pending-file').exists() else False
        elif key == 'directChildReaped':
            if child is None:
                limitations.append('directChildReaped not observed: deadline preceded fixture startup marker')
                continue
            actual = not alive(child['pid'])
        elif key == 'signalAfterDirectChildExit':
            try: witness = int((root / 'descendant.pid').read_text())
            except (OSError, ValueError):
                errors.append('inherited-pipe witness did not initialize')
                witness = None
            actual = (root / 'descendant-signalled').exists() or witness is None or not alive(witness)
            limitations.append('signalAfterDirectChildExit checks same-group witness signal marker; not a syscall audit')
        elif key == 'bindingOpened':
            limitations.append('bindingOpened=false is not observable as a syscall claim; grammar diagnostic precedes binding failure and host marker is absent')
            continue
        elif key in ('startedHarness', 'resolvedImage'):
            limitations.append(key + '=false has no process observation seam; host success under empty environment is observed')
            continue
        else:
            errors.append('unsupported expected field: ' + key); continue
        if actual != value: errors.append(key + ' mismatch')
    if selected.get('host', {}).get('behavior') == 'assert-environment':
        if child is None or child['environment'] != selected['host']['expectedEnvironment']:
            errors.append('exact child environment mismatch')
    observed['errors'] = errors
    observed['unprovenAssertions'] = limitations
    observed['status'] = 'failed' if errors else ('passed-with-limitations' if limitations else 'passed')
    return observed


def run_case(candidate, native, document, case, parent):
    unknown = set(case) - {'id', 'argv', 'ambient', 'bindingFile', 'bindingPatch', 'bindingRawUtf8', 'control', 'expected', 'host', 'hostFile', 'image', 'platform', 'runnerConfig'}
    if unknown: raise ValueError('unsupported case fields: ' + repr(sorted(unknown)))
    if set(case.get('control', {})) - {'closeWrapperStdout', 'holdWrapperStdoutUntilExit', 'holdWrapperStderrUntilExit', 'signalAfter', 'signal'}: raise ValueError('unsupported control')
    if case.get('platform') == 'windows':
        return {'id': case['id'], 'status': 'not-executed', 'reason': 'requires actual Windows target; Unix unsupported branch is not injectable'}
    if 'languageArgv' in case['expected']:
        return {'id': case['id'], 'status': 'not-executed', 'reason': 'opaque language routing delegated to port parser tests; no silent argv or runner configuration changes'}
    with tempfile.TemporaryDirectory(prefix=case['id']+'-', dir=parent) as temporary:
        root = Path(temporary).resolve()
        selected = construct(root, native, document, case)
        try:
            observed = execute(candidate + selected['argv'], root, selected.get('ambient', {}), selected.get('control', {}), document['harness']['outerTestTimeoutMs'])
            result = assess(selected, observed, root)
            if 'runnerConfig' in selected or 'image' in selected:
                result['unprovenAssertions'].append('runnerConfig/image unavailable require build-specific selection seams; default empty environment only')
                if result['status'] == 'passed': result['status'] = 'passed-with-limitations'
            result['id'] = case['id']
            return result
        finally: cleanup_fixture(root)


def self_check(native, document, parent):
    """Test the observer against the native fixture and intentional wrong output."""
    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        root = Path(temporary).resolve()
        case = {'id': 'self-check', 'argv': [], 'ambient': {'PRESENT': 'chosen', '__proto__': 'literal'},
                'host': {'stdoutHex': 'ff000a', 'stderrHex': 'fe', 'exitCode': 42},
                'expected': {'spawned': True, 'exitCode': 42, 'stdoutHex': 'ff000a', 'stderrHex': 'fe', 'stdinEOF': True}}
        selected = construct(root, native, document, case)
        observation = execute([str(root/'fake-host'), 'status', str(root/'weave.json')], root, case['ambient'], {}, 2000)
        result = assess(selected, observation, root)
        assert result['status'] == 'passed', result
        assert result['child']['environment'] == case['ambient'], result
        selected['expected']['stdoutHex'] = '00'
        assert 'stdoutHex mismatch' in assess(selected, observation, root)['errors']
        for stream in ('stdout', 'stderr'):
            with tempfile.TemporaryDirectory(dir=parent) as blocked_temporary:
                blocked_root = Path(blocked_temporary).resolve()
                case['host'] = {'behavior': 'saturate-' + stream + '-then-sleep'}
                construct(blocked_root, native, document, case)
                control = {'holdWrapper' + stream.capitalize() + 'UntilExit': True}
                blocked = execute([str(blocked_root/'fake-host')], blocked_root, {}, control, 250)
                assert blocked['harnessFailure'] == 'outer-test-timeout', blocked
                assert blocked['elapsedMs'] < 2000, blocked
        return {'status': 'passed', 'checks': ['raw invalid UTF-8 and NUL', 'exit 42', 'stdin EOF', 'exact empty-based environment including __proto__', 'deliberate wrong expectation rejected', 'both open-unread controls trigger bounded outer timeout against unsupervised fixture']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).resolve().parents[1]/'fixtures/weave-host-v1.json')
    parser.add_argument('--cc', type=Path, default=Path('/usr/bin/cc'))
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--self-check-only', action='store_true')
    parser.add_argument('candidate', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    candidate = args.candidate[1:] if args.candidate[:1] == ['--'] else args.candidate
    if not args.self_check_only and (not candidate or not Path(candidate[0]).is_absolute()): parser.error('explicit absolute candidate executable required after --')
    document = json.loads(args.fixture.read_bytes())
    if document['schema'] != 'openprose.weave-host-fixture/1': parser.error('unsupported fixture schema')
    if len({case['id'] for case in document['cases']}) != len(document['cases']): parser.error('duplicate case IDs')
    with tempfile.TemporaryDirectory(prefix='weave-host-corpus-') as temporary:
        root = Path(temporary).resolve()
        native, compile_command = build_host(root, args.cc.resolve())
        check = self_check(native, document, root)
        results = []
        if not args.self_check_only:
            for case in document['cases']:
                try: result = run_case(candidate, native, document, case, root)
                except Exception as error: result = {'id': case['id'], 'status': 'harness-error', 'error': type(error).__name__, 'detail': str(error)[:1000]}
                results.append(result)
                print(case['id'] + ': ' + result['status'], file=sys.stderr, flush=True)
        report = {'schema': 'openprose.weave-host-process-report/1', 'fixtureSha256': sha(args.fixture),
                  'fixtureCases': len(document['cases']), 'candidateArgv': candidate, 'candidateFileSha256': {item: sha(Path(item)) for item in candidate if Path(item).is_absolute() and Path(item).is_file()}, 'platform': sys.platform,
                  'fixtureCompilerArgv': compile_command, 'fixtureExecutableSha256': sha(native),
                  'harnessSha256': sha(Path(__file__)), 'fixtureHelperSha256': sha(Path(__file__).with_name('weave_host_fixture.py')),
                  'selfCheck': check, 'providerCalls': 0, 'networkCallsByHarness': 0,
                  'scope': 'Trusted candidate; no OS network sandbox. No model or fulfillment qualification.',
                  'results': results}
        report['counts'] = {status: sum(r['status'] == status for r in results) for status in ('passed', 'passed-with-limitations', 'failed', 'harness-error', 'not-executed')}
        args.report.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report['counts']))
        return 1 if any(r['status'] in ('failed', 'harness-error') for r in results) else 0

if __name__ == '__main__': sys.exit(main())
