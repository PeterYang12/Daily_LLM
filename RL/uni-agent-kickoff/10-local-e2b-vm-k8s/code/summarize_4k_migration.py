"""Create a compact report from completed migration and live verification records."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'migrations/20260920-4k-512'


def read(name):
    return json.loads((RUN / name).read_text())


def main():
    migration = read('migration-state.json')
    templates = read('after/all-template-verification.json')
    capacity = read('after/capacity-512.json')
    config = read('after/final-service-state.json')
    sdk = read('after/sdk-functional.json')
    before_pages = read('after/page-before-pause.json')
    after_pages = read('after/page-after-resume.json')
    capacity_pages = read('after/capacity-template-pages.json')
    inventory = read('before/template-inventory.json')
    identities = {x['templateID']:x for x in read('after/template-identities.json')}
    assert len(migration['entries']) == len(inventory) == 91
    assert all(e['phase'] == 'ready' for e in migration['entries'].values())
    for item in inventory:
        current = identities[item['template_id']]
        assert set(current['names']) == set(item['names'])
        assert current['cpuCount'] == item['cpu_count'] and current['memoryMB'] == item['memory_mb']
        expected = migration['entries'][f"{item['template_id']}:{item['build_id']}"]['new_build_id']
        assert current['buildID'] == expected
    assert templates['status'] == 'passed' and templates['passed'] == 91
    assert all(r['cleanup_ok'] and r['pages']['four_kib_verified'] for r in templates['results'])
    assert capacity['status'] == 'passed'
    assert capacity['quota_probe']['status'] == 'expected_team_quota'
    assert capacity['cleanup']['status'] == 'verified_zero'
    assert capacity['hold']['status'] == 'passed' and capacity['hold']['elapsed_seconds'] >= 60
    assert capacity['post_hold_tools']['checked_count'] == 512
    assert config['ordinary_page_configuration_verified'] and config['running_list_first_page_count'] == 0
    assert sdk['status'] == 'passed'
    assert all(p['four_kib_verified'] for p in (before_pages, after_pages, capacity_pages))
    first_512 = next(x for x in capacity['milestones'] if x['target'] == 512)
    hashes = {name: hashlib.sha256((RUN / 'bin' / name).read_bytes()).hexdigest()
              for name in ('api', 'orchestrator', 'migrator')}
    report = {
        'status': 'passed', 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'endpoint': config['endpoint'], 'original_client_credentials_still_accepted': True,
        'source_commit': 'e7b22501988c826563bf1b3cf0d22a1183e7affe',
        'custom_runtime_version': 'local-4k-512-20260920', 'go_version': '1.26.8',
        'binary_sha256': hashes, 'guest_memory_page_bytes': 4096,
        'hugetlb_reserved_gib': 0, 'team_concurrent_sandboxes': 512,
        'node_max_sandboxes': 512, 'node_max_starting_instances': 3,
        'nbd_devices': 1024, 'nbd_warm_pool': 32,
        'neighbor_thresholds': config['neighbor_thresholds'],
        'qemu_nofile': config['qemu_nofile'],
        'nginx_worker_nofile': 65536,
        'vm_memory_gib': int(config['memory']['MemTotal'].split()[0]) / (1024 * 1024),
        'vm_vcpus': int(config['node_capacity']['cpu']),
        'vm_reboot_verified': (RUN/'after/boot-id-before-reboot.txt').read_text().strip() != config['boot_id'],
        'node_hugepages_allocatable': config['node_allocatable']['hugepages-2Mi'],
        'postgres_huge_pages': config['postgres_huge_pages'],
        'migrated_templates': 91, 'runtime_verified_templates': 91,
        'template_resources_and_names_preserved': True,
        'pause_resume_4k_verified': True,
        'sdk_functional_checks': len(sdk['basic']['checks']),
        'capacity_test': {
            'run_id': capacity['run_id'], 'template': read('after/capacity-template.json'),
            'simultaneously_running': 512, 'create_inflight': capacity['create_inflight'],
            'seconds_to_512_running': first_512['elapsed_seconds'],
            'wall_seconds_including_checks_and_cleanup': capacity['wall_seconds'],
            'milestones': capacity['milestones'], 'memory_checks': capacity['memory_checks'],
            'quota_probe': capacity['quota_probe'], 'cleanup': capacity['cleanup'],
            'hold': capacity['hold'],
            'post_hold_tools': capacity['post_hold_tools'],
            'retry_events': capacity.get('retry_events', []),
            'scope': capacity['scope'],
        },
        'limits': [
            '512 is the configured running-instance quota and a verified lightweight test count, not a full-workload performance guarantee.',
            '512 instances each fully using 4 GiB would require 2 TiB guest RAM, above this VM physical memory.',
            'Historical build UUIDs keep their old snapshots; current template tags were migrated.',
            'Creation/restoration is still limited to three in-flight instances per node; use bounded client concurrency and retry.',
        ],
        'evidence_directory': str(RUN),
    }
    first_path = RUN/'after/capacity-512-first-attempt.json'
    if first_path.exists():
        first = json.loads(first_path.read_text())
        report['first_capacity_attempt'] = {
            'status': first['status'], 'created_instances': len(first['sandboxes']),
            'test_error': first['test_error'], 'cleanup': first['cleanup']['status'],
            'finding': 'Kernel logs show ARP neighbor table overflow starting before the RPC timeouts; the original gc_thresh3 was 1024.',
            'response': 'Raised bounded IPv4/IPv6 neighbor table thresholds to 4096/8192/16384; retained health gates and added bounded client retries and a 60-second hold.',
        }
    report['earlier_capacity_attempts'] = []
    for label in ('first', 'second', 'third'):
        path = RUN/f'after/capacity-512-{label}-attempt.json'
        if path.exists():
            attempt = json.loads(path.read_text())
            report['earlier_capacity_attempts'].append({
                'attempt':label, 'status':attempt['status'],
                'created_instances':len(attempt['sandboxes']),
                'test_error':attempt['test_error'], 'cleanup':attempt['cleanup']['status'],
            })
    network_samples = [json.loads(line) for line in (RUN/'logs/arp-capacity-fourth-attempt.jsonl').read_text().splitlines()]
    network_samples = [x for x in network_samples if 'entries' in x]
    report['neighbor_capacity_test'] = {
        'old_gc_thresh3':1024, 'configured_gc_thresh3':16384,
        'sample_count':len(network_samples),
        'peak_entries':max(x['entries'] for x in network_samples),
        'table_fulls_before':network_samples[0]['table_fulls'],
        'table_fulls_after':network_samples[-1]['table_fulls'],
    }
    assert report['neighbor_capacity_test']['table_fulls_before'] == report['neighbor_capacity_test']['table_fulls_after']
    (RUN/'after/summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','migrated_templates','runtime_verified_templates','team_concurrent_sandboxes','hugetlb_reserved_gib')}))


if __name__ == '__main__':
    main()
