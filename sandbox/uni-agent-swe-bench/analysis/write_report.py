"""Compose bilingual reports and an offline eight-sample explorer."""
import html
import hashlib
import importlib.metadata
import json
import re
import shutil
import sys
from pathlib import Path

import markdown
import numpy as np
import pandas as pd

OUT=Path(__file__).resolve().parents[1]


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+
                     ['| '+' | '.join(str(x) for x in row)+' |' for row in rows])


def fig(name,caption):
    return f'![{caption}](figures/{name}.png)\n\n*{caption}*\n'


def number(x,d=2):return f'{float(x):,.{d}f}'
def integer(x):return f'{int(x):,}'
def pct(x,d=1):return f'{100*float(x):.{d}f}%'


def render_html(md_path,title,lang):
    engine=markdown.Markdown(extensions=['tables','fenced_code','toc','sane_lists'])
    body=engine.convert(md_path.read_text())
    css='''
    :root{--ink:#172332;--muted:#536273;--line:#dce3ea;--accent:#186499}
    *{box-sizing:border-box}body{font-family:system-ui,-apple-system,"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:var(--ink);margin:0;line-height:1.68;background:#f7f9fb}
    nav{position:fixed;width:255px;left:0;top:0;bottom:0;padding:25px 18px;background:#fff;border-right:1px solid var(--line);overflow:auto;font-size:12px}
    nav ul{padding-left:16px}nav a{color:var(--muted);text-decoration:none}nav li{margin:5px 0}
    main{margin-left:255px;padding:38px 46px 80px;max-width:1460px;background:#fff;min-height:100vh}
    h1{font-size:30px;line-height:1.25}h2{font-size:23px;border-top:1px solid var(--line);padding-top:25px;margin-top:42px}h3{font-size:18px;margin-top:28px}
    a{color:var(--accent)}p,li{max-width:1150px}code{font-family:ui-monospace,monospace;font-size:.9em;background:#f2f5f8;padding:1px 4px;border-radius:3px;overflow-wrap:anywhere}
    pre{white-space:pre-wrap;background:#f2f5f8;padding:16px;overflow:auto}pre code{padding:0}table{border-collapse:collapse;font-size:12px;display:block;overflow:auto;margin:20px 0;width:100%}
    th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{background:#edf3f8;text-align:left;white-space:nowrap}tr:nth-child(even) td{background:#fafcfe}
    img{display:block;width:100%;height:auto;margin:20px 0;border:1px solid #edf0f4}blockquote{border-left:4px solid #3a81b3;margin:22px 0;padding:8px 18px;background:#f2f7fb}
    .toplinks{font-size:13px;color:var(--muted)}@media(max-width:1000px){nav{display:none}main{margin:0;padding:22px}}
    @media print{nav,.toplinks{display:none}body,main{background:white}main{margin:0;padding:0;max-width:none}h2{break-before:auto}img,table{break-inside:avoid}a{color:inherit;text-decoration:none}@page{size:A4;margin:15mm}}
    '''
    doc=f'<!doctype html><html lang="{lang}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{css}</style><nav><b>{html.escape(title)}</b>{engine.toc}</nav><main><div class="toplinks"><a href="README.md">Directory guide</a> · <a href="REPORT.html">English</a> · <a href="REPORT.zh-CN.html">中文</a> · <a href="explorer.html">8-sample explorer</a></div>{body}</main></html>'
    md_path.with_suffix('.html').write_text(doc)


def explorer(ep):
    cols=['batch','instance_id','rollout_index','session_id','episode_s','create_wait_and_api_s',
          'post_create_setup_s','model_roundtrip_s','logical_tools_s','verifier_s','turns',
          'api_completion_tokens_sum','reward']
    payload=ep[cols].to_json(orient='records',force_ascii=False)
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Eight-sample timing explorer</title>
<style>body{font-family:system-ui,sans-serif;max-width:1240px;margin:32px auto;padding:0 22px;color:#183047;line-height:1.55}h1{font-size:27px}select{padding:9px;margin:6px;max-width:90%}.note{color:#536273;font-size:13px}.grid{display:grid;grid-template-columns:95px 1fr 135px;gap:9px;align-items:center;margin:12px 0}.track{background:#f0f3f7;height:25px}.bar{height:100%;background:#377daf}table{border-collapse:collapse;font-size:12px;width:100%;margin:25px 0}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:right}th{background:#eef4f8}td:first-child{text-align:left}#summary{padding:14px;background:#f2f6fa;margin:20px 0}a{color:#186499}@media print{select{border:0}}</style>
<h1>同一道题的8次尝试 / Eight samples of the same task</h1>
<p><a href="REPORT.zh-CN.html">中文报告</a> · <a href="REPORT.html">English report</a> · <a href="data/episodes.csv">All 256 samples (CSV)</a></p>
<label>Task / 题目 <select id="task"></select></label><label>Metric / 耗时口径 <select id="metric"><option value="episode_s">Runner total / 任务总耗时</option><option value="create_wait_and_api_s">Creation wait + API / 排队与创建</option><option value="model_roundtrip_s">Model roundtrips / 等待模型</option><option value="logical_tools_s">Logical tools / 工具调用</option><option value="verifier_s">Verifier / 评分</option></select></label>
<div id="summary"></div><div id="chart"></div><div id="table"></div>
<p class="note">Bars show minutes. Runner time starts when the Ray runner enters the task, so it includes the creation gate. Model/tool durations are reconstructed from second-resolution log markers; other values use explicit timers. Reward=1 means the verifier resolved the task. These are different sampled attempts, not controlled system A/B tests. No network service is contacted by this page.</p>
<script id="data" type="application/json">__DATA__</script><script>
const rows=JSON.parse(document.getElementById('data').textContent);const task=document.getElementById('task'),metric=document.getElementById('metric');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const keys=[...new Set(rows.map(r=>r.batch+'|'+r.instance_id))];for(const key of keys){const o=document.createElement('option');o.value=key;o.textContent='Batch '+key.replace('|',' — ');task.appendChild(o)}
const initial=new URLSearchParams(location.search).get('task');if(initial){const found=keys.find(k=>k.endsWith('|'+initial));if(found)task.value=found}
function draw(){const [b,id]=task.value.split('|');const a=rows.filter(r=>r.batch==b&&r.instance_id===id).sort((x,y)=>x.rollout_index-y.rollout_index);const values=a.map(r=>r[metric.value]);const max=Math.max(...values),min=Math.min(...values);const total=a.map(r=>r.episode_s);document.getElementById('summary').textContent='Batch '+b+' · '+id+' · resolved '+a.reduce((n,r)=>n+r.reward,0)+'/8 · total runtime range '+(Math.min(...total)/60).toFixed(2)+'–'+(Math.max(...total)/60).toFixed(2)+' min · slowest/fastest '+(Math.max(...total)/Math.min(...total)).toFixed(2)+'×';
document.getElementById('chart').innerHTML=a.map(r=>'<div class="grid"><span>Sample '+r.rollout_index+'</span><div class="track"><div class="bar" style="width:'+(100*r[metric.value]/(max||1)).toFixed(3)+'%;background:'+(r.reward?'#299d72':'#377daf')+'"></div></div><span>'+(r[metric.value]/60).toFixed(2)+' min · R='+r.reward+'</span></div>').join('');
document.getElementById('table').innerHTML='<table><thead><tr><th>Sample</th><th>Total min</th><th>Create+wait s</th><th>Model min</th><th>Tools s</th><th>Verifier s</th><th>Turns</th><th>Generated tokens</th><th>Reward</th></tr></thead><tbody>'+a.map(r=>'<tr><td title="'+esc(r.session_id)+'">'+r.rollout_index+'</td><td>'+(r.episode_s/60).toFixed(2)+'</td><td>'+r.create_wait_and_api_s.toFixed(2)+'</td><td>'+(r.model_roundtrip_s/60).toFixed(2)+'</td><td>'+r.logical_tools_s.toFixed(1)+'</td><td>'+r.verifier_s.toFixed(2)+'</td><td>'+r.turns+'</td><td>'+r.api_completion_tokens_sum.toLocaleString()+'</td><td>'+r.reward+'</td></tr>').join('')+'</tbody></table>'}
task.addEventListener('change',draw);metric.addEventListener('change',draw);draw();
</script></html>'''
    (OUT/'explorer.html').write_text(page.replace('__DATA__',payload))


def main():
    s=json.loads((OUT/'data/summary.json').read_text());f=json.loads((OUT/'data/additional-facts.json').read_text())
    ep=pd.read_csv(OUT/'data/episodes.csv');g=pd.read_csv(OUT/'data/groups.csv')
    raw=Path(s['runtime']);m=s['step1_metrics'];b1=s['batches']['1'];b2=s['batches']['2']
    phase_table=table(['Component','Seconds','Share of step 1'],[[p['phase'],number(p['seconds']),number(p['percent'],3)+'%'] for p in f['step1_wall_phases']])
    runner_table=table(['Runner component','Cumulative hours','Share of summed runner lifetimes'],[[p['phase'],number(p['seconds']/3600),number(p['runner_lifetime_percent'],2)+'%'] for p in f['aggregate_runner_phases']])
    dist_fields=[('episode_s','Runner total'),('create_wait_and_api_s','Creation wait + API'),('post_create_setup_s','Post-create preparation'),
                 ('model_roundtrip_s','Model roundtrips per runner'),('logical_tools_s','Logical tools per runner'),('verifier_s','Verifier'),('destroy_s','Destroy acknowledgement')]
    dist_table=table(['Component','Mean s','P50 s','P95 s','Max s'],[[label]+[number(s['episode_statistics'][k][q],3 if k=='destroy_s' else 2) for q in ['mean','p50','p95','max']] for k,label in dist_fields])
    batch_table=table(['Metric','Batch 1','Batch 2'],[
        ['Prompt groups × n','16 × 8','16 × 8'],['Completed / resolved','128 / 26','128 / 32'],
        ['Observed cohort span, min',number(b1['observed_wall_s']/60),number(b2['observed_wall_s']/60)],
        ['Event-reconstructed peak live',b1['client_live']['max'],b2['client_live']['max']],
        ['10-second API sampled peak live',b1['gpu']['sampled_peak_sandboxes'],b2['gpu']['sampled_peak_sandboxes']],
        ['Mean live sandboxes',number(b1['client_live']['mean']),number(b2['client_live']['mean'])],
        ['Mean reported GPU busy',number(b1['gpu']['mean_busy_pct'])+'%',number(b2['gpu']['mean_busy_pct'])+'%'],
        ['Time with mean GPU busy <50%, min',number(b1['gpu']['under_50pct_s']/60),number(b2['gpu']['under_50pct_s']/60)],
        ['Model-generated tokens',integer(b1['cum_tokens_generated']),integer(b2['cum_tokens_generated'])],
        ['Generated tokens / observed cohort second, all GPUs',number(b1['generated_tokens_per_observed_wall_s']),number(b2['generated_tokens_per_observed_wall_s'])]])
    startup_table=table(['Metric','Batch 1','Batch 2'],[
        ['First-to-last successful create, s',number(b1['creation_ramp_s']),number(b2['creation_ramp_s'])],
        ['Last create after cohort session-open, s',number(b1['last_created_offset_s']),number(b2['last_created_offset_s'])],
        ['Observed creation slope, instances/s',number(b1['created_per_s_during_ramp'],3),number(b2['created_per_s_during_ramp'],3)],
        ['Creation wait + API P50, s',number(b1['statistics']['create_wait_and_api_s']['p50']),number(b2['statistics']['create_wait_and_api_s']['p50'])],
        ['Post-create work still inside create gate, mean s',number(f['startup']['1']['post_create_gate_hold_mean_s']),number(f['startup']['2']['post_create_gate_hold_mean_s'])],
        ['Of that: cached tmux provisioning, mean s',number(f['startup']['1']['cached_packages_mean_s']),number(f['startup']['2']['cached_packages_mean_s'])],
        ['Of that: readiness/probes/other, mean s',number(f['startup']['1']['readiness_and_other_in_gate_mean_s']),number(f['startup']['2']['readiness_and_other_in_gate_mean_s'])],
        ['Lower bound: gate capacity occupied AFTER create success',pct(f['startup']['1']['post_create_gate_occupancy_lower_fraction']),pct(f['startup']['2']['post_create_gate_occupancy_lower_fraction'])],
        ['Dispatch/import log upper-bound median, s',number(f['startup']['1']['dispatch_log_upper_bound_stats']['p50']),number(f['startup']['2']['dispatch_log_upper_bound_stats']['p50'],3)]])
    tail_table=table(['Tail measure','Batch 1','Batch 2'],[
        ['After 90% complete: remaining min',number(b1['tail_after_90pct_s']/60),number(b2['tail_after_90pct_s']/60)],
        ['After 90% complete: share of cohort time',pct(f['tails']['1']['after90_wall_fraction']),pct(f['tails']['2']['after90_wall_fraction'])],
        ['Mean GPU busy in that tail',number(f['tails']['1']['after90_mean_gpu_busy_pct'])+'%',number(f['tails']['2']['after90_mean_gpu_busy_pct'])+'%'],
        ['After 95% complete: remaining min',number(b1['tail_after_95pct_s']/60),number(b2['tail_after_95pct_s']/60)],
        ['Last one sample alone adds, min',number(b1['last_sample_penalty_s']/60),number(b2['last_sample_penalty_s']/60)],
        ['Critical task',b1['last_instance_id'],b2['last_instance_id']],
        ['Critical sample verifier, s',number(b1['critical_verifier_s']),number(b2['critical_verifier_s'])]])
    group_table=table(['Batch','Task','R=1 /8','Fastest min','Median min','Slowest min','Max/min','Max/median'],[
        [int(r.batch),r.instance_id,f'{int(r.resolved)}/8',number(r.min_s/60),number(r.median_s/60),number(r.max_s/60),number(r.max_min_ratio)+'×',number(r.max_median_ratio)+'×'] for r in g.itertuples()])
    tool_table=table(['Logical tool','Calls','Mean s','P50 s','P95 s','P99 s','Max s'],[
        [name,integer(d['n'])]+[number(d['duration_s'][q]) for q in ['mean','p50','p95','p99','max']] for name,d in s['tool_statistics'].items()])
    rpc_table=table(['Instrumented operation','Calls','Mean s','P50 s','P95 s'],[
        [name,integer(d['n'])]+[number(d['duration_s'][q],4) for q in ['mean','p50','p95']]
        for name,d in s['rpc_statistics'].items()])
    def eight(iid,batch):
        rows=ep[(ep.instance_id==iid)&(ep.batch==batch)].sort_values('rollout_index')
        return table(['Sample','Total min','Create+wait s','Model min','Tools s','Verifier s','Turns','Generated tokens','R'],[
            [int(r.rollout_index),number(r.episode_s/60),number(r.create_wait_and_api_s),number(r.model_roundtrip_s/60),number(r.logical_tools_s,0),number(r.verifier_s),int(r.turns),integer(r.api_completion_tokens_sum),int(r.reward)] for r in rows.itertuples()])
    uniform=g[g.zero_reward_variance]
    overview=f'''# Uni-Agent SWE-agent RL: infrastructure and critical-path analysis

This report analyzes the **2026-09-20 three-step smoke run**, which stopped during trainer step 2 after a driver-reported uncorrectable GPU event. **One optimizer update completed; two batches of 128 rollouts completed; no trained checkpoint was saved.** The training data was SWE-reBench, not the 500-task SWE-bench Verified evaluation set. No full benchmark evaluation was run.

The questions are operational: where time went, why startup felt slow, what n=8 did to the completion barrier, whether verification or transport errors dominated, and which interventions have evidence behind them. Rollout timing statistics below come from the final runtime attempt's 256 completed episodes; the incident section separately covers earlier setup failures.

## 1. Findings that should drive the next run

1. **Rollout collection dominates the completed step:** {m['timing_s/gen']/60:.2f} of {m['timing_s/step']/60:.2f} minutes ({m['timing_s/gen']/m['timing_s/step']*100:.1f}%). Actor update took {m['timing_s/update_actor']/60:.2f} minutes. Faster gradient updates alone cannot remove the largest delay in this run.
2. **The startup bottleneck is partly in our client-side gate, not a measured multi-minute E2B boot.** The create timer includes queue waiting. Three create permits remain held through readiness checks and cached tmux installation. Measured post-create work alone occupied at least **91.1% / 92.0%** of the three-permit capacity during the two creation ramps.
3. **Long tails leave useful capacity idle.** After 90% of samples had finished, batch 1 still needed **{b1['tail_after_90pct_s']/60:.2f} minutes** and batch 2 **{b2['tail_after_90pct_s']/60:.2f} minutes**. These tails consumed **{pct(f['tails']['1']['after90_wall_fraction'])} / {pct(f['tails']['2']['after90_wall_fraction'])}** of cohort wall time. Their mean GPU busy levels were only **{f['tails']['1']['after90_mean_gpu_busy_pct']:.1f}% / {f['tails']['2']['after90_mean_gpu_busy_pct']:.1f}%**.
4. **Verifier cost is small on the observed critical paths.** Its mean was {s['episode_statistics']['verifier_s']['mean']:.2f}s, P95 {s['episode_statistics']['verifier_s']['p95']:.2f}s and maximum {s['episode_statistics']['verifier_s']['max']:.2f}s. The final sample's verifier took just **{b1['critical_verifier_s']:.2f}s / {b2['critical_verifier_s']:.2f}s**, versus last-sample gaps of **{b1['last_sample_penalty_s']/60:.2f} / {b2['last_sample_penalty_s']/60:.2f} minutes**.
5. **Fine-grained tool transport is an optimization opportunity, but not the leading batch tail.** There were {integer(f['rpc']['agent_total_rpc_count'])} instrumented agent-side RPCs for {integer(len(pd.read_csv(OUT/'data/tool_actions.csv.gz')))} logical tool actions, about **{f['rpc']['agent_rpc_per_logical_tool']:.2f} RPCs/action**. Most exec calls were short control operations; direct file reads/writes were much cheaper.
6. **Reliability is the first blocker.** A real update was verified on all eight ranks, then an ATHUB event and automatic all-GPU reset destroyed unsaved state. The next sustained run needs a repaired/validated GPU environment and a checkpoint policy suitable for that risk.

Use the [offline eight-sample explorer](explorer.html) to select any of the 32 tasks and compare its eight durations, phase costs, tokens and rewards. The [256-row episode CSV](data/episodes.csv) and [32-group CSV](data/groups.csv) contain the underlying measurements.

## 2. Experiment scope and accounting rules

| Setting | Actual run |
|---|---|
| Model | Qwen/Qwen3-Coder-30B-A3B-Instruct; full-parameter RL |
| Trainer | Uni-Agent + bundled verl, colocate_async, GRPO advantages / GSPO loss, R3 Router Replay |
| Hardware | 8 AMD MI350X; actor TP1/PP1/CP4/EP8; four TP2 vLLM replicas on the same GPUs |
| Sampling | 16 prompts/batch × 8 independent attempts = 128 rollout sessions |
| Budgets | 131,072 Agent tokens; 200 Agent steps; 3 requested trainer steps |
| E2B | User-hosted endpoint; reported 192 CPUs / 1 TB RAM / quota 512; 4 vCPU and 8 GiB configured per task sandbox |
| Client gates | 128 session cap; 3 combined create/readiness/provision permits; 16 instrumented exec permits |
| Dataset | All 1,150 official prepared SWE-reBench rows retained; ready-template tasks ordered first; first 32 tasks actually used |
| Scheduling deviation | Warmup prefetch=0; fixed 48-prompt submission cap for the planned three batches |
| Outcome | 1/3 updates completed, 256/384 planned rollouts completed, no step-3 checkpoint |

**A rollout means one independent attempt. A prompt group means one task with eight rollouts.** A task's eight attempts share the question, model version and recipe, but have independent writable sandboxes and different sampled action sequences.

**Instance quota and compute capacity are different.** At 128 live sandboxes, the configured envelopes sum to **512 vCPU and 1,024 GiB (1 TiB)**. The vCPU count is 2.67 times the reported 192 host CPUs; the memory envelope is approximately the reported entire host RAM, before host/services overhead, with TB versus TiB units unspecified. These are configured guest resources, not measured CPU demand or resident memory. The successful 128-instance check established that the platform admitted and exercised that many instances; it did not establish sustained full-load capacity. No server-side CPU, memory-pressure or disk measurements are available to decide whether actual contention occurred.

There are three different denominators in this report:

- **Trainer wall time:** the explicit non-overlapping timers for completed step 1. This is the correct view for “what percentage of a training step is rollout versus optimization?”
- **Cohort observation span:** earliest framework session-open to the last observed completion. This is reconstructed from session timestamps and differs slightly from the trainer's generation timer. It is the basis for startup ramps, tails and GPU occupancy.
- **Summed runner/sandbox time:** add durations across concurrent attempts. This measures accumulated occupancy or where individual attempts spend time. It can be many hours even when the batch finishes in half an hour; it must not be added to trainer wall time.

Structured event durations and runner/verifier timers have subsecond resolution. Model and logical-tool intervals are reconstructed from log markers with **one-second endpoint resolution**; each interval can be off by under about one second. Their component shares are approximate. Rounded framework timestamps can also make a subsecond finalization interval appear negative; the dataset preserves interval bounds rather than interpreting that as negative work. No E2B server-side boot trace, pure inference queue/prefill/decode split, server CPU/RAM utilization or checkpoint-write benchmark was available.

## 3. Where the completed training step spent its wall time

{phase_table}

{fig('01_step1_wall_phases','Only completed trainer step 1 has a full authoritative phase breakdown.')}

The small residual is only time outside these explicit timers; it does **not** mean orchestration overhead is negligible, because waiting, dispatch, RPCs and verification are included inside `gen`. Step 2's rollout completed, but its training-side computation was interrupted, so assigning a complete step-2 training percentage would be misleading.

The actor's reported MFU was **{m['perf/mfu/actor']*100:.2f}%**, despite long stretches of nearly 100% GPU busy. MFU is an estimator tied to the model/FLOP accounting; GPU busy is a hardware utilization sample. They answer different questions. The run showed generic AITER/Torch fallbacks, Stream-K/GSU warnings and shape-driven Dynamo recompilation warnings. Those are reasons to profile kernels later, not evidence that any one warning caused the hardware event or a quantified speed loss.

## 4. The actual rollout and GPU timeline

{batch_table}

{fig('02_attempt_timeline','Rollout bands, GPU utilization, completed update 1 and the later GPU fault.')}

The 128-session setting did not mean 128 continuously live sandboxes. Client-event reconstruction found peaks of **126 / 122**, while ten-second API sampling saw **125 / 122**. The separate capacity check did hold and verify 128 live instances. Mean live counts of roughly **43 / 39** reflect creation ramp-up and completion tail-down, not a proof that the E2B service could only support that many.

With warmup prefetch disabled, this run effectively waited for all 16 groups of a batch before updating. It demonstrates **GPU colocation**, but is not a measurement of the best fully asynchronous/partial-rollout throughput. Idle capacity released by completed samples could not be filled with the next batch under this bounded schedule. The framework reported zero staleness in step 1, consistent with this behavior.

## 5. Why sandbox startup was slow

{startup_table}

{fig('03_startup_ramp_and_wait','The measured create duration includes waiting before the SDK create call.')}

Across 256 runners, `created.seconds` had P50 **{s['episode_statistics']['create_wait_and_api_s']['p50']:.2f}s**, P95 **{s['episode_statistics']['create_wait_and_api_s']['p95']:.2f}s** and maximum **{s['episode_statistics']['create_wait_and_api_s']['max']:.2f}s**. It would be wrong to report these as E2B cold-boot latency. The timer starts before acquiring the shared create semaphore; the SDK request begins only after a permit is obtained.

The permit is then retained for:

```text
E2B create → /bin/true readiness probe → command -v tmux
    → OS lookup → upload cached packages → dpkg install → tmux check
    → release create permit
```

The first three creation measurements, which precede a backlog, were **0.41–0.72s** in batch 1 and **0.58–0.63s** in batch 2. These are useful observations, not a service-wide boot-latency distribution. The API may return before the execution channel is ready.

After successful create returned, work still holding the gate averaged **7.76s / 9.54s**. Only **2.35s / 2.91s** of that was the explicitly timed cached-package upload/install; the remainder was readiness/probes/OS lookup and associated overhead. Summing the observed post-create hold intervals, clipped to the creation ramp, already occupies at least **91.1% / 92.0%** of the three-permit capacity. This is a direct lower-bound argument for a client-side bottleneck without guessing unknown API request-start times.

There was also a first-use runner dispatch/import effect: the framework-to-runner log delta had median **35.45s** in batch 1, versus a subsecond upper-bound median around **0.049s** in batch 2. The first batch had one outlier near **268s**. This delta combines scheduling, process startup/import and local entry overhead; it is not a pure Ray scheduler metric. Batch 2's longer create ramp despite warm workers shows that worker warmup alone does not solve the gate bottleneck.

**Intervention:** fix template building, bake tools into templates, and separate a small create-API gate from a separately bounded readiness/provision pool. Do not simply launch unbounded startup requests: the initial capacity test observed connection resets, and the passing retry changed both readiness probing and execution burst concurrency.

## 6. Per-runner time composition

{runner_table}

{fig('04_runner_time_composition','Parallel accumulated time: do not read this as a training-step pie chart.')}

{dist_table}

Total runner time was **{f['accounting']['runner_s']/3600:.2f} runner-hours**, and acknowledged live sandbox time was **{f['accounting']['sandbox_s']/3600:.2f} sandbox-hours**. Model-response waits overlapped **{pct(f['model']['model_roundtrip_share_of_sandbox_lifetime'])}** of that live-sandbox residence and **{pct(f['model']['model_roundtrip_share_of_agent_loop'])}** of Agent-loop time. This explains why many stateful environments can remain allocated while the model service controls progress. It does **not** measure sandbox CPU idleness; a background process may run while the Agent waits, and server CPU metrics were unavailable.

Post-create preparation includes readiness, cached tools, repository-history cleanup and shell initialization. Tool provisioning is nested inside it and must not be added again. The very small destroy acknowledgements do not prove that every server-side resource is physically reclaimed in 12ms; they measure the client-visible API operation.

## 7. Long-tail cost at n=8

{tail_table}

{fig('05_duration_and_completion_tail','Completion times use nearest-rank milestones: 90% means 116 of 128 samples completed.')}

For a group, readiness waits for the slowest of eight attempts. For this no-prefetch batch, completion waits for the slowest of all 128 attempts. The median within-group slowest/fastest runner ratio was **{s['group_max_min_ratio']['p50']:.2f}×**; the largest was **{s['group_max_min_ratio']['max']:.2f}×**. The median slowest/median ratio was **{s['group_max_median_ratio']['p50']:.2f}×**.

This does not establish that n=8 is intrinsically worse than n=4 at fixed total rollout count: changing n also changes the number of distinct tasks and the learning signal, and the workload has not been rerun under those alternatives. It does show why “most samples are done” is a poor ETA signal for a blocking batch.

{fig('06_all_8_sample_groups','Every measured task and all eight samples. An asterisk marks reward 1.')}

### Case A: AnyIO is batch 1's critical group

The slowest attempt took **28.84 minutes**, versus **8.75 minutes** for the fastest. The slow attempt generated **43,223 tokens** across **110 Agent steps**, versus **7,326 tokens / 41 steps** for the fastest. Its **1,472s of model roundtrips** dominated its lifetime; the verifier took only **15.73s**. This is a workload/model-path tail, not an observed verifier bottleneck.

{eight('agronholm__anyio-639',1)}

{fig('07_anyio_eight_samples','Within-task timelines expose both staggered startup and different model/tool paths.')}

### Case B: Hy determines the end of batch 2

The slowest Hy sample took **28.99 minutes** and generated **37,973 tokens**, compared with **15.90 minutes / 12,324 tokens** for its fastest peer. Its creation wait+API cost was **424.12s**, its model roundtrips **1,235s**, and its verifier **6.35s**. Startup and behavior both mattered. The final sample ended **6.61 minutes after the penultimate sample of the whole batch**.

{eight('hylang__hy-2522',2)}

{fig('08_hy_eight_samples','The final Hy sample remains expensive after separating creation waiting.')}

### Case C: a task with an 11.72× sample spread

For `diogommartins__simple_json_logger-14`, the fastest attempt took **73.41s** with **8 steps / 601 generated tokens**. The slowest took **860.08s** with **44 steps / 16,864 tokens**. This is the largest ratio in the dataset, but it was not the batch-2 critical path: a large within-task ratio and a large batch-level tail are different findings.

{fig('09_largest_sample_ratio','An extreme sample ratio can coexist with a different task controlling batch completion.')}

The computed “completed-sample waiting equivalents” in `groups.csv` are diagnostic slot-seconds, **not live sandbox time or GPU idle time**: completed samples had already been destroyed. They indicate opportunities a different scheduler might fill.

## 8. Sampling cost versus reward diversity

**{len(uniform)} of 32 groups ({len(uniform)*8} rollouts)** had identical rewards across all eight attempts. They consumed **{pct(s['zero_variance_episode_time_fraction'])} of summed runner time** and **{pct(s['zero_variance_generated_token_fraction'])} of generated tokens**. There were {int((uniform.resolved==0).sum())} all-zero groups and {int((uniform.resolved==8).sum())} all-one groups.

With group-normalized GRPO rewards, uniform-reward groups have zero reward-derived within-group advantage. This is useful cost/signal information, not a reason to retrospectively drop slow failures: their rewards were unknown beforehand, and changing group filtering changes data selection and refill demand. A later controlled study can compare n=4/B=32 and n=8/B=16 at 128 sessions, or evaluate supported group filtering, while tracking group reward variance, consumed data and refill cost.

## 9. Verifier, tooling and timeout behavior

### Verification was small and bounded here

All **256 evaluations completed with parseable test output**. There was no recorded verifier timeout; maximum call time was **48.83s** against a **600s** evaluator limit. Cumulative verifier time was **50.68 minutes**, but this was parallel across samples and therefore cannot be added as 50.68 minutes to the batch wall clocks. It was only **1.62% of summed runner lifetimes**.

The exact verifier timer includes any exec-admission waiting, SDK/transport and command execution. The inner instrumented exec timer begins after its gate; the difference is queue/wrapper overhead, not an independently measured server compute interval. An evaluation process completing normally is also distinct from tests passing: **58/256** rollouts resolved their tasks.

### Logical tools amplify into many RPCs

{tool_table}

{rpc_table}

The Agent is configured with `stateful_shell`, exposed to the model as `shell`. This provider does not implement the native persistent-shell interface, so the framework uses **tmux over one-shot exec**. A logical command writes an input file, sends a tmux command, polls an exit-code file, may wait on a tmux signal, then reads stdout/stderr. File editing also issues existence/type checks.

Of **36,988 exec RPCs**, **16,052 were `cat`**, **12,462 were `test`** and **6,073 were `tmux`**. Among execs attributed to Agent operation, **{pct(f['rpc']['agent_exec_under_1s_fraction'])} lasted under one second**. Direct file APIs had mean times around **9ms for reads** and **17ms for Agent writes**, while the median Agent exec RPC was **0.290s**. This suggests batching/control-protocol or native-session work is more promising than optimizing file payload transfer in isolation.

The instrumented exec gate reached 16 concurrent calls. It was fully occupied for **{f['rpc']['batch1_time_at_16_s']:.1f}s / {f['rpc']['batch2_time_at_16_s']:.1f}s**, about **{pct(f['rpc']['batch1_time_at_16_s']/b1['observed_wall_s'])} / {pct(f['rpc']['batch2_time_at_16_s']/b2['observed_wall_s'])}** of cohort spans. Average use of the 16-permit capacity was only **{pct(f['rpc']['batch1_gate_capacity_utilization'])} / {pct(f['rpc']['batch2_gate_capacity_utilization'])}**. It can create burst queuing, but it was not continuously saturated. These intervals exclude direct readiness/provision operations and file APIs that bypass this gate.

{fig('11_verifier_and_rpc_calls','Verifier distribution and the low-level control operations behind Agent tools.')}

### Only one logical timeout and one format error

- **Shell timeout:** one Shopify sample issued `find /testbed -name "*.py" -exec grep -l "not all arguments converted" \\;`, omitting the `{{}}` file placeholder. `grep` consequently reads standard input, consistent with the tool's interactive-wait timeout. The 120s command budget produced a **138s observed logical call**, including polling/interruption/output handling. The Agent continued and finished. This sample was not the batch critical path.
- **Format error:** one watchdog sample passed an extra `shell` argument to a function that only accepts `command`. Validation rejected it before normal execution; the Agent corrected the call. The logged interval rounded to zero seconds.
- **Main-attempt transport:** no recorded E2B create/readiness/destroy/exec-transport error events and no anchored model-client retry warnings. This does not include the earlier capacity-test resets or failed setup attempts.
- **Nonzero exec exits:** {integer(f['rpc']['nonzero_exec_exits'])} were observed, but these are not an infrastructure-failure count. For example, `test` and polling `cat` normally return nonzero while a path/result does not exist.

None of the 256 episodes exhausted the configured Agent budget: all returned `finished=True`; observed Agent steps reached at most **110** and last-call context+completion at most **122,743 tokens**. The two long tails are therefore not evidence of hitting the 200-step or 128K limit.

## 10. Model service time and metric interpretation

{fig('10_model_work_and_latency','Descriptive correlations; shared GPU load and different tasks prevent a causal latency claim.')}

The logs contain **9,970 completed Agent model-query calls**, with **2,759,570 generated tokens**. These generated-token totals exactly match the exported trajectories' model-token masks for every episode. The API prompt-token sum is much larger—**200,993,200**—because multi-turn requests resend history. It is not a count of unique tokens physically recomputed on GPUs: prefix-cache hit data was not recorded.

Model-query roundtrip latency averaged **{s['request_statistics']['latency_s']['mean']:.2f}s**, with **P50 {s['request_statistics']['latency_s']['p50']:.0f}s, P95 {s['request_statistics']['latency_s']['p95']:.0f}s, P99 {s['request_statistics']['latency_s']['p99']:.0f}s, maximum {s['request_statistics']['latency_s']['max']:.0f}s**. Completion sizes ranged from 7 to 4,972 tokens (median 75). A slow roundtrip is not automatically an infrastructure timeout: these are completed calls with different output sizes and input histories, and the measured interval combines client work, service queueing, inference and transport. Instrumenting those subspans is necessary before choosing between more serving capacity, better cache reuse or lower RPC latency.

Within task groups, demeaned Agent time and generated token count had Pearson correlation **{s['within_group_demeaned_pearson']['agent_s']['api_completion_tokens_sum']:.3f}**. Across all episodes, the Spearman correlation between total runner duration and generated tokens was **{s['spearman']['episode_s']['api_completion_tokens_sum']:.3f}**. These support generated workload as an explanation for duration variation, while leaving queue, prefill, decode and transport contributions unresolved.

Three metric traps matter for this analysis:

1. `perf/throughput=267.19` is computed as **4,530,385 trajectory tokens / (2,119.43 step seconds × 8 GPUs)**. It is not decode-only tokens/s. Likewise the reported generation ms/token uses trajectory response length, which includes tool observations and history tokens.
2. In this version, V1 converts jagged data to batch-padded tensors before `compute_data_metrics`. `response_length/clip_ratio=1/128` and `prompt_length/clip_ratio=8/128` compare lengths with that padded batch maximum. They are **not sufficient evidence of exhausting the configured token limits**. Termination reasons and configured budgets are the appropriate checks.
3. Gateway `num_turns` counts user/assistant messages plus one. Every observed trajectory's counter is **Agent query count + 2**; the trainer's maximum 112 therefore corresponds to the Agent's 110 steps, not 112 model calls.

## 11. Infrastructure incidents and reliability cost

| Incident | Evidence and consequence | What the data supports |
|---|---|---|
| Fresh template builds failed | Multiple images and a minimal import-only probe returned internal errors during E2B environment initialization | Build path needs repair; reusing ready templates does not validate arbitrary new-image support |
| Initial capacity command resets | 126/128 checks passed first; two connection resets; all cleaned | Repeat passed 128/128 with readiness probes and reduced command burst concurrency; root cause was not isolated |
| Missing tmux in ready task images | Cached Debian tmux/libutempter packages installed at startup | Correctness recovered, but provisioning was placed inside the three-permit create gate |
| Lease mismatch | Training requested 7,200s; API rejected durations above one hour | Actual finalized Task YAML must be preflighted; capacity alone does not test all runtime settings |
| Failure refill escaped prepared prefix | Initial lease failures caused asynchronous refill into unmapped tasks | A bounded failure path is needed; the run added a 48-prompt guard and disabled extra warmup prefetch |
| Resource metrics unavailable | Explicit range queries failed and default range returned no samples | No CPU/RAM/disk utilization claim for the E2B server can be made |
| GPU event interrupted step 2 | ATHUB uncorrectable event at PCI 0000:66:00.0; XGMI events at 0000:96:00.0; automatic reset of all eight GPUs | Hardware/driver reliability is a blocker; exact component/firmware/software root cause remains undetermined |

The hardware event occurred around **09:52:53 UTC**. The kernel explicitly reported lost VRAM contents. The step-1 optimizer update was real (gradient norm **0.084919**, all eight ranks successful, **1,149/131,072** sampled parameter elements changed), and its new weights served batch 2. But saving was scheduled only at step 3, so **no resumable trained checkpoint survived**.

The GPU failure happened after batch-2 sandboxes were already cleaned. Thus “zero leftovers after the fault” does **not** prove cleanup would work if SIGABRT occurred mid-rollout. Python `finally` blocks did not complete after the abort. An independent run-ID-based lease janitor and heartbeat/exit recorder would protect the control plane more reliably. Some CPU runner/coordinator processes also received ROCm hardware exceptions; keeping non-GPU orchestration independent of the accelerator runtime should be considered where the framework permits it.

The complete attempt lasted approximately **{(s['state']['exited_unix']-s['launch']['started_unix'])/60:.2f} minutes** before abort, including initialization and the partial second training computation. This is useful recovery-cost evidence. It is not a measured checkpoint overhead: no checkpoint write occurred, so that cost remains unknown.

## 12. Prioritized next experiments

| Priority | Change / experiment | Evidence motivating it | Acceptance measurements |
|---|---|---|---|
| P0 | Investigate GPU 0000:66:00.0 and XGMI-related events; validate sustained stability | Actual uncorrectable event and lost VRAM after a successful short preflight | Sustained representative workload, kernel/RAS evidence, no automatic resets; do not treat one short health pass as a repair |
| P0 | Repair new-image template builds | Ready-template workaround restricts deployability of the full recipe | Build and base/reference checks for the intended task set; preserve image digests |
| P1 | Bake tools; split create from readiness/provision gates | At least 91–92% of create-gate capacity was held after create success | A/B current gate vs pre-baked tools vs separately bounded readiness; ramp, first model request, resets, live cap and cleanup |
| P1 | Restore supported prefetch/partial rollout in a controlled run | 32–45% of cohort time remained after 90% completed | Prepare enough templates for lookahead; adjust the 48-prompt guard; compare consumed workload, wall time, GPU busy, staleness, reward and unused/cancelled sessions |
| P1 | Save each completed step during stability work; retain only the newest | One completed update was lost with no checkpoint | Measure actual save time/bytes and verify reload; balance overhead against observed recovery cost |
| P1 | Independent cleanup/heartbeat process | SIGABRT bypassed Python teardown; this fault occurred when no sandbox was live | Deliberate failure during active rollout in an isolated test; only run-owned cleanup; no orphaned leases |
| P2 | Native persistent shell or batched control operations | 4.49 instrumented RPCs per logical tool; cat/test/tmux dominate | Preserve cwd/env/output/interrupt semantics; replay representative tool workloads and validate final grading |
| P2 | Kernel/compile profiling after stability | Actor MFU 6.86%, eager inference and generic fallbacks | Controlled kernel/graph variants with numerical checks and end-to-end timing; no claimed speedup from warning text alone |
| P2 | n=4/B=32 versus n=8/B=16, or supported group filtering | Nine uniform-reward groups used about one quarter of cost | Same session budget, multiple repeats, reward diversity, refill cost, policy staleness and training quality |

The first performance changes should target **startup admission and rollout scheduling**, while preserving the semantics of the Agent and verifier. Optimizing a 12-second verifier first would leave the measured multi-minute tails largely intact.

## Appendix A. All 32 eight-sample groups

Durations below are precise runner-entry-to-return timers and include creation queuing. They are not all measured from a common batch start. The heatmap and interactive explorer expose each individual sample.

{group_table}

## Appendix B. Reproduction and evidence

- [Measurement methodology and limitations](METHODS.md)
- [256 episodes](data/episodes.csv), [32 groups](data/groups.csv), [model-query intervals](data/model_requests.csv.gz), [logical-tool intervals](data/tool_actions.csv.gz), [RPC intervals](data/sandbox_rpc.csv.gz)
- [Derived summary](data/summary.json), [additional facts](data/additional-facts.json), [source SHA-256 inventory](data/source-manifest.json)
- [Raw completed-step metrics](evidence/train-metrics.jsonl), [fault summary](evidence/kernel-fault-summary.txt), [actual configuration](evidence/train.yaml), [provider implementation](evidence/source/e2b_provider.py)
- CPU-only reproduction: `python3 analysis/extract.py`, then `python3 analysis/render.py`, then `python3 analysis/write_report.py`. Extraction reads the original stopped runtime; figures can be regenerated using the included tables and copied evidence. Report generation also refreshes evidence copies from the original runtime. No model or sandbox is launched.
'''
    # A typo in a nested f-string would be caught before writing reports; all numbers come from the shared tables.
    (OUT/'REPORT.md').write_text(overview)

    chinese=f'''# Uni-Agent SWE任务RL：基础设施耗时与瓶颈分析

本报告分析2026-09-20这次3-step基础实验。**实际完成1次optimizer更新、两批共256条rollout；第2步训练计算被GPU不可纠正错误中断，第3步未启动，也没有保存新的模型checkpoint。** 训练数据来自SWE-reBench，本次没有跑500题SWE-bench Verified评测。

这里把“一道题的8次独立尝试”称为一个**题组**，每次尝试称为一条rollout/sample。每批16个题组，`n=8`，共128条；它们各自使用独立sandbox。

## 1. 最值得关注的结论

- **rollout是完整训练step的主要耗时。** 第1步共{m['timing_s/step']/60:.2f}分钟，其中rollout收集{m['timing_s/gen']/60:.2f}分钟，占{m['timing_s/gen']/m['timing_s/step']*100:.1f}%；actor更新{m['timing_s/update_actor']/60:.2f}分钟，占约12.5%。
- **启动慢主要不能解释为“E2B boot要几分钟”。** 创建计时包含客户端排队；3个创建名额直到就绪探测和tmux准备结束才释放。创建成功后的工作，就占掉了启动阶段至少91.1%和92.0%的这3个名额容量。
- **长尾比平均耗时更决定整批结束时间。** 两批在90%样本完成后，还分别等待{b1['tail_after_90pct_s']/60:.2f}和{b2['tail_after_90pct_s']/60:.2f}分钟，占整批约32%和45%；最后一个sample单独增加了5.53和6.61分钟等待。
- **verifier不是这次主要长尾。** 平均11.88秒，P95为26.88秒，最长48.83秒。两批最后结束的sample，verifier分别只用了15.73和6.35秒。
- **工具接口存在明显的细粒度RPC开销。** 9,950次逻辑工具调用对应44,645次agent阶段的已记录RPC，平均4.49次RPC/工具动作。`cat`、`test`、`tmux`占了大量调用。
- **首要阻塞仍是稳定性。** 硬件/驱动事件使已更新但未checkpoint的权重丢失。应先处理GPU/XGMI稳定性，再比较吞吐优化。

可打开[8-sample交互查看器](explorer.html)，按题目和耗时口径查看每组8个sample。原始抽取结果见[256条明细](data/episodes.csv)和[32个题组](data/groups.csv)。

## 2. 配置、范围与计时口径

模型为`Qwen/Qwen3-Coder-30B-A3B-Instruct`，单机8张MI350X；采用`colocate_async`、GRPO advantage / GSPO loss、R3 Router Replay。Actor为TP1/PP1/CP4/EP8，vLLM为4个TP2副本，两者复用同一组GPU。每批16题×8次尝试；Agent预算131,072 token、最多200轮。

自建E2B地址为`10.145.64.67:8088`，用户提供的配置为192 CPU、1 TB内存、配额512；每个任务sandbox配置4 vCPU和8 GiB。客户端限制为128个session、3个创建/就绪/依赖准备名额，以及16个已记录exec调用名额。后两个限制不能与sandbox存活上限混为一谈。

**实例配额与计算容量要分开看。** 128个环境按上述配置合计512 vCPU、1,024 GiB（1 TiB）：vCPU数为192个宿主CPU的2.67倍，内存配置总额也大致相当于整机容量，还没有扣除宿主与服务开销，且“1 TB”的单位口径未确认。这是guest配置总额，不是实测CPU需求或驻留内存。128实例验收通过证明平台能接纳并操作这些实例；是否能同时承受持续满负载，还需要CPU、内存压力和磁盘监控。本次数据既不能证明资源完全无争用，也不能证明这些配置已经造成资源瓶颈。

由于新模板构建服务出错，本次保留了完整1,150题文件，但把已有可用模板的题目排在前面，准备48题，实际用了前32题。`warmup prefetch=0`，并限制最多提交48题。**因此本次能验证colocate闭环，但不能代表官方充分异步、partial-rollout的最佳吞吐。**

本报告严格区分三种时间：

1. **训练step墙钟时间**：第1步完整timer，用来回答training与rollout各占多少。
2. **批次观测时间**：最早framework session打开，到最后一个观测结果结束；用来分析启动、长尾和GPU使用情况。它与trainer的`gen` timer有少量边界差异。
3. **并行任务累计时间**：把256条runner或sandbox时间相加，用来分析累计占用。它可以达到几十小时，不能再加到训练step的墙钟时间上。

结构化event、runner、verifier timer有亚秒精度；模型请求和逻辑工具时间来自秒级日志标记，端点各自有取整误差，所以其占比是近似拆解。未获得E2B服务端boot分段、纯GPU排队/prefill/decode分段、服务器CPU/RAM利用率和checkpoint写入耗时。

## 3. 第1个训练step的耗时占比

{phase_table}

{fig('01_step1_wall_phases','第1步的完整墙钟计时；各项互不重复，可以相加。')}

`gen`包含任务分发、等待环境、Agent多轮交互、verifier及轨迹交付等工作，不能把它理解为纯GPU生成时间。表中很小的剩余项仅表示没有落进这些timer的时间，不代表所有调度开销都只有这么少。

第2批rollout虽然完成，但第2步训练计算在结束前被打断，**不能给第2步编造完整training占比，也不能把未记录的部分当成0秒**。

## 4. rollout过程：启动爬坡、收尾与GPU利用率

{batch_table}

{fig('02_attempt_timeline','蓝色区域对应rollout批次；绿色线是第1次更新完成；红线是GPU故障。')}

从客户端事件重建，训练中sandbox峰值为126和122；10秒一次API采样看到125和122。独立容量验收则确实让128个sandbox同时存活并完成检查。训练没一直占满128，不等于E2B只能承载这些数量：样本会在创建尚未全部结束时提前完成，后段又逐渐退出。

平均存活约43和39个，是整个批次时间上的平均值，包含启动和长尾。它是“存活slot占用”，不是CPU利用率。也不能据此断言把并发降到64就不会影响性能。

这次关闭了未来批次预取。一个batch的16个题组都要返回，才进入更新；已经结束的sample释放了sandbox，但下一批还没有填入。因此后段常只剩少数任务、只有部分vLLM副本工作。第1步记录的policy staleness为0，也与这一调度方式一致。

## 5. 起sandbox最初为什么慢

{startup_table}

{fig('03_startup_ramp_and_wait','注意右图是客户端排队＋create API，并非服务端纯启动时间。')}

256次创建计时的中位数149.12秒、P95为385.81秒、最大434.11秒。源码表明timer在等待创建semaphore之前启动，所以这些数值**包含等待名额的时间**。

本次名额的实际占用范围是：

```text
等待3个创建名额之一
  → E2B create
  → /bin/true就绪检查
  → command -v tmux、读取OS信息
  → 上传缓存包、dpkg安装、tmux检查
  → 释放创建名额
```

每批最前面的3个create计时只有约0.41–0.72秒、0.58–0.63秒。这说明后续几分钟计时不能直接归因于E2B API，但这几个数据点也不足以代表服务端boot分布；API返回后，执行通道还可能需要就绪时间。

create成功后仍占着名额的工作，平均7.76和9.54秒。其中明确计时的缓存包上传/安装平均2.35和2.91秒，其余5.41和6.63秒是就绪检查、探测、OS读取等路径。只累计“create返回到工具准备完成”这一确定占着名额的区间，就已消耗启动窗口中3个名额容量的至少91.1%和92.0%；这个结论不需要猜测未记录的API请求开始时刻。

另一个成本来自runner冷启动：首批framework到runner入口的日志差，中位数约35.45秒，最大268秒；第二批中位数变成亚秒，日志给出的上界约0.049秒。这项包含Ray调度、进程启动/import和入口开销，不能全部叫作Ray排队。第二批创建爬坡仍更慢，说明只解决worker冷启动还不够。

**优先方案**是修好模板构建，把tmux等工具预装进模板，再把“create API并发”和“就绪/依赖准备并发”分开控制。不能直接无限放开：首次容量检查出现过连接重置，后续成功的复查同时调整了就绪探测和命令突发并发，尚未隔离单一根因。

## 6. 一条runner的时间花在哪里

{runner_table}

{fig('04_runner_time_composition','累计并行时间占比，分母不是训练step墙钟时间。')}

{dist_table}

256条runner累计约52.09小时，sandbox成功创建至销毁确认累计约40.01小时。等待模型响应的区间覆盖了约75.3%的sandbox存活时间，以及80.0%的Agent-loop时间。这说明状态化sandbox常常要在等待模型时继续存在；**不能把75.3%直接当成CPU空闲率**，因为可能存在后台进程，而且没有服务端CPU指标。

“post-create准备”已包括就绪、工具、Git历史清理和shell初始化；tmux安装是它的子项，不能再重复相加。销毁平均约11.6毫秒，是客户端API确认时间，并不等于测量了服务端物理资源回收全过程。

## 7. 同一道题的8个sample差异与长尾

{tail_table}

{fig('05_duration_and_completion_tail','90%完成点按nearest-rank取116/128；曲线末端直接影响整批结束。')}

32个题组中，最慢/最快耗时比中位数1.69倍，最大11.72倍；最慢/组内中位数的比值中位数为1.31倍。题组需要等待8次尝试中最慢的一个，而本次全批barrier又要等待128次尝试中最慢的一个。

这说明长尾代价，但**不能仅凭这次数据断言固定128条rollout时n=4一定比n=8快**：n变了，题目数、难度混合、组内奖励信号和调度都会变，需要对照实验。

{fig('06_all_8_sample_groups','32道题全部8次尝试的耗时；星号表示reward=1。')}

### AnyIO：第1批的关键长尾

同题最快8.75分钟，最慢28.84分钟。最慢sample进行了110轮，生成43,223 token；最快进行了41轮，生成7,326 token。最慢sample里，等待模型累计1,472秒，工具128秒，verifier仅15.73秒。

{eight('agronholm__anyio-639',1)}

{fig('07_anyio_eight_samples','同题8次尝试的真实时间线，能看出各自不同的模型与工具路径。')}

### Hy：第2批最后结束的sample

最快15.90分钟，最慢28.99分钟；生成token分别为12,324和37,973。最慢sample有424.12秒创建排队＋API时间、1,235秒模型响应等待、47秒逻辑工具时间，verifier仅6.35秒。它比整批倒数第二个结束的sample晚了6.61分钟。

{eight('hylang__hy-2522',2)}

{fig('08_hy_eight_samples','Hy样本既受启动排队影响，也受生成路径长度影响。')}

### simple_json_logger：最大11.72倍差异

最快sample只用了73.41秒、8轮、601个生成token；最慢用了860.08秒、44轮、16,864个生成token。不过它并不是整批最后结束的任务。**“组内相对差异最大”与“决定batch结束时间”不是同一件事。**

{fig('09_largest_sample_ratio','极端组内差异的例子；完整数值可在交互查看器中选择该题。')}

`groups.csv`还给出了各sample完成后到组内最慢sample结束的等待等价值。它只是诊断性的slot-seconds，不能算作额外sandbox占用或GPU空闲：那些sample完成时环境已经销毁。

## 8. n=8的成本是否都产生奖励学习信号

32组中有9组的8次reward完全相同，共72条rollout。这9组占约25.7%的累计runner时间、25.5%的生成token；其中{int((uniform.resolved==0).sum())}组全0、{int((uniform.resolved==8).sum())}组全1。

对组内归一化的GRPO来说，同组reward完全相同会得到0的奖励差异advantage。这能帮助评估采样成本，但不能事后因为结果差就删除长样本：采样前不知道它们的reward，组过滤也会改变训练分布并增加补采量。后续可比较固定128session预算下的`n=4/B=32`与`n=8/B=16`，同时衡量组内reward方差、补采成本和训练质量。

## 9. verifier、RPC以及实际timeout

所有256次评测都有可解析结果，没有记录到verifier超时；最长48.83秒，低于600秒限制。verifier累计50.68分钟是并行累计值，不能加到两批墙钟时间上。它只占累计runner时间1.62%；两条关键长尾中分别只占15.73和6.35秒。

verifier timer覆盖exec名额等待、SDK/传输和执行；内层exec timer在拿到名额后才开始。二者差值只能称为gate/wrapper开销，不能直接当成服务端计算时间。评测脚本正常结束也不意味着修复成功：两批总共58/256次通过了测试。

{tool_table}

{rpc_table}

当前provider没有提供native persistent-shell接口，所以`stateful_shell`回退到tmux-over-exec：写命令文件、发送tmux指令、轮询exit-code文件、必要时等待信号、再读stdout/stderr。文件编辑也会做多次存在性检查。

36,988次exec里，`cat`为16,052次、`test`为12,462次、`tmux`为6,073次；agent阶段96.7%的exec短于1秒。对比之下，直接文件读取平均约9ms、写入约17ms，而agent exec中位数约290ms。这支持优化控制RPC数量或native shell路径，而不是只优化文件payload传输。

exec gate确实到过16并发，分别满载约291.8和349.3秒，占各自批次约16.6%和20.1%；但平均只使用了16个名额容量的37.1%和37.6%。它可能产生突发排队，并非整个batch一直饱和。直接就绪/依赖准备调用和文件API不属于这个exec gate统计。

{fig('11_verifier_and_rpc_calls','verifier耗时分布与低层RPC数量构成。')}

正式运行只观测到两类逻辑工具问题：

- Shopify某个sample执行的`find -exec grep`缺少`{{}}`文件占位符，导致`grep`读取标准输入，符合等待输入的超时表现。命令上限120秒，完整逻辑调用耗时138秒，包含轮询、中断和读取输出。agent随后继续完成；它不是batch长尾。
- Watchdog某个sample向shell工具多传了一个不支持的`shell`参数，被参数校验拒绝；随后修正调用。该操作的日志耗时低于1秒分辨率。

正式attempt没有记录到E2B传输错误事件或模型客户端重试warning。先前容量检查的2次connection reset，以及初始化时租期配置被API拒绝，属于不同阶段，不能混在一起。低层exec共有7,530次非零退出码，也不能作为基础设施失败数：`test`、轮询结果文件的`cat`等经常正常返回非零。

所有256个Agent最终都返回`finished=True`，没有耗尽200轮或128K预算。最大实际Agent轮数110，最大最后一次上下文＋输出122,743 token。

## 10. 模型等待与指标误读

{fig('10_model_work_and_latency','图中是描述性关联；没有分离GPU排队、prefill、decode与传输。')}

总计9,970次完成的Agent模型查询，生成2,759,570 token，与轨迹mask标记的模型token逐条相等。API输入prompt累计200,993,200 token，因为多轮请求反复携带历史；它不能等同于GPU实际重算了2亿token，prefix-cache命中没有被记录。

单次模型查询往返平均{s['request_statistics']['latency_s']['mean']:.2f}秒，P50为{s['request_statistics']['latency_s']['p50']:.0f}秒、P95为{s['request_statistics']['latency_s']['p95']:.0f}秒、P99为{s['request_statistics']['latency_s']['p99']:.0f}秒，最长{s['request_statistics']['latency_s']['max']:.0f}秒；单次输出为7–4,972 token，中位数75。长请求并不自动等于infra timeout：这些请求均完成了，且输出长度和历史上下文各不相同。往返时间混合了客户端处理、服务排队、推理和传输，下一次需要分别记录这些分段，才能判断增加推理容量、改善cache或降低RPC延迟哪个更有效。

同题组内去均值后，Agent耗时与生成token数量的Pearson相关系数约0.976；跨全部样本，runner耗时与生成token的Spearman相关系数约0.779。生成工作量能解释很多时间差异，但这不是隔离变量后的因果测量。

三个指标尤其容易误读：

1. `perf/throughput=267.19`是轨迹总token数除以step时间再除8张卡，**不是decode TPS**。轨迹response长度也包含工具观测和多轮历史。
2. 当前V1在统计前把变长tensor补齐到batch最大长度，`clip_ratio`是与这个padding最大值比较。第1步的`response_length/clip_ratio=1/128`与`prompt_length/clip_ratio=8/128`，不能单独证明撞到了配置上限。
3. Gateway的`num_turns`统计user/assistant消息再加1，本次每条都比实际Agent查询次数多2。因此trainer里的最大112，与Agent日志最大110对应，并不是多做了两次模型调用。

## 11. 基础设施问题与恢复代价

新模板导入在E2B环境初始化阶段返回内部错误，最小的“只导入镜像”复查也失败；精确的服务端根因没有确定。因此本次使用已有模板，不能据此声称完整1150题的任意新镜像构建链路已经跑通。

开始时训练配置请求7200秒租期，被E2B的1小时限制拒绝。失败补采进一步走到未准备的题目才出现映射报错，所以映射报错是次生现象。最终按3600秒创建，并用**正式解析后的Task配置**做实际启动检查，同时加了48题提交上限。这个案例说明：只测“能创建一个sandbox”或“配额够128”还不够，预检参数必须与最终配置一致。

最严重的问题发生在09:52:53 UTC附近：内核报告`0000:66:00.0`不可纠正ATHUB事件，`0000:96:00.0`同期出现XGMI可纠正错误；随后ROCm报告GPU Hang，驱动自动重置全部8卡并明确记录VRAM丢失。日志只能支持硬件/驱动层故障这一结论，不能凭此断言具体坏件、固件缺陷或某条性能warning就是根因。

第1步确实更新了模型：梯度0.084919，8个rank成功，参数抽样有1,149/131,072个元素变化，更新后的模型还执行了第二批rollout。但因为只计划第3步保存，**没有可恢复的新checkpoint**。本次有效运行尝试约{(s['state']['exited_unix']-s['launch']['started_unix'])/60:.2f}分钟的状态都无法从训练checkpoint继续；现有指标和轨迹仍可分析。checkpoint写入开销本次没有测到，不能猜一个百分比。

故障发生时第二批sandbox已经销毁，所以故障后残留为零，**并不证明在rollout中途SIGABRT也一定能自动清理干净**。Python的finally没有在这次abort后完整执行；独立于训练进程的run-ID/lease清理器和heartbeat记录更可靠。部分CPU协调/runner进程也收到ROCm硬件异常，说明故障域存在耦合，后续可检查能否让纯CPU控制面尽量不初始化GPU runtime。

## 12. 建议的优化顺序与验证方法

| 优先级 | 建议 | 为什么优先 | 应测什么 |
|---|---|---|---|
| P0 | 排查GPU/XGMI，做持续负载稳定性验证 | 不可纠正事件导致显存和未保存状态丢失 | 代表性长负载、内核/RAS记录、无自动reset；短预检通过不能当成已修复 |
| P0 | 修复新模板构建 | 已有模板绕过了问题，尚不能覆盖完整recipe部署 | 新镜像build、base/reference评测、固定digest |
| P1 | 预装工具，拆分创建与就绪gate | 91–92%的创建名额容量被返回后的工作占用 | 当前方案/预装/分gate对照；爬坡、首次模型请求、错误率、并发和清理 |
| P1 | 准备足够模板后恢复受支持的prefetch/partial rollout | 90%完成后仍占32–45%时间 | 调整48题guard与收尾逻辑；测消耗的数据、staleness、吞吐、reward、取消/残留 |
| P1 | 稳定性阶段每step保存，只留最新 | 已完成的1步权重丢失 | 实测写入秒数/字节数与可恢复性，不预估免费保存 |
| P1 | 独立清理与心跳 | SIGABRT会绕过finally | 隔离测试中途故障；只清本run实例；验证无孤儿租期 |
| P2 | native shell或批量控制RPC | 逻辑工具被放大为4.49次RPC | 保持cwd/env/输出/中断语义，对照相同工具工作负载与最终评分 |
| P2 | 稳定后做kernel/graph分析 | actor MFU约6.86%，有通用fallback和重编译 | 受控数值检查和端到端时间，不凭warning文字推断提速幅度 |
| P2 | 固定session预算比较n/B组合或group filtering | 9个同reward题组占约四分之一成本 | 多次重复、reward方差、补采成本、策略陈旧度与训练质量 |

当前最有证据支持的性能方向是**启动门控和rollout调度**。优先优化平均12秒的verifier，很难消除已经观测到的几分钟长尾。

## 附录：全部32个题组

下表耗时来自runner入口到返回，包含创建排队；不是从统一batch时刻开始计时。

{group_table}

## 数据与复算

详见[METHODS.md](METHODS.md)、[完整明细](data/episodes.csv)、[题组明细](data/groups.csv)、[来源hash清单](data/source-manifest.json)和[可复算脚本](analysis/)。先运行`analysis/extract.py`，再运行`analysis/render.py`和`analysis/write_report.py`。这些脚本只读历史运行数据并生成CPU分析结果，不会启动训练或sandbox。
'''
    (OUT/'REPORT.zh-CN.md').write_text(chinese)
    (OUT/'METHODS.md').write_text('''# Measurement methods and reproducibility

The source runtime is identified in `data/summary.json`. Extraction selects only the 256 result files whose `started_unix` is at or after attempt 3's launch. Each session is joined to exactly one created event, one cached-tool provisioning event, one destroyed event, a Task log and a framework/trajectory record. All groups must have eight distinct rollout indices and each cohort must have sixteen tasks.

## Boundaries

- `episode_s`: precise Task runner entry-to-return interval from its result record. Framework dispatch/import before entry is separate.
- `create_wait_and_api_s`: the provider's monotonic timer begins before the Ray create semaphore. It includes admission queueing and the API call; it ends when create returns. It is not pure server boot time.
- `post_create_setup_s`: create acknowledgement to Agent STEP 1. Includes readiness, cached tools, repository cleanup and persistent-shell setup.
- `tools_provision_s`: timed cached archive upload/install/check; nested inside post-create setup and inside the create permit's lifetime.
- `agent_s`: STEP 1 marker to Episode done marker. Model-query and logical-tool intervals are parsed only from anchored `agents.react.agent` logger lines, not from arbitrary test output that may contain the words ERROR or retry.
- `model_roundtrip_s`: STEP marker to corresponding Prompt Tokens marker. Includes preparation, gateway/service queueing, inference, transport and any client retry delay. Endpoints are rounded to seconds. It is not pure decode time.
- `logical_tools_s`: ACTION to OBSERVATION/TIMEOUT/TOOL ERROR. Includes control RPCs, gate waits, process execution, polling and capture. An interval rounded to zero does not mean no cost.
- `verifier_s`: the explicit `eval_execution_time` timer. The final instrumented exec per episode is checked to coincide with the evaluator. Its own timer starts after the exec gate, so it is nested inside the verifier timer.
- `destroy_s`: client-visible successful kill acknowledgement interval. Server-side physical teardown is not instrumented.
- `other_episode_s`: runner duration minus the main measured/reconstructed phases. It includes small local gaps and timestamp resolution residue, not a separately traced subsystem.

## Parallel-time rules

Trainer step-1 timers are authoritative wall-clock values. Cohort windows use the earliest framework log timestamp and the latest framework/precise runner end; the boundary has roughly one-second resolution. Episode and live-sandbox durations are summed only when explicitly labeled cumulative. Percentiles of durations use NumPy linear quantiles. Completion milestones use nearest rank: ceil(fraction × 128).

Concurrency is reconstructed by sweeping acknowledged create/destroy intervals; API-sampled counts use the recorded ten-second monitor samples. The former can detect a brief peak missed by sampling, but it still describes client-acknowledged lifecycle, not physical CPU allocation. GPU busy is integrated as a left-held sampled counter over the cohort window. It is neither MFU nor a direct throughput measure. No sampling gap exceeded 30 seconds in the analyzed cohort windows.

The post-create gate occupancy lower bound intersects each [create-return, tool-provision-end] interval with the creation ramp and divides summed overlap by 3 × ramp duration. Source code holds the permit throughout each such interval. Unmeasured API time and release overhead are omitted, so this is a lower bound without assuming FIFO request ordering.

## Integrity checks

Extraction asserts 256 complete episodes, 32 groups of eight, matching tool/action counts, matching Agent steps/model-query result counts, matching timeout/error counts, and a valid final verifier event. Model-generated token counts summed from query usage equal exported model-token masks for all 256 episodes. The prior runtime audit checked token/mask/logprob lengths, finite logprobs and Task reward agreement. Structured phase residuals are under two seconds per episode.

Framework timestamps are integer seconds. `framework_finalize_log_delta_s` can be negative from rounding; its lower/upper fields bound the plausible nonnegative interval. It must not be averaged as a negative processing cost. Dispatch log deltas are upper bounds with less than one second of rounding uncertainty.

The reported trainer `clip_ratio` and `num_turns` definitions were checked in the pinned source. The former compares against padded batch maxima; the latter counts user/assistant messages plus one. They are not silently equated to budget exhaustion or Agent loop iterations.

## Limits

No server-side E2B boot spans, whole-node CPU/RAM/disk metrics, per-request inference queue/prefill/decode splits, cache-hit counters, kernel timeline or checkpoint I/O timing were recorded. We cannot quantify a native-shell speedup, the benefit of larger concurrency, or the effect of a different n/batch configuration from this run. Shared resources, different tasks and different model versions between cohorts prevent treating the two batches as a controlled A/B test.

## Rebuild

Run `python3 analysis/extract.py --runtime /path/to/the/original/run`, then `python3 analysis/render.py`, then `python3 analysis/write_report.py`. Packages: numpy, pandas, matplotlib and markdown. Extraction requires the original run. Figure generation uses included evidence when present and otherwise falls back to the original runtime; report generation refreshes evidence copies from that runtime. `data/source-manifest.json` records absolute original input paths and SHA-256 hashes; `data/evidence-manifest.json` also covers the copied source/configuration evidence. The generated CSVs and timing markers preserve derived evidence without duplicating full model thoughts and tool outputs. The offline HTML explorer embeds only selected timing/result columns. Run `python3 analysis/validate.py` after generation to verify accounting, joins, source/evidence hashes and document links; results are written to `data/validation.json`.
''')
    evidence=OUT/'evidence';(evidence/'source').mkdir(parents=True,exist_ok=True)
    names=['train-metrics.jsonl','telemetry.jsonl','failure-summary.json','capacity-128.json',
           'capacity-128-attempt-1.json','kernel-fault-summary.txt','kernel-fault.log','trajectory-audit.json',
           'container-image.json','environment.json','implementation-decisions.json','cleanup.json']
    for name in names:shutil.copy2(raw/'results'/name,evidence/name)
    (evidence/'optimizer').mkdir(exist_ok=True)
    for path in sorted((raw/'results/optimizer').glob('rank-*.jsonl')):
        shutil.copy2(path,evidence/'optimizer'/path.name)
    for name in ['train.yaml','task_config.yaml']:shutil.copy2(raw/'configs'/name,evidence/name)
    sources={'e2b_provider.py':raw/'code/e2b_provider.py','runtime_launch.py':raw/'code/runtime_launch.py',
             'react_agent.py':raw/'src/uni-agent/uni_agent/agents/react/agent.py',
             'shell.py':raw/'src/uni-agent/uni_agent/tools/shell.py',
             'swe_rebench_reward.py':raw/'src/uni-agent/uni_agent/tasks/swe_rebench/reward.py',
             'metric_utils.py':raw/'src/uni-agent/verl/verl/trainer/ppo/metric_utils.py',
             'trainer_base.py':raw/'src/uni-agent/verl/verl/trainer/ppo/v1/trainer_base.py',
             'gateway_session.py':raw/'src/uni-agent/uni_agent/gateway/session/session.py',
             'react_model.py':raw/'src/uni-agent/uni_agent/agents/react/model.py'}
    for name,path in sources.items():shutil.copy2(path,evidence/'source'/name)
    copied={name:raw/'results'/name for name in names}
    copied.update({name:raw/'configs'/name for name in ['train.yaml','task_config.yaml']})
    copied.update({'source/'+name:path for name,path in sources.items()})
    copied.update({'optimizer/'+path.name:path for path in (raw/'results/optimizer').glob('rank-*.jsonl')})
    evidence_manifest={name:{'source':str(path),'bytes':path.stat().st_size,
                            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                       for name,path in sorted(copied.items())}
    (OUT/'data/evidence-manifest.json').write_text(json.dumps(evidence_manifest,indent=2)+'\n')
    explorer(ep)
    render_html(OUT/'REPORT.md','Uni-Agent RL infrastructure analysis','en')
    render_html(OUT/'REPORT.zh-CN.md','Uni-Agent RL 基础设施分析','zh-CN')
    (OUT/'README.md').write_text(f'''# Uni-Agent SWE任务RL分析报告

这份报告针对2026-09-20的3-step尝试：实际完成1次更新、256条rollout，在第2步被GPU不可纠正事件中断。重点分析时间构成、启动排队、同题8次尝试差异、verifier与工具RPC、长尾及故障恢复代价。

- **[中文详细报告（HTML）](REPORT.zh-CN.html)** · [Markdown](REPORT.zh-CN.md)
- **[English technical report（HTML）](REPORT.html)** · [Markdown](REPORT.md)
- **[同题8个sample交互查看器](explorer.html)**：选择32道题之一，比较总时间、模型等待、排队、工具和verifier。
- [256条sample明细](data/episodes.csv) · [32个题组明细](data/groups.csv)
- [统计口径与限制](METHODS.md) · [复算脚本](analysis/) · [原始来源hash清单](data/source-manifest.json)

主要结论：第1步rollout占约83.7%；客户端创建gate在create成功后仍被准备工作占用；两批90%样本完成后仍有32%/45%的墙钟时间；verifier平均11.9秒，远小于主要长尾；正式运行只有一次逻辑shell超时和一次参数错误，GPU/驱动稳定性是完成训练的首要阻塞。

![完整运行时间线](figures/02_attempt_timeline.png)

报告保留可复算表格、11组PNG/SVG图和关键证据。分析只读取历史数据，没有重启训练、模型服务或sandbox。HTML与交互页面可以离线打开；没有外部CDN或服务依赖。
''')
    versions={'python':sys.version,'packages':{name:importlib.metadata.version(name) for name in ['numpy','pandas','matplotlib','markdown']}}
    (OUT/'analysis/environment.json').write_text(json.dumps(versions,indent=2))
    print(json.dumps({'english_words':len(overview.split()),'chinese_characters':len(chinese),
                      'groups':len(g),'episodes':len(ep),'directory':str(OUT)},indent=2))


if __name__=='__main__':main()
