"""Controlled incompressible/identity file-read cohort using the frozen runner.

Only the fixture and request header change. Measurement, concurrency, safety
guards and ownership cleanup are inherited unchanged. Requesting identity is
not itself wire-byte proof; pair these results with file_transport_probe.py.
No service calls occur at import or with --dry-run.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import signal


RUNNER_PATH = Path(__file__).with_name('runner.py')
RUNNER_SHA256 = hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location('frozen_file_runner', RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
PAYLOAD_SIZE = 1024 * 1024
PAYLOAD_SEED = 20260920


def payload_bytes():
    return random.Random(PAYLOAD_SEED).randbytes(PAYLOAD_SIZE)


class IdentityFileBench(runner.Bench):
    def __init__(self, args):
        self.identity_payload = payload_bytes()
        self.identity_payload_sha256 = hashlib.sha256(self.identity_payload).hexdigest()
        self.identity_fixture_ready = False
        super().__init__(args)

    def write(self, kind, **data):
        self.base.update(scenario='file_read_1m_identity_random',
                         comparison_key='file-read-1m-identity-random-v1')
        data.update(payload_sha256=self.identity_payload_sha256 if self.identity_fixture_ready else None,
                    target_payload_sha256=self.identity_payload_sha256,
                    payload_generator='random.Random(20260920).randbytes(1048576)',
                    requested_accept_encoding='identity' if self.identity_fixture_ready else None,
                    target_accept_encoding='identity',
                    identity_fixture_ready=self.identity_fixture_ready,
                    frozen_runner_sha256=RUNNER_SHA256)
        super().write(kind, **data)

    async def prepare(self):
        await super().prepare()
        # In non-streaming files.read the public SDK uses this same client.
        # Service mode copies this client's headers into the raw HTTPX request.
        self.payload = self.identity_payload
        path = '/tmp/e2b-bench-source-' + self.run_id
        for sb in self.handles.values():
            sb._envd_api.headers['Accept-Encoding'] = 'identity'
            await self.write_file(sb, path, self.payload)
        for sb in self.handles.values():
            if sb._envd_api.headers.get('Accept-Encoding') != 'identity':
                raise runner.InvalidResult('SDK envd client did not retain the identity request header')
            actual = await self.read_file(sb, path)
            if actual != self.identity_payload:
                raise runner.InvalidResult('Controlled random file fixture read-back mismatch')
        self.identity_fixture_ready = True
        self.write('identity_fixture', window='setup', resident_count=len(self.handles),
                   state_check=True, request_header_scope='HTTPX client/request configuration',
                   wire_encoding_verified=False,
                   encoding_evidence='phase0-environment/file_transport_probe.py results required')
        warmup = await self.measured(-2, 'warmup')
        if warmup['status'] != 'success':
            raise runner.InvalidResult('Identity/random warmup failed')
        await self.guard()


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', choices=['service','sdk'], required=True)
    parser.add_argument('--phase')
    parser.add_argument('--template', default='base-4k-4g')
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--residents', type=int, default=8)
    parser.add_argument('--duration', type=float, default=20)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--run-id')
    parser.add_argument('--output', required=True)
    parser.add_argument('--expected-runner-sha256')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.concurrency <= 64 or not 1 <= args.residents <= 64 or not 1 <= args.duration <= 120:
        parser.error('concurrency/residents must be 1..64; duration 1..120 seconds')
    if args.expected_runner_sha256 and args.expected_runner_sha256 != RUNNER_SHA256:
        parser.error('frozen runner hash does not match')
    args.phase = args.phase or ('phase1-service' if args.adapter=='service' else 'phase2-sdk')
    args.scenario = 'file_read_1m'
    args.bytes = PAYLOAD_SIZE
    args.mode = 'closed_loop'
    args.count = 1
    args.rate = 1
    return args


async def main(argv=None):
    args = arguments(argv)
    if args.dry_run:
        print(json.dumps({'scenario':'file_read_1m_identity_random','adapter':args.adapter,
            'concurrency':args.concurrency,'residents':args.residents,'duration':args.duration,
            'payload_bytes':PAYLOAD_SIZE,'payload_sha256':hashlib.sha256(payload_bytes()).hexdigest(),
            'requested_accept_encoding':'identity','wire_encoding_verified':False,
            'frozen_runner_sha256':RUNNER_SHA256,'service_requests':0},indent=2))
        return 0
    benchmark = IdentityFileBench(args)
    for sig in (signal.SIGINT,signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig,benchmark.stop.set)
    return await benchmark.run()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
