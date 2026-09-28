"""Bounded, non-remediating service checks. Never touches other clients' sandboxes."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx

ROOT = Path('/data2/yuhanya/e2b-local-20260916')
STATE = ROOT / 'monitoring'
CONFIG = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')

GUEST_CHECK = '''
import json,os,shutil,subprocess
from pathlib import Path
pods=json.loads(subprocess.check_output(['k3s','kubectl','-n','e2b','get','pods','-o','json'],timeout=10))['items']
items=[]
for p in pods:
 n=p['metadata']['name']
 if n!='e2b-0' and not n.startswith('e2b-gateway-'):continue
 s=p.get('status',{})
 items.append({'name':n,'ready':any(c.get('type')=='Ready' and c.get('status')=='True' for c in s.get('conditions',[])),
 'restarts':sum(c.get('restartCount',0) for c in s.get('containerStatuses',[])+s.get('initContainerStatuses',[]))})
m={k:v.strip() for k,v in (x.split(':',1) for x in Path('/proc/meminfo').read_text().splitlines())}
d=shutil.disk_usage('/')
print(json.dumps({'pods':items,'vcpus':os.cpu_count(),'load_average':os.getloadavg(),
 'hugepages_total':int(m['HugePages_Total']),'hugepages_free':int(m['HugePages_Free']),
 'disk_total':d.total,'disk_used':d.used,'disk_available':d.free}))
'''


def functional_probe():
    from e2b import Sandbox
    config = json.loads(CONFIG.read_text())
    result = {'ok': False, 'cleanup_confirmed': False, 'timings_ms': {}}
    sandbox = None
    def timed(name, fn):
        started = time.perf_counter()
        value = fn()
        result['timings_ms'][name] = round((time.perf_counter() - started) * 1000, 2)
        return value
    try:
        sandbox = timed('create', lambda: Sandbox.create('base', timeout=90, request_timeout=10,
            metadata={'purpose': 'e2b-service-monitor'}, **config))
        response = timed('first_command', lambda: sandbox.commands.run('printf e2b-monitor-ok', timeout=10))
        assert response.exit_code == 0 and response.stdout == 'e2b-monitor-ok'
        timed('file_write', lambda: sandbox.files.write('/tmp/e2b-monitor', 'monitor-ok'))
        assert timed('file_read', lambda: sandbox.files.read('/tmp/e2b-monitor')) == 'monitor-ok'
        result['ok'] = True
    except Exception as error:
        result['error_type'] = type(error).__name__
    finally:
        if sandbox is not None:
            try:
                timed('kill', sandbox.kill)
                result['cleanup_confirmed'] = not sandbox.is_running(request_timeout=5)
            except Exception as error:
                result['cleanup_error_type'] = type(error).__name__
        result['ok'] = result['ok'] and result['cleanup_confirmed']
    print(json.dumps(result), flush=True)


def disk_record(name, total, used, available):
    percent = 100 * used / (used + available)
    return {'filesystem': name, 'used_percent': round(percent, 1),
            'available_gib': round(available / 2**30, 1),
            'severity': 'critical' if percent >= 95 else 'warning' if percent >= 85 else 'ok'}


def run(force_functional=False, passive=False):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Previous monitor invocation remains active')
            return 0
        latest = STATE / 'latest.json'
        try:
            previous = json.loads(latest.read_text())
        except (OSError, ValueError):
            previous = {}
        now = time.time()
        checks = {}
        disks = []
        for mount in ['/', '/data2']:
            u = shutil.disk_usage(mount)
            disks.append(disk_record('host:' + mount, u.total, u.used, u.free))
        config = None
        try:
            config = json.loads(CONFIG.read_text())
            with httpx.Client(timeout=8, trust_env=False) as client:
                for name, path, headers in [
                    ('api_health', '/health', {}),
                    ('authenticated_api', '/sandboxes', {'X-API-KEY': config['api_key']})]:
                    started = time.perf_counter()
                    try:
                        response = client.get(config['api_url'] + path,
                            headers={'User-Agent': 'e2b-service-monitor/1.0', **headers})
                        item = {'ok': response.status_code == 200, 'http_status': response.status_code,
                                'latency_ms': round((time.perf_counter() - started) * 1000, 2)}
                        if name == 'authenticated_api' and item['ok']:
                            data = response.json()
                            if isinstance(data, list):
                                item['visible_running_sandboxes'] = len(data)
                                item['sandboxes'] = [{
                                    'sandbox_id': x.get('sandboxID'), 'template_id': x.get('templateID'),
                                    'cpu_count': x.get('cpuCount'), 'memory_mb': x.get('memoryMB'),
                                    'metadata': {k: str(v)[:128] for k, v in (x.get('metadata') or {}).items()
                                                 if k in ['purpose', 'run_id', 'episode_id', 'task_id', 'instance_id']}}
                                    for x in data]
                        checks[name] = item
                    except Exception as error:
                        checks[name] = {'ok': False, 'error_type': type(error).__name__}
        except Exception as error:
            checks['configuration'] = {'ok': False, 'error_type': type(error).__name__}
        try:
            vm = subprocess.run(['systemctl', 'is-active', 'e2b-sandbox-vm.service'],
                                capture_output=True, text=True, timeout=5)
            checks['vm'] = {'ok': vm.returncode == 0 and vm.stdout.strip() == 'active',
                            'state': vm.stdout.strip()}
        except Exception as error:
            checks['vm'] = {'ok': False, 'error_type': type(error).__name__}
        try:
            guest = subprocess.run([str(ROOT / 'code/vm-ssh'), 'sudo python3 -'], input=GUEST_CHECK,
                                   capture_output=True, text=True, timeout=18, check=True)
            data = json.loads(guest.stdout)
            present = any(p['name'] == 'e2b-0' for p in data['pods']) and any(
                p['name'].startswith('e2b-gateway-') for p in data['pods'])
            checks['guest_kubernetes'] = {'ok': present and all(p['ready'] for p in data['pods']), **data}
            disks.append(disk_record('vm:/', data['disk_total'], data['disk_used'], data['disk_available']))
        except Exception as error:
            checks['guest_kubernetes'] = {'ok': False, 'error_type': type(error).__name__}
        if passive:
            for name, path, maximum_age in [
                ('performance_recording', STATE / 'performance/latest.json', 35),
                ('event_recording', STATE / 'events/latest-collection.json', 240)]:
                try:
                    age = max(0, time.time() - path.stat().st_mtime)
                    checks[name] = {'ok': age <= maximum_age, 'record_age_seconds': round(age, 1)}
                    if name == 'event_recording':
                        errors = json.loads(path.read_text()).get('errors', [])
                        checks[name]['collection_errors'] = errors
                        checks[name]['ok'] = checks[name]['ok'] and not errors
                except Exception as error:
                    checks[name] = {'ok': False, 'error_type': type(error).__name__}
        last_probe = previous.get('last_functional_at', 0)
        if not passive and (force_functional or now - last_probe >= 300) and checks.get('authenticated_api', {}).get('ok'):
            try:
                probe = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--probe-child'],
                                       capture_output=True, text=True, timeout=60, check=True)
                checks['sandbox_functional'] = json.loads(probe.stdout)
            except Exception as error:
                checks['sandbox_functional'] = {'ok': False, 'error_type': type(error).__name__,
                    'note': 'Probe sandbox TTL is 90 seconds if cleanup was interrupted'}
            last_probe = now
        streaks = previous.get('failure_streaks', {}).copy()
        if passive:streaks.pop('sandbox_functional', None)
        for name, item in checks.items():
            streaks[name] = 0 if item['ok'] else streaks.get(name, 0) + 1
        # Configuration is healthy once authenticated requests succeed.
        if checks.get('authenticated_api', {}).get('ok'):streaks['configuration'] = 0
        last_result = checks.get('sandbox_functional', previous.get('last_functional_result'))
        failures = [name for name, count in streaks.items() if count > 0]
        alert = any(n >= 3 for n in streaks.values()) or any(d['severity'] == 'critical' for d in disks)
        record = {'utc': datetime.now(timezone.utc).isoformat(), 'endpoint': config['api_url'] if config else None,
            'mode': 'passive' if passive else 'functional_probes_enabled',
            'checks': checks, 'disks': disks, 'failure_streaks': streaks, 'failed_checks': failures,
            'last_functional_at': last_probe, 'last_functional_result': last_result,
            'alert': alert, 'status': 'alert' if alert else 'warning' if failures or any(d['severity'] == 'warning' for d in disks) else 'ok'}
        temporary = latest.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, indent=2))
        temporary.replace(latest)
        logger = logging.getLogger('e2b-monitor')
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(STATE / 'history.jsonl', maxBytes=5*2**20, backupCount=3)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        logger.info(json.dumps(record))
        handler.close()
        logger.removeHandler(handler)
        print(json.dumps(record), flush=True)
        return 2 if alert else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe-child', action='store_true')
    parser.add_argument('--functional-now', action='store_true')
    parser.add_argument('--passive', action='store_true', help='Do not create synthetic sandboxes during formal training')
    args = parser.parse_args()
    if args.probe_child:
        functional_probe()
    else:
        raise SystemExit(run(args.functional_now, args.passive))
