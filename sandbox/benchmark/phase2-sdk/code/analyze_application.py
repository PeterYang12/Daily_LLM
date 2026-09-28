"""Reproducible local-only analysis of nine frozen SDK/lifecycle result files.

First run with --freeze to copy the exact nine completed JSONL inputs and their
execution manifests. Subsequent runs verify and read only the frozen copies.
No E2B client, API request, sandbox command or shared analysis module is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys


ROOT = Path(__file__).resolve().parents[2]
RAW = Path('/data2/yuhanya/e2b-benchmark/session-20260920')
APP = ROOT / 'phase2-sdk/results/application'
LIFE = ROOT / 'phase1-service/results/lifecycle'
OFFICIAL_STAGES = ['create', 'health_check', 'run_code_first', 'run_code_second']
LIFECYCLE_STAGES = ['pause', 'resume_connect', 'ready_state_probe']
SOURCE_FILES = [
    ('official', 'sdk', 'phase2-sdk', f'official-code-interpreter-r{repeat}.jsonl')
    for repeat in range(1, 4)
] + [
    ('lifecycle', adapter, phase, f'lifecycle-{adapter}-r{repeat}.jsonl')
    for adapter, phase in [('service', 'phase1-service'), ('sdk', 'phase2-sdk')]
    for repeat in range(1, 4)
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def write_csv(path, rows):
    if not rows:
        raise ValueError('Refusing an empty CSV')
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def copy_frozen(source, destination):
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha(destination) != digest:
            raise ValueError('Frozen copy differs from source: ' + str(destination))
    else:
        with destination.open('xb') as handle:
            handle.write(data)
        os.chmod(destination, 0o444)
    return {'original_path': str(source), 'frozen_path': str(destination),
            'sha256': digest, 'size_bytes': len(data)}


def freeze_inputs():
    target = APP / 'INPUTS-FROZEN.json'
    if target.exists():
        return load_frozen()
    entries = []
    code_sources = {}
    for family, adapter, phase, name in SOURCE_FILES:
        source = RAW / phase / name
        frozen = APP / 'frozen' / name
        entry = {'family': family, 'adapter': adapter, **copy_frozen(source, frozen)}
        manifest_source = source.with_suffix('.manifest.json')
        entry['execution_manifest'] = copy_frozen(manifest_source, APP / 'frozen' / manifest_source.name)
        execution = json.loads(manifest_source.read_text())
        script = ROOT / execution['cell']['script']
        if sha(script) != execution['runner_sha256']:
            raise ValueError('Executed script differs from currently available source: ' + str(script))
        if str(script) not in code_sources:
            code_sources[str(script)] = copy_frozen(script, APP/'frozen-code'/script.name)
        entries.append(entry)
    supporting = []
    for relative in ['phase2-sdk/results/template-build.json', 'phase2-sdk/results/template-smoke.json',
                     'phase2-sdk/configs/upstream-sources.json', 'phase2-sdk/configs/client-requirements.lock']:
        source = ROOT / relative
        supporting.append(copy_frozen(source, APP / 'frozen-support' / source.name))
    document = {'schema_version': 1, 'frozen_utc': datetime.now(timezone.utc).isoformat(),
                'measurement_input_count': 9, 'selection': 'Exactly three official SDK runs and three lifecycle runs per adapter; no phase0/preflight/smoke measurement inputs',
                'measurement_inputs': entries, 'executed_code': list(code_sources.values()),
                'supporting_fixture_provenance': supporting}
    write_json(target, document)
    return document


def load_frozen():
    document = json.loads((APP / 'INPUTS-FROZEN.json').read_text())
    if document['measurement_input_count'] != 9:
        raise ValueError('Expected exactly nine frozen measurement files')
    all_entries = list(document['measurement_inputs']) + document['executed_code'] + document['supporting_fixture_provenance']
    all_entries += [entry['execution_manifest'] for entry in document['measurement_inputs']]
    for entry in all_entries:
        if sha(entry['frozen_path']) != entry['sha256']:
            raise ValueError('Frozen input SHA mismatch: ' + entry['frozen_path'])
    return document


def quantile(values, p):
    values = sorted(values)
    if not values:
        return None
    position = (len(values)-1) * p
    lo = int(position)
    hi = min(lo+1, len(values)-1)
    return values[lo] + (values[hi]-values[lo]) * (position-lo)


def stats(values):
    return {'n': len(values), 'mean_ms': statistics.mean(values) if values else None,
            'p50_ms': quantile(values, .5), 'p95_ms': quantile(values, .95),
            'p99_ms': quantile(values, .99), 'min_ms': min(values) if values else None,
            'max_ms': max(values) if values else None}


def audit_and_load(document):
    runs, operations, rounds, quality = [], [], [], []
    run_ids = set()
    official_ids = set()
    for source in document['measurement_inputs']:
        records = [json.loads(line) for line in Path(source['frozen_path']).read_text().splitlines()]
        finals = [row for row in records if row.get('record_type') == 'run']
        if len(finals) != 1:
            raise ValueError('Expected one terminal run record')
        final = finals[0]
        family, adapter = source['family'], source['adapter']
        stages = OFFICIAL_STAGES if family == 'official' else LIFECYCLE_STAGES
        measured = [row for row in records if row.get('record_type') == 'operation'
                    and row.get('window') == 'measurement']
        if final['run_id'] in run_ids:
            raise ValueError('Duplicate run ID')
        run_ids.add(final['run_id'])
        expected = 20 * len(stages)
        failures = []
        def check(condition, reason):
            if not condition:
                failures.append(reason)
        check(len(measured) == expected, 'operation count')
        check(Counter(row['operation'] for row in measured) == Counter({stage:20 for stage in stages}), 'per-stage counts')
        check(len({row['operation_id'] for row in measured}) == expected, 'unique operation IDs')
        check(all(row.get('status') == 'success' and row.get('state_check') is True for row in measured), 'successful validated operations')
        check(final.get('cleanup_ok') is True and final.get('stop_reason') == 'completed', 'run status/cleanup')
        check(all(final.get(key) == expected for key in ('scheduled_count','dispatched_count','completed_count','successful_count')), 'terminal counters')
        check(final.get('concurrency') == 1 and final.get('resident_count') == 1 and final.get('memory_mib') == 4096, 'fixture dimensions')
        execution = json.loads(Path(source['execution_manifest']['frozen_path']).read_text())
        check(execution.get('returncode') == 0, 'process exit status')
        grouped = defaultdict(list)
        for row in measured:
            check(row['run_id'] == final['run_id'], 'row run identity')
            check(math.isfinite(row['duration_ms']) and row['duration_ms'] >= 0, 'valid duration')
            check(abs((row['end_offset_s']-row['start_offset_s'])*1000-row['duration_ms']) < .001, 'timer consistency')
            check(final['measurement_start_offset_s'] <= row['start_offset_s'] <= row['end_offset_s'] <= final['measurement_end_offset_s']+.001, 'operation within recorded window')
            grouped[row['iteration']].append(row)
            operations.append({'family':family,'adapter':adapter,'repeat':final['repeat'],
                'run_id':final['run_id'],'iteration':row['iteration'],'operation':row['operation'],
                'duration_ms':row['duration_ms'],'start_offset_s':row['start_offset_s'],
                'end_offset_s':row['end_offset_s'],'status':row['status'],'state_check':row.get('state_check'),
                'sandbox_id':row.get('sandbox_id'), 'raw':row})
        check(sorted(grouped) == list(range(1,21)), 'twenty complete iterations')
        for iteration, sequence in sorted(grouped.items()):
            check([row['operation'] for row in sequence] == stages, 'ordered stages')
            if len(sequence) != len(stages):
                continue
            check(all(a['end_offset_s'] <= b['start_offset_s'] for a,b in zip(sequence,sequence[1:])), 'serial operation order')
            cycle = {'family':family,'adapter':adapter,'repeat':final['repeat'],'run_id':final['run_id'],
                     'iteration':iteration,'cycle_elapsed_ms':(sequence[-1]['end_offset_s']-sequence[0]['start_offset_s'])*1000,
                     'stage_sum_ms':sum(row['duration_ms'] for row in sequence)}
            cycle['between_stage_gap_ms'] = cycle['cycle_elapsed_ms']-cycle['stage_sum_ms']
            if family == 'official':
                check(sequence[0].get('sandbox_id') not in official_ids, 'fresh sandbox per official cycle')
                official_ids.add(sequence[0].get('sandbox_id'))
                first,second=sequence[2],sequence[3]
                cycle.update(first_run_ms=first['duration_ms'],second_run_ms=second['duration_ms'],
                             first_to_second_ratio=first['duration_ms']/second['duration_ms'],
                             create_to_first_code_end_ms=(first['end_offset_s']-sequence[0]['start_offset_s'])*1000)
            rounds.append(cycle)
        cleanups = [row for row in records if row.get('record_type')=='cleanup']
        if family == 'official':
            check(len(cleanups)==20 and all(row.get('status')=='success' for row in cleanups), 'twenty sandbox cleanup events')
            check(final.get('owned_remaining')==0, 'no remaining owned sandbox')
        else:
            probes=[row for row in measured if row['operation']=='ready_state_probe']
            check(all(row.get('memory_preserved') is True and row.get('file_preserved') is True for row in probes), 'memory/file preservation')
            check(len({row.get('sandbox_id') for row in measured})==1, 'same lifecycle sandbox')
            check(len({row.get('token_sha256') for row in probes})==1 and bool(probes[0].get('token_sha256')), 'stable in-memory token hash')
            check(len({row.get('guest_process_pid') for row in probes})==1, 'stable process PID')
            check([row.get('counter') for row in probes]==list(range(2,22)), 'preserved incrementing counter')
            paused=[row for row in records if row.get('record_type')=='validation' and row.get('operation')=='paused_state']
            check(len(paused)==20 and all(row.get('state_check') is True for row in paused), 'paused-state validation')
            check(len(cleanups)==1 and cleanups[0].get('cleanup_ok') is True, 'lifecycle cleanup event')
            check(final.get('roundtrips_completed')==20, 'roundtrip terminal counter')
            if adapter=='service':
                check(all(row.get('http_status')==(204 if row['operation']=='pause' else 201)
                          for row in measured if row['operation']!='ready_state_probe'), 'raw control HTTP response codes')
        quality.append({'run_id':final['run_id'],'family':family,'adapter':adapter,'repeat':final['repeat'],
                        'valid':not failures,'issues':failures,'operations':len(measured),
                        'state_checks_true':sum(row.get('state_check') is True for row in measured),
                        'cleanup_ok':final.get('cleanup_ok'),'source':source['frozen_path']})
        window=(final['measurement_end_offset_s']-final['measurement_start_offset_s'])*1000
        run_rounds=[row for row in rounds if row['run_id']==final['run_id']]
        runs.append({'family':family,'adapter':adapter,'repeat':final['repeat'],'run_id':final['run_id'],
            'started_utc':final['run_started_utc'],'iterations':20,'stage_count':len(stages),
            'measurement_window_ms':window,'stage_sum_ms':sum(row['duration_ms'] for row in measured),
            'non_stage_window_ms':window-sum(row['duration_ms'] for row in measured),
            'cycle_elapsed_p50_ms':quantile([row['cycle_elapsed_ms'] for row in run_rounds],.5),
            'successful_operations':sum(row['status']=='success' for row in measured),
            'cleanup_ok':final['cleanup_ok'],'runner_sha256':execution['runner_sha256']})
    if any(not row['valid'] for row in quality):
        write_json(APP/'data_quality.json',quality)
        raise ValueError('Input audit failed; see data_quality.json')
    return runs,operations,rounds,quality


def stage_summaries(operations):
    grouped=defaultdict(list)
    for row in operations:
        grouped[(row['family'],row['adapter'],row['operation'])].append(row)
    output=[]
    for (family,adapter,operation),rows in grouped.items():
        values=[row['duration_ms'] for row in rows if row['status']=='success']
        output.append({'family':family,'adapter':adapter,'operation':operation,
            **stats(values),'failures':sum(row['status']!='success' for row in rows),
            'state_checks_true':sum(row['state_check'] is True for row in rows),
            'repeat_count':len({row['run_id'] for row in rows})})
    return output


def figures(operations):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,
                         'svg.hashsalt':'e2b-application-lifecycle-20260920'})
    def save(fig,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        fig.savefig(path.with_suffix('.png'),dpi=190,bbox_inches='tight',metadata={'Software':'E2B local analysis'})
        fig.savefig(path.with_suffix('.svg'),bbox_inches='tight',metadata={'Date':None})
        plt.close(fig)
    official=[row for row in operations if row['family']=='official']
    fig,axes=plt.subplots(1,2,figsize=(13,5.1),gridspec_kw={'width_ratios':[1.5,1]})
    labels=['Create','Health via\ncommands.run','First\nrun_code','Second\nrun_code']
    values=[[row['duration_ms'] for row in official if row['operation']==stage] for stage in OFFICIAL_STAGES]
    box=axes[0].boxplot(values,tick_labels=labels,showfliers=False,patch_artist=True,widths=.5)
    for patch in box['boxes']:patch.set(facecolor='#dbeafe',edgecolor='#2563eb')
    rng=random.Random(20260920)
    colors=['#2563eb','#d97706','#15803d']
    for index,stage in enumerate(OFFICIAL_STAGES,1):
        for repeat,color in enumerate(colors,1):
            ys=[row['duration_ms'] for row in official if row['operation']==stage and row['repeat']==repeat]
            axes[0].scatter([index+rng.uniform(-.16,.16) for _ in ys],ys,s=16,color=color,alpha=.55,zorder=3)
        axes[0].scatter([index],[quantile(values[index-1],.95)],marker='D',s=38,color='#111827',zorder=4)
    axes[0].set_yscale('log');axes[0].set_ylabel('SDK call latency (ms; log scale)')
    axes[0].set_title('60 observations per stage (3 runs × 20 cycles)')
    axes[0].grid(axis='y',alpha=.25,which='both')
    keyed=defaultdict(dict)
    for row in official:keyed[(row['run_id'],row['iteration'])][row['operation']]=row
    for repeat,color in enumerate(colors,1):
        pairs=[d for d in keyed.values() if d['run_code_first']['repeat']==repeat]
        axes[1].scatter([d['run_code_first']['duration_ms'] for d in pairs],
                        [d['run_code_second']['duration_ms'] for d in pairs],s=30,color=color,alpha=.7,label=f'Run {repeat}')
    axes[1].plot([40,1600],[40,1600],'--',color='#64748b',linewidth=1,label='Equal latency')
    axes[1].set(xscale='log',yscale='log',xlim=(40,1600),ylim=(40,1600),
                xlabel='First run_code (ms)',ylabel='Second run_code (ms)',title='Paired calls in the same fresh sandbox')
    axes[1].grid(alpha=.2,which='both');axes[1].legend(loc='upper left',fontsize=8)
    axes[0].legend(handles=[Line2D([0],[0],marker='D',linestyle='',color='#111827',label='P95 (descriptive)')],loc='upper left',fontsize=8)
    fig.suptitle('Official SDK workload · local minimal Python/JS template · C1 / 4 GiB',fontsize=13)
    fig.text(.5,.01,'No cold image build in timing; health includes a command + curl; all calls and cleanups passed.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.04,1,.93));save(fig,APP/'plots/official-sdk-stages')

    fig,axes=plt.subplots(2,3,figsize=(13,7.2))
    life=[row for row in operations if row['family']=='lifecycle']
    titles=['Pause: memory snapshot','Resume/connect','Ready + memory/file probe (SDK for both)']
    for index,stage in enumerate(LIFECYCLE_STAGES):
        ax=axes[0,index]
        values=[[row['duration_ms'] for row in life if row['adapter']==adapter and row['operation']==stage] for adapter in ['service','sdk']]
        box=ax.boxplot(values,tick_labels=['Raw HTTP\ncontrols','SDK\ncontrols'],showfliers=False,patch_artist=True,widths=.4)
        for patch,color in zip(box['boxes'],['#93c5fd','#fdba74']):patch.set_facecolor(color)
        for position,ys,color in zip([1,2],values,['#2563eb','#d97706']):
            ax.scatter([position+rng.uniform(-.13,.13) for _ in ys],ys,s=12,color=color,alpha=.5,zorder=3)
            ax.scatter([position],[quantile(ys,.95)],marker='D',s=32,color='#111827',zorder=4)
        if stage=='pause':ax.set_yscale('log')
        ax.set_title(titles[index],fontsize=10);ax.set_ylabel('Latency (ms)' + ('; log scale' if stage=='pause' else ''))
        ax.grid(axis='y',alpha=.25,which='both')
        lower=axes[1,index]
        all_medians=[]
        for repeat,color in enumerate(colors,1):
            medians=[quantile([row['duration_ms'] for row in life if row['operation']==stage and row['adapter']==adapter and row['repeat']==repeat],.5) for adapter in ['service','sdk']]
            all_medians.extend(medians)
            lower.plot([1,2],medians,'o-',color=color,label=f'Run pair {repeat}',alpha=.8)
        lower.set_xticks([1,2],['Raw HTTP\ncontrols','SDK\ncontrols']);lower.set_xlim(.8,2.2)
        lower.set_ylim(0,max(all_medians)*1.15)
        lower.set_title('Run medians matched by repeat ID',fontsize=10);lower.set_ylabel('Median latency (ms)');lower.grid(axis='y',alpha=.25)
    axes[1,2].legend(fontsize=8,loc='best')
    axes[0,0].legend(handles=[Line2D([0],[0],marker='D',linestyle='',color='#111827',label='P95')],loc='upper right',fontsize=8)
    fig.suptitle('Memory-preserving lifecycle · 60 rounds per adapter · C1 / 4 GiB',fontsize=13)
    fig.text(.5,.01,'All checks passed; 3 lineages per adapter. Lines match repeat IDs, not the same sandbox. Differences are descriptive.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.045,1,.94));save(fig,LIFE/'plots/lifecycle-stages')


def markdown_table(rows, columns):
    header='| '+' | '.join(label for _,label in columns)+' |'
    separator='| '+' | '.join('---' for _ in columns)+' |'
    body=[]
    for row in rows:
        cells=[]
        for field,_ in columns:
            value=row[field]
            cells.append(f'{value:.2f}' if isinstance(value,float) else str(value))
        body.append('| '+' | '.join(cells)+' |')
    return '\n'.join([header,separator,*body])


def reports(runs, operations, rounds, summaries, quality):
    official=[row for row in summaries if row['family']=='official']
    lifecycle=[row for row in summaries if row['family']=='lifecycle']
    app_rounds=[row for row in rounds if row['family']=='official']
    app_runs=[row for row in runs if row['family']=='official']
    life_runs=[row for row in runs if row['family']=='lifecycle']
    life_rounds=[row for row in rounds if row['family']=='lifecycle']
    paired=[]
    for stage in LIFECYCLE_STAGES:
        for repeat in range(1,4):
            values={adapter:[row['duration_ms'] for row in operations if row['family']=='lifecycle' and row['adapter']==adapter and row['operation']==stage and row['repeat']==repeat] for adapter in ['service','sdk']}
            raw,public=quantile(values['service'],.5),quantile(values['sdk'],.5)
            paired.append({'operation':stage,'repeat':repeat,'service_p50_ms':raw,'sdk_p50_ms':public,
                           'sdk_minus_service_p50_ms':public-raw,'sdk_to_service_p50_ratio':public/raw})
    notes=['All selected inputs passed count/order/duration/state/cleanup audits.',
           'Quantiles use linear interpolation at (n-1)*p. P99 with n=60 is descriptive, not a stable tail estimate.',
           'No stage-count/window throughput is reported; windows include validation and inter-iteration cleanup gaps.',
           'Sequential run-pair differences do not isolate SDK overhead or establish causality.']
    app={'schema_version':1,'stage_summary':official,'per_run':app_runs,
         'first_vs_second_paired':{'pairs':len(app_rounds),
           'first_to_second_ratio_p50':quantile([row['first_to_second_ratio'] for row in app_rounds],.5),
           'all_second_calls_faster':all(row['second_run_ms']<row['first_run_ms'] for row in app_rounds)},
         'create_to_first_code_end':stats([row['create_to_first_code_end_ms'] for row in app_rounds]),
         'cycle_elapsed':stats([row['cycle_elapsed_ms'] for row in app_rounds]),
         'correctness':{'operations':240,'validated_operations':240,'fresh_sandboxes':60,'cleanup_events':60,'failures':0},
         'template_scope':'Local minimal Python/JavaScript template; not full hosted code-interpreter-v1',
         'notes':notes}
    life={'schema_version':1,'stage_summary':lifecycle,'per_run':life_runs,'paired_run_medians':paired,
          'cycle_elapsed_by_adapter':{adapter:stats([row['cycle_elapsed_ms'] for row in life_rounds if row['adapter']==adapter]) for adapter in ['service','sdk']},
          'correctness':{'operations':360,'validated_operations':360,'memory_file_roundtrips':120,'sandbox_lineages':6,'cleanup_events':6,'failures':0},
          'notes':notes+['Each adapter contains 60 sequential roundtrips across only three sandbox lineages.',
                         'ready_state_probe uses the SDK in both adapters and includes Python process startup, loopback HTTP and validation payload retrieval.']}
    write_json(APP/'summary.json',app);write_json(LIFE/'summary.json',life)
    write_json(APP/'data_quality.json',quality)
    for directory,family,stage_rows,run_rows,cycle_rows in [(APP,'official',official,app_runs,app_rounds),(LIFE,'lifecycle',lifecycle,life_runs,life_rounds)]:
        write_csv(directory/'stage_summary.csv',stage_rows);write_csv(directory/'per_run.csv',run_rows)
        write_csv(directory/'cycles.csv',cycle_rows)
        normalized=[{k:v for k,v in row.items() if k!='raw'} for row in operations if row['family']==family]
        write_csv(directory/'operation_samples.csv',normalized)
    write_csv(LIFE/'paired_run_medians.csv',paired)
    app_labels={'create':'SDK创建','health_check':'首次命令/curl健康检查','run_code_first':'第一次run_code','run_code_second':'第二次run_code'}
    app_table=[{**row,'stage':app_labels[row['operation']]} for row in official]
    table=markdown_table(app_table,[('stage','阶段'),('n','样本数'),('p50_ms','P50 ms'),('p95_ms','P95 ms'),('max_ms','最大 ms')])
    window_table=markdown_table(app_runs,[('repeat','重复'),('measurement_window_ms','整窗口 ms'),('stage_sum_ms','四阶段计时合计 ms'),('non_stage_window_ms','阶段外间隔 ms')])
    app_text=f'''# 官方 SDK 场景结果

固定的3次运行全部完成：每次20个新sandbox、每个执行4个阶段，共240个操作全部成功并通过结果校验，60个sandbox均完成清理。创建通常约{official[0]['p50_ms']:.0f} ms；首次curl健康检查约{official[1]['p50_ms']:.0f} ms，首次解释器执行约{official[2]['p50_ms']:.0f} ms，第二次约{official[3]['p50_ms']:.0f} ms（均为P50）。

{table}

同一sandbox内配对的60组`run_code`中，第二次全部更快；第一次/第二次耗时比的中位数为 **{app['first_vs_second_paired']['first_to_second_ratio_p50']:.2f}**。从创建开始到第一次代码返回的配对实测P50为 **{app['create_to_first_code_end']['p50_ms']:.2f} ms**，这是每个cycle实际时间线计算值，不是把各阶段分位数相加。

![SDK四阶段与配对执行](results/application/plots/official-sdk-stages.png)

[可导出SVG](results/application/plots/official-sdk-stages.svg) · [JSON](results/application/summary.json) · [阶段CSV](results/application/stage_summary.csv) · [逐操作CSV](results/application/operation_samples.csv)

## 实验口径

输入严格限定为`official-code-interpreter-r1..3.jsonl`。每次使用`Sandbox.create → commands.run(curl /health) → run_code(Hello World) → 再次run_code → kill`。计时是公开Python SDK调用返回的完整耗时，`run_code`包含消费响应流。健康检查同时包含命令启动和curl执行，不是纯HTTP ping或网络RTT。镜像下载/模板构建、setup smoke不计入这些样本。

环境为单节点`local-4k-512-20260920`、普通4KiB页、2vCPU/4096MiB、C1/N1。客户端`e2b==2.49.1`、`e2b-code-interpreter==2.10.0`。模板`benchmark-ci-minimal-20260920`保留固定上游server及Python/JS kernel，省略完整云端模板的大量包和其他语言；结果不能代表官方云端`code-interpreter-v1`。模板identity及准备验证的冻结副本位于`results/application/frozen-support`。

三个运行的整窗口边界及组成如下；整窗口保留迭代间清理和少量记录/校验间隔，最后边界在最终清理之前。**不把80个阶段数除以整窗口称为纯服务吞吐。**

{window_table}

表中P50/P95是合并60个观测后的描述性分位数，与主矩阵“先算每run分位数、再汇总run”的口径不同。每阶段n=60来自3次运行。P99仅随JSON/CSV保留，不作为稳定SLA尾延迟结论。图中散点为原始样本，黑色菱形为P95；没有删除慢样本。所有成功和清理结论仅覆盖这60个新实例，不是可靠性保证。

## 对后续定位的意义

创建响应时间并不是首次应用结果时间。当前首条命令和首次解释器执行段明显比后续热执行更慢，应优先分解恢复后首次进程启动、解释器/页面就绪和客户端首次连接，而不能将其统称为SDK开销或网络延迟。是否能通过复用已就绪sandbox/context改善真实任务，需要后续回放验证。

## 复现与输入冻结

九份应用/lifecycle测量输入及执行manifest已共同冻结并校验SHA：[INPUTS-FROZEN.json](results/application/INPUTS-FROZEN.json)。这里只读取其中三份官方SDK运行；phase0预检、权限错误smoke和template smoke均排除。分析不调用服务。复现：

```bash
taskset -c 16-31 /data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \\
  /home/yuhanya/Daily_LLM/sandbox/benchmark/phase2-sdk/code/analyze_application.py
```
'''
    (ROOT/'phase2-sdk/APPLICATION-RESULTS.md').write_text(app_text)
    labels={'pause':'内存快照暂停','resume_connect':'恢复/connect','ready_state_probe':'恢复后状态探针（双方均SDK）'}
    life_table=[{**row,'stage':labels[row['operation']],'adapter_label':'原始HTTP控制' if row['adapter']=='service' else 'SDK控制'} for row in lifecycle]
    table=markdown_table(life_table,[('stage','阶段'),('adapter_label','适配器'),('n','样本数'),('p50_ms','P50 ms'),('p95_ms','P95 ms'),('max_ms','最大 ms')])
    runtable=markdown_table(life_runs,[('adapter','适配器'),('repeat','重复'),('measurement_window_ms','整窗口 ms'),('cycle_elapsed_p50_ms','一轮P50 ms'),('non_stage_window_ms','阶段外间隔 ms')])
    life_text=f'''# 内存状态保留的暂停/恢复结果

原始HTTP控制和公开SDK控制各完成3次运行、每次20轮暂停/恢复，共120轮。360个测量阶段全部成功，内存/文件状态校验全部通过，6个sandbox lineage最终均清理完成。两种方式的典型暂停/恢复延迟接近；当前样本不支持将描述性差值当作确定的SDK额外成本。

{table}

![暂停恢复阶段对比](results/lifecycle/plots/lifecycle-stages.png)

[可导出SVG](results/lifecycle/plots/lifecycle-stages.svg) · [JSON](results/lifecycle/summary.json) · [阶段CSV](results/lifecycle/stage_summary.csv) · [逐轮CSV](results/lifecycle/cycles.csv) · [配对运行中位数](results/lifecycle/paired_run_medians.csv)

## 什么被测量和验证

输入只包括`lifecycle-service-r1..3.jsonl`和`lifecycle-sdk-r1..3.jsonl`。前者对暂停发原始HTTP `POST /sandboxes/{{id}}/pause {{memory:true}}`，对恢复发`POST /sandboxes/{{id}}/connect {{timeout:600}}`；60次响应分别为204/201，排除原本已运行的200。后者使用public SDK pause/connect。**第三阶段双方都通过SDK启动Python命令并查询sandbox内常驻进程，因此包括进程启动、loopback HTTP和状态返回，不是envd初始化本身的纯耗时。**

每轮在暂停前更新文件marker，恢复后核验进程内生成、未落盘的随机token、递增counter、PID、guest boot ID和文件marker。记录中120个probe均有`memory_preserved=true`、`file_preserved=true`；每条lineage的token hash和PID稳定，counter顺序为2–21，120次paused listing也全部通过。boot ID在执行器中参与校验，没有把原始boot ID另行写入结果，故离线审计依据其state-check标志和冻结执行源码。失败预检与旧smoke不在输入集合中。

## 时间窗口与限制

fixture为`base-4k-4g`、2vCPU/4096MiB、普通4KiB页、单实例单并发；runtime和e2b版本与SDK应用场景一致。每次运行在同一个sandbox上连续恢复20轮，因此每adapter虽有60轮样本，但只有3条独立lineage，不能视为60个独立环境重复。

{runtable}

单阶段计时排除marker更新、paused listing及最终清理；整窗口包含轮间准备/校验间隔。这里报告延迟和窗口耗时，不将阶段计数/整窗口称为服务吞吐。表中P50/P95是每adapter/stage合并60个观测的描述性分位数，区别于主矩阵的逐run汇总。图下方的Run pair仅将相同repeat编号配在一起，使用的是不同sandbox lineage，不是同一sandbox内A/B交叉。配对运行在时间上顺序执行，没有随机化或CPU/网络开销的逐调用因果分解，因此不能把SDK与HTTP差值直接称作SDK overhead。

原始HTTP pause有最大{next(row['max_ms'] for row in lifecycle if row['adapter']=='service' and row['operation']=='pause'):.2f} ms的慢样本，SDK pause最大{next(row['max_ms'] for row in lifecycle if row['adapter']=='sdk' and row['operation']=='pause'):.2f} ms；均保留在分布与CSV。P95为描述性统计，n=60的P99不用于稳定尾延迟承诺。图下半部显示3组运行中位数，避免只凭合并平均值解释差异。

恢复后状态探针占据每轮的大段等待，下一步应以阶段trace或固定轻量探针对照分解其首次命令/页面就绪成本，不能直接宣称控制API是唯一瓶颈。当前结果证明本fixture的状态保留和这120轮生命周期可用性，不代表重负载内存快照性能。

## 冻结材料与复现

[输入冻结manifest](../phase2-sdk/results/application/INPUTS-FROZEN.json)覆盖本报告六个运行和SDK场景三个运行，保存原始路径、SHA、只读副本及执行源码。分析仅使用冻结输入；源码为[analyze_application.py](../phase2-sdk/code/analyze_application.py)，运行固定在CPU16–31，不修改共享reporter或服务。
'''
    (ROOT/'phase1-service/LIFECYCLE-RESULTS.md').write_text(life_text)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',action='store_true',help='Copy the nine explicitly selected inputs once')
    args=parser.parse_args(argv)
    document=freeze_inputs() if args.freeze else load_frozen()
    runs,operations,rounds,quality=audit_and_load(document)
    summaries=stage_summaries(operations)
    reports(runs,operations,rounds,summaries,quality)
    figures(operations)
    provenance={'analysis_script':str(Path(__file__).resolve()),'analysis_sha256':sha(__file__),
        'python':sys.version,'matplotlib':importlib.metadata.version('matplotlib'),
        'cpu_affinity':sorted(os.sched_getaffinity(0)),
        'input_manifest_sha256':sha(APP/'INPUTS-FROZEN.json'),
        'quantile_method':'linear interpolation at (n-1)*p; pooled samples, never averaged quantiles',
        'service_requests':0,'input_runs':9,'operation_count':len(operations)}
    write_json(APP/'ANALYSIS-PROVENANCE.json',provenance)
    write_json(LIFE/'ANALYSIS-PROVENANCE.json',provenance)
    print(json.dumps({'status':'written','runs':len(runs),'operations':len(operations),
                      'application':str(APP),'lifecycle':str(LIFE),'all_audits_passed':True},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
