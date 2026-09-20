"""Validate the delivered analysis against its recorded evidence; no experiment runs."""
import ast
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1]
checks = {}


def check(name, condition, details=None):
    if not condition:
        raise AssertionError(name)
    checks[name] = details if details is not None else True


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for key in ['href', 'src']:
            if key in attrs:
                self.urls.append(attrs[key])


def main():
    ep = pd.read_csv(OUT/'data/episodes.csv')
    groups = pd.read_csv(OUT/'data/groups.csv')
    req = pd.read_csv(OUT/'data/model_requests.csv.gz')
    tool = pd.read_csv(OUT/'data/tool_actions.csv.gz')
    rpc = pd.read_csv(OUT/'data/sandbox_rpc.csv.gz')
    summary = json.loads((OUT/'data/summary.json').read_text())
    check('256_unique_episodes', len(ep) == 256 and ep.session_id.nunique() == 256)
    sizes = ep.groupby(['batch', 'instance_id']).size()
    check('32_groups_of_eight', len(sizes) == len(groups) == 32 and (sizes == 8).all())
    check('eight_distinct_sample_indices_per_group', all(set(g.rollout_index) == set(range(8)) for _, g in ep.groupby(['batch', 'instance_id'])))
    check('128_episodes_per_batch', ep.groupby('batch').size().to_dict() == {1: 128, 2: 128})
    check('complete_parseable_verification', ep[['finished', 'eval_completed', 'found_eval_status']].all().all())
    check('reported_result_counts', ep.groupby('batch').reward.sum().to_dict() == {1: 26, 2: 32})
    for name, frame, expected in [('queries', req, 9970), ('tools', tool, 9950), ('rpc', rpc, 47742)]:
        check(name+'_counts_and_session_join', len(frame) == expected and set(frame.session_id) == set(ep.session_id), expected)
    byid = ep.set_index('session_id')
    for field, actual in [('llm_requests', req.groupby('session_id').size()),
                          ('turns', req.groupby('session_id').size()),
                          ('logical_tool_calls', tool.groupby('session_id').size()),
                          ('api_completion_tokens_sum', req.groupby('session_id').completion_tokens.sum())]:
        check(field+'_reconciles', (byid[field] == actual.reindex(byid.index)).all())
    check('generated_tokens_equal_training_masks', (ep.api_completion_tokens_sum == ep.training_model_tokens).all() and ep.training_model_tokens.sum() == 2759570)
    check('gateway_turns_definition', (ep.trajectory_turns == ep.turns + 2).all())
    check('one_timeout_one_format_error', (tool.status == 'timeout').sum() == ep.tool_timeouts.sum() == 1 and (tool.status == 'format_error').sum() == ep.tool_errors.sum() == 1)
    check('no_budget_termination', set(ep.termination_reason) == {'finished'} and ep.turns.max() < 200 and req.context_plus_completion_tokens.max() < 131072)
    phases = pd.read_csv(OUT/'data/step1_wall_phases.csv')
    check('step1_wall_phases_reconcile', np.isclose(phases.seconds.sum(), summary['step1_metrics']['timing_s/step']) and np.isclose(phases.percent.sum(), 100))
    parts = ['create_wait_and_api_s', 'post_create_setup_s', 'model_roundtrip_s', 'logical_tools_s', 'verifier_s', 'destroy_s', 'agent_local_residual_s', 'other_episode_s']
    check('each_episode_phases_reconcile', np.allclose(ep[parts].sum(axis=1), ep.episode_s))
    aggregate = pd.read_csv(OUT/'data/aggregate_runner_phases.csv')
    check('cumulative_phases_reconcile', np.isclose(aggregate.seconds.sum(), ep.episode_s.sum()) and np.isclose(aggregate.runner_lifetime_percent.sum(), 100))
    check('subsecond_boundary_residuals', ep.other_episode_s.abs().max() < 2)
    for batch, batch_ep in ep.groupby('batch'):
        stats = summary['batches'][str(batch)]
        endings = np.sort(batch_ep.task_end.to_numpy())
        check(f'batch{batch}_tail_reconciles', np.isclose(endings[-1]-endings[115], stats['tail_after_90pct_s']) and np.isclose(endings[-1]-endings[-2], stats['last_sample_penalty_s']))

    original = json.loads((OUT/'data/source-manifest.json').read_text())
    for name, metadata in original.items():
        path = Path(name)
        check('source:'+name, path.stat().st_size == metadata['bytes'] and digest(path) == metadata['sha256'])
    evidence = json.loads((OUT/'data/evidence-manifest.json').read_text())
    for name, metadata in evidence.items():
        path = OUT/'evidence'/name
        check('evidence:'+name, path.stat().st_size == metadata['bytes'] and digest(path) == metadata['sha256'] == digest(Path(metadata['source'])))
    # Keep output compact; inventory files retain per-file hashes.
    checks['original_files_hash_verified'] = len(original)
    checks['copied_evidence_files_hash_verified'] = len(evidence)
    for key in list(checks):
        if key.startswith(('source:', 'evidence:')):
            del checks[key]

    pages = {}
    for path in OUT.glob('*.html'):
        parser = Links(); parser.feed(path.read_text()); pages[path] = parser
    link_count = 0
    for path, parser in pages.items():
        for url in parser.urls:
            bits = urlsplit(url)
            check('offline_assets_only', not bits.scheme and not bits.netloc)
            target = (path.parent/unquote(bits.path)).resolve() if bits.path else path
            check('link:'+str(path)+':'+url, target.exists())
            if bits.fragment and target in pages:
                check('fragment:'+url, unquote(bits.fragment) in pages[target].ids)
            link_count += 1
    for path in OUT.glob('*.md'):
        for url in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            bits = urlsplit(url)
            if not bits.scheme:
                check('markdown_link:'+url, (path.parent/unquote(bits.path)).exists())
                link_count += 1
    for key in list(checks):
        if key.startswith(('link:', 'fragment:', 'markdown_link:')):
            del checks[key]
    checks['document_links_checked'] = link_count
    check('11_png_svg_pairs', len(list((OUT/'figures').glob('*.png'))) == len(list((OUT/'figures').glob('*.svg'))) == 11)
    for png in (OUT/'figures').glob('*.png'):
        check('figure_pair:'+png.stem, png.with_suffix('.svg').is_file() and png.stat().st_size > 1000)
    for path in (OUT/'analysis').glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
    checks['analysis_python_syntax'] = 'passed'

    candidates = sorted((Path.home()/'.vscode-server/cli/servers').glob('*/server/node'))
    node = shutil.which('node') or (str(candidates[0]) if candidates else None)
    if node:
        run = subprocess.run([node, str(OUT/'analysis/validate_explorer.cjs')], check=True, capture_output=True, text=True)
        checks['explorer_functionality'] = json.loads(run.stdout)
    else:
        checks['explorer_functionality'] = {'status': 'not executed; Node unavailable'}
    # Config/evidence checks flag credential-shaped literals, never output their values.
    suspicious = []
    pattern = re.compile(r'(?i)(?:api[_-]?key|access[_-]?token|password|secret[_-]?key)["\x27]?\s*[:=]\s*["\x27]([^"\x27\n]+)["\x27]')
    for path in (OUT/'evidence').rglob('*'):
        if path.suffix not in {'.json', '.jsonl', '.yaml', '.yml'}:
            continue
        for match in pattern.finditer(path.read_text()):
            val = match.group(1)
            if len(val) >= 16 and not val.startswith(('$', '${')) and val.lower() not in {'none', 'null', 'redacted'}:
                suspicious.append(str(path.relative_to(OUT)))
    check('no_credential_shaped_config_literals', not suspicious, {'flagged_files': sorted(set(suspicious)), 'method': 'heuristic scan of copied JSON/YAML; secret files are not copied'})
    result = {'passed': True, 'validated_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'checks': checks,
              'visual_review': ['02_attempt_timeline', '06_all_8_sample_groups', '07_anyio_eight_samples'],
              'browser_layout_validation': 'Not performed; no browser installed. Figures reviewed as rendered PNGs.'}
    (OUT/'data/validation.json').write_text(json.dumps(result, indent=2)+'\n')
    manifest = {str(p.relative_to(OUT)): {'bytes': p.stat().st_size, 'sha256': digest(p)}
                for p in sorted(OUT.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p != OUT/'report-manifest.json'}
    (OUT/'report-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'passed': True, 'checks': len(checks), 'source_files': len(original),
                      'evidence_files': len(evidence), 'document_links': link_count,
                      'report_files': len(manifest), 'explorer': checks['explorer_functionality']}, indent=2))


if __name__ == '__main__':
    main()
