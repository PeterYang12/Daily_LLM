"""Diagnose file compression semantics; this is not a throughput benchmark.

For 1 MiB repeated-x and reproducible SHA-256-counter bytes, compare native
HTTPX raw response bodies with the SDK's own envd HTTP client and public
files.read. Only safe encoding/length headers, byte counts and hashes are saved.
The SDK's pyqwest transport may decode before HTTPX sees the response, so its
num_bytes_downloaded is deliberately not labeled as wire bytes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import time
import uuid
import zlib

import httpx
from e2b import Sandbox, SandboxQuery, SandboxState


CONFIG = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')
PURPOSE = 'e2b-benchmark-20260920'
SIZE = 1024 * 1024
SEED = b'e2b-benchmark-file-transport-sha256-counter-v1'
SAFE_RESPONSE_HEADERS = ('content-encoding', 'content-length')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def deterministic_bytes(size=SIZE):
    return b''.join(hashlib.sha256(SEED + index.to_bytes(8, 'big')).digest()
                    for index in range((size+31)//32))[:size]


def decode_entity(raw, content_encoding):
    result = raw
    for coding in reversed([x.strip().lower() for x in (content_encoding or '').split(',') if x.strip()]):
        if coding == 'identity':
            continue
        if coding in ('gzip', 'x-gzip'):
            result = gzip.decompress(result)
        elif coding == 'deflate':
            try:
                result = zlib.decompress(result)
            except zlib.error:
                result = zlib.decompress(result, -zlib.MAX_WBITS)
        else:
            raise RuntimeError('Unexpected HTTP content coding in raw probe: ' + coding)
    return result


def safe_headers(response):
    return {
        'request_accept_encoding': response.request.headers.get('accept-encoding'),
        'response_headers': {name: response.headers.get(name) for name in SAFE_RESPONSE_HEADERS},
    }


def observation(response, expected, *, decoded, raw=None):
    record = {
        **safe_headers(response), 'status_code': response.status_code,
        'num_bytes_downloaded': response.num_bytes_downloaded,
        'decoded_bytes': len(decoded), 'decoded_sha256': sha(decoded),
        'expected_bytes': len(expected), 'expected_sha256': sha(expected),
        'content_matches': decoded == expected,
    }
    if raw is not None:
        record.update(raw_http_entity_bytes=len(raw), raw_http_entity_sha256=sha(raw),
                      raw_to_logical_ratio=len(raw)/len(expected))
    return record


def list_all(config):
    page = Sandbox.list(query=SandboxQuery(state=[SandboxState.RUNNING, SandboxState.PAUSED]),
                        limit=100, request_timeout=20, retries=0, **config)
    result = []
    while page.has_next:
        result.extend(page.next_items())
    return result


def owns(info, metadata):
    return all((info.metadata or {}).get(key) == value for key, value in metadata.items())


def guard(config, metadata):
    if any(not owns(info, metadata) for info in list_all(config)):
        raise RuntimeError('Foreign or different benchmark sandbox present; diagnostic paused')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--template', default='base-4k-4g')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    payloads = {'repeated_x': b'x' * SIZE, 'sha256_counter': deterministic_bytes()}
    if args.dry_run:
        print(json.dumps({'template': args.template, 'service_requests': 0,
            'payloads': {name: {'bytes':len(data), 'sha256':sha(data)} for name,data in payloads.items()},
            'accept_encoding_modes': ['inherited_default', 'identity', 'gzip']}))
        return 0
    if args.output.exists():
        raise SystemExit('Output already exists; inspect before allocating another diagnostic sandbox')
    value = json.loads(args.config.read_text())
    config = {name:value[name] for name in ('api_key','api_url','sandbox_url')}
    run_id = args.run_id or 'transport-' + uuid.uuid4().hex
    metadata = {'purpose': PURPOSE, 'run_id': run_id}
    guard(config, metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        'schema_version': 1, 'status': 'running', 'run_id': run_id,
        'purpose': PURPOSE, 'template': args.template, 'started_utc': utc_now(),
        'endpoint': config['api_url'], 'observations': [],
        'versions': {name:importlib.metadata.version(name) for name in ('e2b','httpx','pyqwest')},
        'script_sha256': sha(Path(__file__).read_bytes()),
        'payload_generator': 'SHA256(seed || big-endian uint64 counter), concatenated then truncated',
        'payload_seed': SEED.decode(),
        'scope': 'Correctness and HTTP compression diagnosis only; no throughput or latency claim',
        'counter_semantics': {
            'native_httpx_raw': 'iter_raw and num_bytes_downloaded count the HTTP response entity before HTTPX content decoding; excludes HTTP headers/transfer framing and TCP/TLS overhead',
            'sdk_envd_http': 'num_bytes_downloaded counts bytes delivered to HTTPX by pyqwest; lower transport may already decompress and strip content headers',
            'public_sdk': 'files.read returns logical decoded content; gzip=False does not by itself prove Accept-Encoding identity',
            'request_header_scope': 'Accept-Encoding visible on the HTTPX request; lower-layer implicit header changes are not packet-captured',
        },
    }
    def save():
        temp = args.output.with_suffix(args.output.suffix+'.tmp')
        temp.write_text(json.dumps(report,indent=2)+'\n');os.chmod(temp,0o600);temp.replace(args.output)
    save()
    sbx = None
    uncertain_create = True
    problem = None
    def stop(signum, frame):
        raise KeyboardInterrupt('Diagnostic interrupted')
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        sbx = Sandbox.create(args.template, timeout=600, metadata=metadata,
                             request_timeout=90, retries=0, **config)
        uncertain_create = False
        report['sandbox_id'] = sbx.sandbox_id
        info = sbx.get_info(request_timeout=20)
        if info.memory_mb != 4096 or info.cpu_count != 2:
            raise RuntimeError('Fixture differs from expected 2 vCPU / 4096 MiB')
        report['fixture'] = {'template_id':info.template_id,'memory_mib':info.memory_mb,
                             'cpu_count':info.cpu_count,'envd_version':info.envd_version}
        report['sdk_http_transport'] = type(sbx._envd_api._transport).__module__ + '.' + type(sbx._envd_api._transport).__name__
        save()
        with httpx.Client(trust_env=False, timeout=30, transport=httpx.HTTPTransport(retries=0)) as native:
            for payload_name, payload in payloads.items():
                guard(config, metadata)
                path = '/tmp/e2b-transport-' + payload_name + '.bin'
                sbx.files.write(path, payload, user='root', request_timeout=30)
                params = {'path':path,'username':'root'}
                inherited_headers = dict(sbx._envd_api.headers)
                for mode, encoding in [('inherited_default',None),('identity','identity'),('gzip','gzip')]:
                    headers = dict(inherited_headers)
                    if encoding is not None:
                        headers['accept-encoding'] = encoding
                    with native.stream('GET', config['sandbox_url']+'/files',
                                       headers=headers, params=params) as response:
                        response.raise_for_status()
                        raw = b''.join(response.iter_raw())
                        decoded = decode_entity(raw, response.headers.get('content-encoding'))
                        row = observation(response,payload,decoded=decoded,raw=raw)
                        row.update(payload=payload_name,transport='native_httpx_raw',accept_encoding_mode=mode)
                    report['observations'].append(row);save()
                    if not row['content_matches']:
                        raise RuntimeError('Native raw HTTP decode/hash mismatch')
                    response = sbx._envd_api.get('/files',params=params,
                        headers={} if encoding is None else {'Accept-Encoding':encoding},timeout=30)
                    try:
                        response.raise_for_status()
                        row = observation(response,payload,decoded=response.content)
                        row.update(payload=payload_name,transport='sdk_envd_http',accept_encoding_mode=mode,
                                   raw_http_entity_bytes=None)
                    finally:
                        response.close()
                    report['observations'].append(row);save()
                    if not row['content_matches']:
                        raise RuntimeError('SDK envd HTTP decode/hash mismatch')
                # Observe the public SDK's actual HTTP response object without
                # changing any header or decoder. The hook saves no unsafe field.
                captured = []
                def capture(response):
                    captured.append(response)
                hooks = sbx._envd_api.event_hooks['response']
                hooks.append(capture)
                try:
                    actual = bytes(sbx.files.read(path,format='bytes',user='root',request_timeout=30))
                finally:
                    hooks.remove(capture)
                if len(captured) != 1:
                    raise RuntimeError('Expected one public SDK files.read response')
                row = observation(captured[0],payload,decoded=actual)
                row.update(payload=payload_name,transport='public_sdk_files_read',
                           accept_encoding_mode='public_default_gzip_false',raw_http_entity_bytes=None)
                report['observations'].append(row);save()
                if not row['content_matches']:
                    raise RuntimeError('Public SDK files.read hash mismatch')
        report['status'] = 'passed'
    except BaseException as error:
        problem = type(error).__name__
        report.update(status='failed',error_class=problem,
                      error=str(error).replace(config['api_key'],'<redacted>')[:1000])
    finally:
        report['cleanup_ok'] = False
        try:
            if sbx is not None:
                sbx.kill(request_timeout=30)
            minimum_end = time.monotonic() + (150 if uncertain_create else 0)
            deadline = minimum_end + 20
            empty_count = 0
            while time.monotonic() < deadline:
                owned = [info for info in list_all(config) if owns(info,metadata)]
                report['owned_remaining'] = len(owned)
                for info in owned:
                    Sandbox.kill(info.sandbox_id,request_timeout=30,retries=0,**config)
                if not owned:
                    empty_count += 1
                    if empty_count >= 2 and time.monotonic() >= minimum_end:
                        report['cleanup_ok'] = True
                        break
                else:
                    empty_count = 0
                time.sleep(1)
        except BaseException as error:
            report['cleanup_error_class'] = type(error).__name__
        report['finished_utc'] = utc_now();save()
    print(json.dumps({'status':report['status'],'cleanup_ok':report['cleanup_ok'],
                      'observation_count':len(report['observations']),'output':str(args.output)}))
    return 0 if report['status']=='passed' and report['cleanup_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
