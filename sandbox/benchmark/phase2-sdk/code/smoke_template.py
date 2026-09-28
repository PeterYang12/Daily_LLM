"""One coordinated setup sandbox to validate actual code execution and RAM pages."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from e2b_code_interpreter import Sandbox
from e2b import SandboxQuery, SandboxState

from official_sdk_baseline import check_foreign, load_config


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TOOLS = Path('/home/yuhanya/Daily_LLM/RL/uni-agent-kickoff/10-local-e2b-vm-k8s/code')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/home/yuhanya/.config/e2b-local-20260916/client.json'))
    parser.add_argument('--template', default='benchmark-ci-minimal-20260920')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/template-smoke.json')
    args = parser.parse_args(argv)
    if args.output.exists():
        raise SystemExit('Smoke report already exists; inspect it before allocating another sandbox.')
    config = load_config(args.config)
    check_foreign(config)
    report = {'status': 'running', 'template': args.template,
              'purpose': 'benchmark-phase2-setup', 'run_id': 'session-20260920',
              'started_utc': datetime.now(timezone.utc).isoformat()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    save()
    sbx = None
    began = time.monotonic()
    try:
        sbx = Sandbox.create(args.template, timeout=600,
                             metadata={'purpose': report['purpose'], 'run_id': report['run_id']},
                             request_timeout=90, retries=0, **config)
        report.update(sandbox_id=sbx.sandbox_id, create_seconds=time.monotonic() - began)
        save()
        health = sbx.commands.run('curl -fsS http://127.0.0.1:49999/health', timeout=30)
        if health.exit_code != 0 or health.stdout.strip() != '"OK"':
            raise RuntimeError('Interpreter health check failed')
        executions = []
        for number in range(2):
            started = time.monotonic()
            result = sbx.run_code("print('Hello, world!')", timeout=60, request_timeout=30)
            if result.error or ''.join(result.logs.stdout) != 'Hello, world!\n':
                raise RuntimeError('Interpreter Hello World result invalid: ' + str(result.error))
            executions.append({'number': number + 1, 'seconds': time.monotonic() - started,
                               'execution_count': result.execution_count,
                               'stdout': result.logs.stdout})
        report['executions'] = executions
        report['python_dependencies'] = sbx.files.read('/opt/benchmark-ci/installed-python.txt', user='root')
        report['node_dependencies'] = json.loads(sbx.files.read('/opt/benchmark-ci/installed-node.json', user='root'))
        report['kernels'] = json.loads(sbx.files.read('/opt/benchmark-ci/kernels.json', user='root'))
        versions = sbx.commands.run('/opt/benchmark-ci/bin/python --version', timeout=15)
        report['sandbox_python'] = versions.stdout.strip()
        report['sandbox_node'] = sbx.commands.run('node --version', timeout=15).stdout.strip()
        page_check = subprocess.run(
            [str(LOCAL_TOOLS / 'vm-ssh'), 'sudo', 'python3', '-', '--sandbox-id', sbx.sandbox_id],
            input=(LOCAL_TOOLS / 'verify_guest_4k.py').read_text(), capture_output=True,
            text=True, timeout=30, check=True)
        report['page_verification'] = json.loads(page_check.stdout)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error_type=type(error).__name__,
                      error=str(error).replace(config['api_key'], '<redacted>'))
        raise
    finally:
        report['cleanup_ok'] = False
        try:
            if sbx is not None:
                sbx.kill(request_timeout=30)
            deadline = time.monotonic() + (150 if sbx is None else 10)
            while True:
                query_metadata = {'purpose': report['purpose'], 'run_id': report['run_id']}
                paginator = Sandbox.list(query=SandboxQuery(metadata=query_metadata,
                    state=[SandboxState.RUNNING]), limit=100,
                    request_timeout=20, retries=0, **config)
                owned = []
                while paginator.has_next:
                    owned.extend(info for info in paginator.next_items()
                                 if all((info.metadata or {}).get(k) == v for k, v in query_metadata.items()))
                for info in owned:
                    Sandbox.kill(info.sandbox_id, request_timeout=30, retries=0, **config)
                report['owned_remaining'] = len(owned)
                if not owned and (sbx is not None or time.monotonic() >= deadline):
                    report['cleanup_ok'] = True
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(2)
        except BaseException as cleanup_error:
            report['cleanup_error'] = str(cleanup_error).replace(config['api_key'], '<redacted>')
        report.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                      wall_seconds=time.monotonic() - began)
        save()
        print(json.dumps({k:report.get(k) for k in ['status','sandbox_id','create_seconds','cleanup_ok','error']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
