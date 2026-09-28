"""Local-only, hash-frozen comparison of 1xC64 and 4xC16 generators.

Common-window performance and the complete measurement-dispatch error cohort
are deliberately separate. First-call membership is computed before applying
any common-window filter, by (generator_index, worker_id).
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
import statistics
import sys


ROOT = Path(__file__).resolve().parents[2]
RAW = Path('/data2/yuhanya/e2b-benchmark/session-20260920')
OUT = ROOT/'phase1-service/results/multigenerator'
SDK_OUT = ROOT/'phase2-sdk/results/multigenerator'
sys.path.insert(0, str(ROOT/'analysis'))
import resources as resource_analysis


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1<<20),b''):digest.update(chunk)
    return digest.hexdigest()


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')


def csv_save(path,rows):
    if not rows:return
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def entry(path):
    path=Path(path)
    return {'path':str(path),'sha256':sha(path),'size_bytes':path.stat().st_size}


def freeze():
    path=OUT/'INPUTS-FROZEN.json'
    if path.exists():return json.loads(path.read_text())
    datasets=[]
    # Nothing is frozen until all six coordinators have written final status.
    for adapter,phase in [('service','phase1-service'),('sdk','phase2-sdk')]:
        for repeat in range(1,4):
            folder=RAW/phase/f'multigen-{adapter}-r{repeat}'
            final=json.loads((folder/'cohort.json').read_text())
            if final.get('status') not in ('passed','failed') or 'worker_exit_codes' not in final:
                raise ValueError(f'cohort not final: {folder}')
            if final['status']!='passed' or not final.get('cleanup_ok') or not final.get('common_window',{}).get('valid'):
                raise ValueError(f'cohort execution invalid; preserve separately: {folder}')
            datasets.append({'adapter':adapter,'repeat':repeat,'generator_count':4,
                'dataset_id':f'multigen-{adapter}-r{repeat}',
                'cohort':entry(folder/'cohort.json'),
                'workers':[entry(folder/f'worker-{i}.jsonl') for i in range(4)],
                'resources':entry(folder/'resources.jsonl')})
    for adapter,phase in [('service','phase1-service'),('sdk','phase2-sdk')]:
        for repeat in range(1,4):
            path=RAW/phase/f'exec_true-c64-r{repeat}-{adapter}.jsonl'
            datasets.append({'adapter':adapter,'repeat':repeat,'generator_count':1,
                'dataset_id':f'single-{adapter}-r{repeat}',
                'workers':[entry(path)],'resources':entry(path.with_suffix('.resources.jsonl')),
                'execution_manifest':entry(path.with_suffix('.manifest.json'))})
    sources=[]
    for relative in ['phase1-service/code/multigenerator.py','phase1-service/code/runner.py',
                     'phase1-service/code/executed/runner-20260920-v1.py',
                     'analysis/resources.py','analysis/summarize.py','phase0-environment/sample_resources.py']:
        sources.append(entry(ROOT/relative))
    document={'schema_version':1,'frozen_utc':datetime.now(timezone.utc).isoformat(),
        'freeze_mode':'exact finalized paths plus SHA-256; raw JSONL remains in /data2 and is hash-verified while reading',
        'multi_cohorts':6,'single_generator_comparison_runs':6,
        'selection':'Only multigen-{service|sdk}-r1..3 and main exec_true-c64-r1..3-{service|sdk}; excludes later idle-pool and other diagnostics',
        'datasets':datasets,'source_files':sources}
    save(OUT/'INPUTS-FROZEN.json',document)
    return document


def checked_lines(info):
    digest=hashlib.sha256()
    with Path(info['path']).open('rb') as handle:
        for line in handle:
            digest.update(line)
            if line.strip():yield json.loads(line)
    if digest.hexdigest()!=info['sha256']:
        raise ValueError('Input changed after freeze: '+info['path'])


def checked_json(info):
    content=Path(info['path']).read_bytes()
    if hashlib.sha256(content).hexdigest()!=info['sha256']:
        raise ValueError('Input changed after freeze: '+info['path'])
    return json.loads(content)


def quantile(values,p):
    if not values:return None
    values=sorted(values);at=(len(values)-1)*p;lo=int(at);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(at-lo)


def success(row):
    return row['status']=='success' and row.get('state_check') is not False


def read_worker(info,generator_index):
    rows=[];finals=[];metadata=None
    for record in checked_lines(info):
        if record.get('record_type')=='metadata':metadata=record
        if record.get('record_type')=='run':finals.append(record)
        if record.get('record_type')!='operation' or record.get('window')!='measurement':continue
        if record.get('operation')!='exec_true':raise ValueError('Unexpected operation')
        row={key:record.get(key) for key in ['run_id','operation_id','worker_id','status','state_check',
            'duration_ms','start_offset_s','end_offset_s','error_class','rpc_code','http_status']}
        row['generator_index']=generator_index
        if not math.isfinite(row['duration_ms']) or row['duration_ms']<0:raise ValueError('Invalid duration')
        if abs((row['end_offset_s']-row['start_offset_s'])*1000-row['duration_ms'])>1:raise ValueError('Duration mismatch')
        rows.append(row)
    if len(finals)!=1 or metadata is None:raise ValueError('Missing/duplicate final run or metadata')
    final=finals[0]
    if not final.get('cleanup_ok') or final.get('stop_reason')!='completed':raise ValueError('Incomplete worker')
    if final['completed_count']!=len(rows) or final['dispatched_count']!=len(rows):raise ValueError('Worker terminal count mismatch')
    if final['successful_count']!=sum(success(row) for row in rows):raise ValueError('Worker success count mismatch')
    if len({row['operation_id'] for row in rows})!=len(rows):raise ValueError('Duplicate operation ID')
    if final['resident_count']!=final['concurrency'] or final['memory_mib']!=4096:raise ValueError('Fixture mismatch')
    if final.get('template')!='base-4k-4g' or final.get('mode')!='closed_loop':raise ValueError('Fixture/mode mismatch')
    for row in rows:
        if row['run_id']!=final['run_id']:raise ValueError('Row identity mismatch')
    return rows,final,metadata


def resource_summary(dataset,start_utc,end_utc,pids):
    info=dataset['resources']
    if sha(info['path'])!=info['sha256']:raise ValueError('Resource input changed')
    samples,issues,_=resource_analysis.read_samples([Path(info['path'])])
    receipt_deltas=[]
    for record in checked_lines(info):
        if record.get('record_type')=='sample':
            receipt_deltas.append(resource_analysis.utc_seconds(record['received_utc'])
                                  -resource_analysis.utc_seconds(record['guest']['utc']))
    summary={'receipt_minus_guest_utc_min_s':min(receipt_deltas),
             'receipt_minus_guest_utc_median_s':statistics.median(receipt_deltas),
             'receipt_minus_guest_utc_max_s':max(receipt_deltas),
             'vm_window_alignment':'uncalibrated UTC; receipt delta also includes transport/collection delay'}
    issues.append({'code':'cross_machine_clock_alignment_unresolved',
                   'receipt_minus_guest_utc_median_s':statistics.median(receipt_deltas),
                   'message':'Do not treat receipt-minus-guest time as pure clock offset or network latency. VM interval/window attribution is approximate; host generator PIDs share the host time domain.'})
    trace=[]
    for layer in ['host','guest']:
        all_layer=[row for row in samples if row['layer']==layer]
        overlapping=[row for row in all_layer if row['end']>start_utc and row['start']<end_utc]
        contained=[row for row in overlapping if row['start']>=start_utc-1e-6 and row['end']<=end_utc+1e-6]
        seconds=sum(row['interval_seconds'] for row in contained)
        summary[layer+'_resource_samples']=len(contained)
        summary[layer+'_resource_covered_s']=seconds
        summary[layer+'_resource_coverage_pct']=100*seconds/(end_utc-start_utc)
        summary[layer+'_excluded_boundary_samples']=len(overlapping)-len(contained)
        if not contained:raise ValueError('No contained resource samples for '+layer)
        if layer=='host':
            valid=[]
            for row in contained:
                data=row['data'];selected=data.get('selected_processes',{})
                if not all(selected.get(str(pid),{}).get('continuous_identity') is True for pid in pids):continue
                cores=sum(selected[str(pid)]['cpu_cores'] for pid in pids)
                valid.append((cores,row['interval_seconds']))
                trace.append({'dataset_id':dataset['dataset_id'],'layer':'host_generators','sample_end_utc':resource_analysis.iso_utc(row['end']),
                              'interval_seconds':row['interval_seconds'],'cpu_cores':cores,'selected_pids':','.join(map(str,pids))})
            covered=sum(dt for _,dt in valid)
            summary.update(generator_cpu_mean_cores=sum(value*dt for value,dt in valid)/covered if covered else None,
                generator_cpu_peak_cores=max((value for value,_ in valid),default=None),
                generator_pid_count=len(pids),generator_cpu_covered_s=covered,
                generator_missing_identity_intervals=len(contained)-len(valid))
        else:
            summary.update(vm_cpu_mean_cores=sum(row['data']['cpu']['busy_core_equivalents']*row['interval_seconds'] for row in contained)/seconds,
                vm_cpu_peak_cores=max(row['data']['cpu']['busy_core_equivalents'] for row in contained),
                vm_cpu_mean_pct=sum(row['data']['cpu']['busy_pct']*row['interval_seconds'] for row in contained)/seconds,
                vm_cpu_psi_some_avg10_max=max(row['data'].get('pressure',{}).get('cpu',{}).get('some',{}).get('avg10',0) for row in contained))
            for row in contained:
                trace.append({'dataset_id':dataset['dataset_id'],'layer':'guest_vm','sample_end_utc':resource_analysis.iso_utc(row['end']),
                              'interval_seconds':row['interval_seconds'],'cpu_cores':row['data']['cpu']['busy_core_equivalents'],'selected_pids':''})
    return summary,trace,issues


def analyze_dataset(dataset):
    all_rows=[];runs=[];metadata=[]
    for index,info in enumerate(dataset['workers']):
        rows,final,meta=read_worker(info,index);all_rows.extend(rows);runs.append(final);metadata.append(meta)
    count=dataset['generator_count']
    if sum(run['concurrency'] for run in runs)!=64 or sum(run['resident_count'] for run in runs)!=64:
        raise ValueError('Comparison is not global C64/N64')
    if count==4:
        cohort=checked_json(dataset['cohort'])
        origins=[run['generator_origin_monotonic_s'] for run in runs]
        starts=[origin+run['measurement_start_offset_s'] for origin,run in zip(origins,runs)]
        ends=[origin+run['measurement_end_offset_s'] for origin,run in zip(origins,runs)]
        start,end=max(starts),min(ends)
        for row in all_rows:
            row['absolute_start']=origins[row['generator_index']]+row['start_offset_s']
            row['absolute_end']=origins[row['generator_index']]+row['end_offset_s']
        utc_offsets=[resource_analysis.utc_seconds(run['run_started_utc'])-origin for run,origin in zip(runs,origins)]
        utc_offset=statistics.median(utc_offsets)
        start_utc,end_utc=start+utc_offset,end+utc_offset
        pids=cohort['resources']['host_pids']
        if len(pids)!=4:raise ValueError('Expected four sampled worker PIDs')
        if set(pids)!={row['pid'] for row in metadata}:
            raise ValueError('Sampled PIDs differ from the four worker process identities')
        if len({run['frozen_runner_sha256'] for run in runs})!=1:raise ValueError('Mixed worker source revisions')
        source_hash=runs[0]['frozen_runner_sha256']
        cohort_id=cohort['cohort_id']
    else:
        run=runs[0];start,end=run['measurement_start_offset_s'],run['measurement_end_offset_s']
        for row in all_rows:row['absolute_start']=row['start_offset_s'];row['absolute_end']=row['end_offset_s']
        start_utc=resource_analysis.utc_seconds(run['run_started_utc'])+start
        end_utc=resource_analysis.utc_seconds(run['run_started_utc'])+end
        pids=[metadata[0]['pid']]
        source_hash=checked_json(dataset['execution_manifest'])['runner_sha256']
        cohort_id=None;starts=[start];utc_offsets=[0]
    window=end-start
    inside=[row for row in all_rows if row['absolute_start']>=start and row['absolute_end']<=end]
    common_success=[row for row in inside if success(row)]
    first={}
    for row in all_rows:
        key=(row['generator_index'],row['worker_id'])
        if key not in first or (row['absolute_start'],row['operation_id'])<(first[key]['absolute_start'],first[key]['operation_id']):first[key]=row
    if len(first)!=64:raise ValueError('First-measured cohort does not have 64 worker slots')
    first_ids={row['operation_id'] for row in first.values()}
    first_rows=[];failures=[]
    for row in all_rows:
        initial=row['operation_id'] in first_ids
        contained=row['absolute_start']>=start and row['absolute_end']<=end
        row['first_measured']=initial;row['inside_common_window']=contained
        compact={'dataset_id':dataset['dataset_id'],'adapter':dataset['adapter'],'generator_count':count,'repeat':dataset['repeat'],
            'generator_index':row['generator_index'],'worker_id':row['worker_id'],'operation_id':row['operation_id'],
            'first_measured':initial,'inside_common_window':contained,'status':row['status'],'duration_ms':row['duration_ms'],
            'error_class':row['error_class'],'rpc_code':row['rpc_code'],'http_status':row['http_status']}
        if initial:first_rows.append(compact)
        if not success(row):failures.append(compact)
    total_fail=len(failures);first_fail=sum(not success(row) for row in first.values())
    common_fail=len(inside)-len(common_success);common_ids={row['operation_id'] for row in inside}
    summary={'dataset_id':dataset['dataset_id'],'adapter':dataset['adapter'],'repeat':dataset['repeat'],
        'generator_count':count,'total_concurrency':64,'total_resident_count':64,
        'per_generator_concurrency':64//count,'per_generator_residents':64//count,
        'window_seconds':window,'window_start_utc':resource_analysis.iso_utc(start_utc),
        'window_end_utc':resource_analysis.iso_utc(end_utc),
        'common_completed_count':len(inside),'common_success_count':len(common_success),'common_failure_count':common_fail,
        'common_successful_ops_s':len(common_success)/window,
        'common_p50_ms':quantile([row['duration_ms'] for row in common_success],.5),
        'common_p95_ms':quantile([row['duration_ms'] for row in common_success],.95),
        'common_p99_ms':quantile([row['duration_ms'] for row in common_success],.99),
        'all_measurement_count':len(all_rows),'all_measurement_success_count':len(all_rows)-total_fail,
        'all_measurement_failure_count':total_fail,'all_measurement_failure_pct':100*total_fail/len(all_rows),
        'boundary_excluded_count':len(all_rows)-len(inside),'boundary_excluded_failure_count':total_fail-common_fail,
        'first_measurement_count':64,'first_measurement_failure_count':first_fail,'first_measurement_failure_pct':100*first_fail/64,
        'later_measurement_count':len(all_rows)-64,'later_measurement_failure_count':total_fail-first_fail,
        'later_measurement_failure_pct':100*(total_fail-first_fail)/(len(all_rows)-64),
        'first_p50_ms':quantile([row['duration_ms'] for row in first.values() if success(row)],.5),
        'first_p95_ms':quantile([row['duration_ms'] for row in first.values() if success(row)],.95),
        'later_p95_ms':quantile([row['duration_ms'] for row in all_rows if row['operation_id'] not in first_ids and success(row)],.95),
        'all_dispatch_p95_ms':quantile([row['duration_ms'] for row in all_rows if success(row)],.95),
        'start_spread_ms':1000*(max(starts)-min(starts)),
        'worker_wall_monotonic_offset_spread_ms':1000*(max(utc_offsets)-min(utc_offsets)),
        'cleanup_ok':all(run['cleanup_ok'] for run in runs),'frozen_runner_sha256':source_hash,
        'cohort_id':cohort_id,'initial_warmed_residents':count,
        'state_check_false_count':sum(row['state_check'] is False for row in all_rows),
    }
    if count==4:
        reported=cohort['common_window']
        for key,actual in [('completed_count',len(inside)),('successful_count',len(common_success)),('boundary_excluded_operations',len(all_rows)-len(inside))]:
            if reported[key]!=actual:raise ValueError('Cohort common-window counts mismatch: '+key)
        if sum(reported['status_counts'].values())!=len(inside):raise ValueError('Cohort status sum mismatch')
    resources,trace,issues=resource_summary(dataset,start_utc,end_utc,pids)
    summary.update(resources)
    summary['resource_quality_issue_count']=len(issues)
    return summary,first_rows,failures,trace,issues


def configuration_summaries(rows):
    groups=defaultdict(list)
    for row in rows:groups[(row['adapter'],row['generator_count'])].append(row)
    output=[]
    for (adapter,count),group in sorted(groups.items()):
        if len(group)!=3:raise ValueError('Expected three repetitions per configuration')
        first_total=sum(row['first_measurement_count'] for row in group)
        first_fail=sum(row['first_measurement_failure_count'] for row in group)
        later_total=sum(row['later_measurement_count'] for row in group)
        later_fail=sum(row['later_measurement_failure_count'] for row in group)
        item={'adapter':adapter,'generator_count':count,'repetitions':3,
            'total_measurement_count':sum(row['all_measurement_count'] for row in group),
            'all_failure_count':sum(row['all_measurement_failure_count'] for row in group),
            'common_failure_count':sum(row['common_failure_count'] for row in group),
            'excluded_failure_count':sum(row['boundary_excluded_failure_count'] for row in group),
            'first_count':first_total,'first_failure_count':first_fail,'first_failure_pct':100*first_fail/first_total,
            'later_count':later_total,'later_failure_count':later_fail,'later_failure_pct':100*later_fail/later_total}
        for metric in ['common_successful_ops_s','common_p50_ms','common_p95_ms','common_p99_ms',
                       'generator_cpu_mean_cores','vm_cpu_mean_cores']:
            values=[row[metric] for row in group if row[metric] is not None]
            item[metric+'_median']=statistics.median(values)
            item[metric+'_min']=min(values);item[metric+'_max']=max(values)
        output.append(item)
    return output


def plots(rows,configs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.hashsalt':'multigenerator-20260920'})
    colors=['#2563eb','#d97706','#15803d']
    def savefig(fig,stem):
        path=OUT/'plots'/stem;path.parent.mkdir(parents=True,exist_ok=True)
        fig.savefig(path.with_suffix('.png'),dpi=190,bbox_inches='tight',metadata={'Software':'E2B local analysis'})
        fig.savefig(path.with_suffix('.svg'),bbox_inches='tight',metadata={'Date':None});plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(11,7))
    for col,adapter in enumerate(['service','sdk']):
        for rowidx,(metric,label) in enumerate([('common_successful_ops_s','Successful operations/s'),('common_p95_ms','P95 latency (ms)')]):
            ax=axes[rowidx,col]
            for count,x in [(1,1),(4,2)]:
                values=[r[metric] for r in rows if r['adapter']==adapter and r['generator_count']==count]
                ax.hlines(statistics.median(values),x-.22,x+.22,color='#111827',linewidth=2)
                for repeat,value in enumerate(values,1):ax.scatter([x+(repeat-2)*.045],[value],s=45,color=colors[repeat-1],zorder=3,label=f'R{repeat}' if count==1 else None)
            ax.set_xticks([1,2],['1 process\nC64/N64','4 processes\n4 × C16/N16']);ax.set_xlim(.6,2.4)
            maximum=max(r[metric] for r in rows if r['adapter']==adapter)
            ax.set_ylim(0,maximum*1.15);ax.set_ylabel(label);ax.grid(axis='y',alpha=.2)
            ax.set_title(('Raw HTTPX / Connect' if adapter=='service' else 'Public SDK')+' · global C64/N64')
            if rowidx==0:ax.legend(fontsize=8)
    fig.suptitle('Generator-count sensitivity · 3 repetitions · common-window performance')
    fig.text(.5,.01,'Horizontal bars are medians across runs. Process count also changes pools and initial warmup coverage; this is not a sole-bottleneck proof.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.04,1,.94));savefig(fig,'performance-comparison')

    fig,axes=plt.subplots(1,3,figsize=(15,4.5))
    labels=[f"{row['adapter']}\n{row['generator_count']} proc" for row in configs]
    for ax,field,num,den,title in [(axes[0],'first_failure_pct','first_failure_count','first_count','First measured request per worker slot'),(axes[1],'later_failure_pct','later_failure_count','later_count','All later measurement requests')]:
        values=[row[field] for row in configs];ax.bar(range(4),values,color=['#93c5fd','#2563eb','#fdba74','#d97706'])
        limit=max(.01,max(values)*1.45);ax.set_ylim(0,limit)
        for index,row in enumerate(configs):ax.text(index,values[index]+limit*.025,f"{row[num]}/{row[den]:,}",ha='center',fontsize=8)
        ax.set_xticks(range(4),labels);ax.set_ylabel('Failure rate (%)');ax.set_title(title,fontsize=10);ax.grid(axis='y',alpha=.2)
    multi=[row for row in rows if row['generator_count']==4]
    for index,row in enumerate(multi):
        axes[2].bar(index-.17,row['common_failure_count'],width=.34,color='#94a3b8',label='Inside common window' if index==0 else None)
        axes[2].bar(index+.17,row['all_measurement_failure_count'],width=.34,color='#dc2626',label='All measurement dispatches' if index==0 else None)
    axes[2].set_xticks(range(6),[f"{row['adapter']}\nR{row['repeat']}" for row in multi]);axes[2].set_ylabel('Failure count');axes[2].set_title('Boundary-excluded failures remain disclosed',fontsize=10);axes[2].legend(fontsize=8);axes[2].grid(axis='y',alpha=.2)
    fig.suptitle('Failure accounting includes the complete first-request cohort: 64 slots per run/cohort')
    fig.tight_layout(rect=(0,0,1,.91));savefig(fig,'failure-accounting')

    fig,axes=plt.subplots(2,2,figsize=(11,7))
    for col,adapter in enumerate(['service','sdk']):
        for rowidx,(metric,title) in enumerate([('generator_cpu_mean_cores','Summed generator process CPU'),('vm_cpu_mean_cores','Whole VM CPU (uncalibrated UTC)')]):
            ax=axes[rowidx,col]
            for count,x in [(1,1),(4,2)]:
                values=[r[metric] for r in rows if r['adapter']==adapter and r['generator_count']==count]
                ax.hlines(statistics.median(values),x-.22,x+.22,color='#111827',linewidth=2)
                for repeat,value in enumerate(values,1):ax.scatter([x+(repeat-2)*.04],[value],s=45,color=colors[repeat-1])
            ax.set_xticks([1,2],['1 process','4 processes']);ax.set_xlim(.6,2.4)
            ax.set_ylim(0,max(r[metric] for r in rows if r['adapter']==adapter)*1.15)
            ax.set_ylabel('Time-weighted CPU cores');ax.set_title(adapter+' · '+title,fontsize=10);ax.grid(axis='y',alpha=.2)
    fig.suptitle('Resource estimates in contained intervals · cross-machine clocks are uncalibrated')
    fig.text(.5,.01,'Client CPU sums the selected worker PIDs in host time. VM window attribution is approximate; receipt time also includes transport/collection delay.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.04,1,.94));savefig(fig,'cpu-comparison')


def markdown(rows,configs,adapter):
    phase='phase1-service' if adapter=='service' else 'phase2-sdk'
    relative='results/multigenerator' if adapter=='service' else '../phase1-service/results/multigenerator'
    one=next(row for row in configs if row['adapter']==adapter and row['generator_count']==1)
    four=next(row for row in configs if row['adapter']==adapter and row['generator_count']==4)
    factor=four['common_successful_ops_s_median']/one['common_successful_ops_s_median']
    table=['| 进程数 | 重复 | 共同窗口成功ops/s | P50 ms | P95 ms | 全部失败/总数 | first失败/64 | later失败 | 边界排除失败 |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['adapter']!=adapter:continue
        table.append(f"| {row['generator_count']} | {row['repeat']} | {row['common_successful_ops_s']:.2f} | {row['common_p50_ms']:.2f} | {row['common_p95_ms']:.2f} | {row['all_measurement_failure_count']}/{row['all_measurement_count']} | {row['first_measurement_failure_count']}/64 | {row['later_measurement_failure_count']} | {row['boundary_excluded_failure_count']} |")
    cpu=['| 进程数 | 重复 | generator合计平均核 | VM平均核（UTC近似） | host覆盖% | VM覆盖% | receipt−guest中位秒 |', '|---|---|---:|---:|---:|---:|---:|']
    for row in rows:
        if row['adapter']==adapter:cpu.append(f"| {row['generator_count']} | {row['repeat']} | {row['generator_cpu_mean_cores']:.2f} | {row['vm_cpu_mean_cores']:.2f} | {row['host_resource_coverage_pct']:.1f} | {row['guest_resource_coverage_pct']:.1f} | {row['receipt_minus_guest_utc_median_s']:.3f} |")
    transport='原始HTTPX/Connect' if adapter=='service' else '公开SDK'
    text=f'''# 多进程压测端对照：{transport}

总并发和存量实例均固定为64。将一个C64/N64进程拆为四个C16/N16进程后，3次运行的共同窗口成功吞吐中位数从 **{one['common_successful_ops_s_median']:.2f}** 变为 **{four['common_successful_ops_s_median']:.2f} ops/s**，比值 **{factor:.2f}**。这是压测端进程/transport连接池组织对结果的影响，不足以证明某一个组件是唯一瓶颈。

{chr(10).join(table)}

![进程数对吞吐和P95的影响]({relative}/plots/performance-comparison.png)

## 边界和失败统计

共同窗口性能严格使用四个worker测量窗口的交集，并且操作的start/end都必须落在交集内；单进程对照使用自身30秒窗口和同样的包含规则。这里的P95是start/end均落在窗口内的条件分布；完整measurement dispatch cohort（含drain）的P95另列在CSV/JSON的`all_dispatch_p95_ms`，不能直接与主报告含drain的P95混用。最终drain、开始边界之前的少量请求不用于共同窗口速率/延迟，但**所有标记measurement的worker操作仍进入失败审计**。两种计数有意并存，不将共同窗口失败数冒充完整失败数。

first定义为每个`(generator_index, worker_id)`按开始时间排序的第一条measurement请求，先在完整输入上识别，再检查其是否落入共同窗口；每个run/cohort固定64个first，三次共192个。later为其余所有measurement请求，包含边界/drain。四进程本适配器共 **{four['all_failure_count']}个失败**，其中first **{four['first_failure_count']}/{four['first_count']}**，later **{four['later_failure_count']}/{four['later_count']}**；共同窗口外仍有 **{four['excluded_failure_count']}个失败**明确保留。

SDK四进程R1尤其需要区分：cohort原摘要共同窗口失败8个，而所有worker measurement操作失败9个；本报告以完整9个进行first/later和总体失败分析。协调器`status=passed`表示执行/窗口/清理条件满足，不代表操作零错误。

![首请求、后续请求及边界失败]({relative}/plots/failure-accounting.png)

## 压测端和VM CPU

{chr(10).join(cpu)}

四进程CPU在每个host采样间隔对四个worker PID求和，不使用manager PID替代。只采用完整落在测量窗口内的采样间隔，按实际间隔长度加权；没有把边界间隔的平均值拆分或把缺失进程视为零。host生成器计数与测量窗口处于同一主机时钟域。

**VM CPU目前按未校准的guest UTC窗口做近似归属。** 六个多进程capture中`received_utc−guest.utc`的下界约1.168–1.176秒、中位约1.192–1.213秒，提示不能默认跨机UTC已精确对齐；这个差还包含SSH传输/采集延迟，不能直接当作纯时钟offset或网络耗时。本报告保留未做偏移修正的VM均值及边界覆盖率，不能据此做亚秒级因果分解；host侧4PID合计不受这个跨机问题影响。覆盖率和排除的边界样本均在CSV/JSON中保留，CPU核数不等于整机百分比。

时钟核验和独立敏感性分析见[CLOCK-ALIGNMENT.md](../phase0-environment/CLOCK-ALIGNMENT.md)。当前表保留未修正值，不推定事后测得的offset在所有历史窗口恒定，也不支持精确的跨层开销归因。

![压测端与VM CPU]({relative}/plots/cpu-comparison.png)

## 控制条件和结论边界

基线与补充均为`base-4k-4g`、2vCPU/4GiB、普通4KiB页、`exec_true`、global C64/N64、30秒closed-loop，3个重复。四进程串行完成准备，再由barrier对齐测量；各进程只清理自己的run，cohort cleanup均通过。没有把后续idle-pool实验的输入或资源文件加入本集合。

拆分进程同时改变了Python解释器/GIL、HTTP连接池、SDK进程内共享transport及初始warmup覆盖。父runner只预热一个resident，因此单进程预热1个，四进程合计预热4个；其余first请求仍可能包含首次连接/首次使用效应。四进程prepare还各有一次模板检查。这是对压测器headroom的实验，不是仅改变CPU核数的理想单变量实验。

报告保留逐重复变动，不把3个重复当作稳定上限或显著性检验。P50/P95来自每次运行的共同窗口成功样本；配置级中位数是这3个逐run指标的中位数，没有平均分位数。first失败率的分母与总体/later不同，不能只看被大量后续成功请求稀释的总体错误率。服务/SDK对应源码hash和v1/v2差异保存在冻结输入/执行manifest中；本分析不修改benchmark或daemon。

[汇总JSON]({relative}/summary.json) · [逐运行CSV]({relative}/per_run.csv) · [first 768条CSV]({relative}/first_requests.csv) · [全部失败CSV]({relative}/failures.csv) · [资源CSV]({relative}/resource_samples.csv) · [输入SHA冻结清单]({relative}/INPUTS-FROZEN.json)

## 复现

```bash
taskset -c 16-31 /data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \\
  /home/yuhanya/Daily_LLM/sandbox/benchmark/phase1-service/code/analyze_multigenerator.py
```

所有输入已完成后才按精确路径和SHA冻结，原始大JSONL保留在`/data2`；分析流式验hash，不复制大数据进文档仓库。PNG有对应SVG可导出。分析使用共享`analysis/resources.py`的解析规则，但没有修改共享reporter或执行任何服务请求。
'''
    (ROOT/phase/'MULTIGENERATOR-RESULTS.md').write_text(text)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--freeze',action='store_true');args=parser.parse_args(argv)
    document=freeze() if args.freeze else json.loads((OUT/'INPUTS-FROZEN.json').read_text())
    for info in document['source_files']:
        if sha(info['path'])!=info['sha256']:raise ValueError('Analysis/source dependency changed after freeze: '+info['path'])
    rows=[];first=[];failures=[];trace=[];quality=[]
    for dataset in document['datasets']:
        row,initial,failed,resource_rows,issues=analyze_dataset(dataset)
        rows.append(row);first.extend(initial);failures.extend(failed);trace.extend(resource_rows)
        quality.extend({'dataset_id':dataset['dataset_id'],**issue} for issue in issues)
    rows.sort(key=lambda row:(row['adapter'],row['generator_count'],row['repeat']))
    configs=configuration_summaries(rows)
    if len(first)!=768:raise ValueError('Expected 12 × 64 first-measured slots')
    sdk1=next(row for row in rows if row['dataset_id']=='multigen-sdk-r1')
    if sdk1['common_failure_count']!=8 or sdk1['all_measurement_failure_count']!=9:
        raise ValueError('Known SDK R1 boundary failure regression')
    summary={'schema_version':1,'global_concurrency':64,'global_residents':64,'configurations':configs,'runs':rows,
        'failure_accounting':{'first_key':['generator_index','worker_id'],'first_denominator_per_run_or_cohort':64,
                             'first_rows_total':len(first),'all_measurement_failures_including_boundaries':len(failures)},
        'performance_scope':'common-window contained operations; all measurement errors retained separately',
        'resource_scope':'fully contained intervals only; time-weighted sum of all selected worker PID cores in host time; VM UTC-window attribution is approximate because cross-machine clock offset is unresolved',
        'interpretation':'Generator/transport/pool/warmup sensitivity, not proof of a sole bottleneck'}
    save(OUT/'summary.json',summary);csv_save(OUT/'per_run.csv',rows);csv_save(OUT/'configuration_summary.csv',configs)
    csv_save(OUT/'first_requests.csv',first);csv_save(OUT/'failures.csv',failures);save(OUT/'failures.json',failures)
    csv_save(OUT/'resource_samples.csv',trace);save(OUT/'resource_quality.json',quality)
    plots(rows,configs)
    for adapter in ['service','sdk']:markdown(rows,configs,adapter)
    SDK_OUT.mkdir(parents=True,exist_ok=True)
    save(SDK_OUT/'summary.json',{'schema_version':1,'canonical_summary':str(OUT/'summary.json'),
         'configurations':[r for r in configs if r['adapter']=='sdk'],'runs':[r for r in rows if r['adapter']=='sdk']})
    csv_save(SDK_OUT/'per_run.csv',[r for r in rows if r['adapter']=='sdk'])
    provenance={'analysis_path':str(Path(__file__).resolve()),'analysis_sha256':sha(__file__),
        'input_manifest_sha256':sha(OUT/'INPUTS-FROZEN.json'),'python':sys.version,
        'matplotlib':importlib.metadata.version('matplotlib'),'cpu_affinity':sorted(os.sched_getaffinity(0)),
        'quantile_method':'linear at (n-1)*p; config medians over three run metrics',
        'live_service_requests':0,'dataset_count':12}
    save(OUT/'ANALYSIS-PROVENANCE.json',provenance)
    print(json.dumps({'status':'written','datasets':12,'first_requests':len(first),'all_failures':len(failures),'output':str(OUT)}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
