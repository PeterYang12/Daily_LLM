"""Publish the completed same-task comparison as a compact numbered experiment."""
import argparse,hashlib,json,shutil
from pathlib import Path

def main(root):
    data=json.loads((root/'analysis/summary.json').read_text());assert data['complete']
    assert json.loads((root/'cleanup-audit.json').read_text())['success']
    protocol=json.loads((root/'protocol.json').read_text());probe=json.loads((root/'login-probe.json').read_text())
    target=Path('/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment/09_sandbox_three_way');target.mkdir(exist_ok=True)
    records=[]
    def copy(source,dest):
        output=target/dest;output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,output)
        records.append({'source':str(source),'destination':dest,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()})
    for p in Path('/home/yuhanya/uni-agent-lab/scripts/sandbox_three_way').glob('*.py'):copy(p,'code/'+p.name)
    for p in (root/'code-snapshot').rglob('*.py'):copy(p,'code/executed/'+str(p.relative_to(root/'code-snapshot')))
    for name in ['summary.json','cases.json','cases.csv','microbench.csv','tool-calls.csv']:copy(root/'analysis'/name,'results/'+name)
    for name in ['controls-audit.json','cleanup-audit.json','login-probe.json','model-service.json','source-hashes.json','stages.json','resource-release.json']:
        if (root/name).exists():copy(root/name,'results/'+name)
    copy(root/'protocol.json','configs/protocol.json')
    for p in (root/'analysis/figures').glob('*.svg'):copy(p,'figures/'+p.name)
    names=['docker','local_e2b','remote_e2b'];labels={'docker':'Local Docker','local_e2b':'Local E2B','remote_e2b':'Remote E2B-compatible'}
    for variant,path in protocol['variants'].items():
        base=Path(path.replace('/lab','/home/yuhanya/uni-agent-lab',1));provider='docker' if variant=='docker' else 'e2b'
        copy(base/'microbench'/provider/'summary.json','results/microbench/'+variant+'.json')
        if (base/'native-shell-probe.json').exists():copy(base/'native-shell-probe.json','results/native/'+variant+'.json')
        for case in base.glob(f'trial-*/{provider}/{protocol["task"]}'):
            tag=case.parents[1].name
            for name in ['result.json','verifier.json','trajectory-audit.json','candidate-source-only.patch']:
                copy(case/name,f'results/cases/{variant}/{tag}/{name}')
    groups=data['groups'];micro={(r['variant'],r['operation']):r for r in data['microbench']}
    lines=['# 9. The same SWE task on Docker, local E2B, and remote E2B','',
      '**2026-09-16. Fixed Qwen3-Coder-30B-A3B-Instruct; Uni-Agent ReAct, Gateway, and fresh SWE verification. No training update.**','',
      'We repeated `pallets__flask-5014` three times on each deployment, using one shared model service and alternating the order. The task asks the agent to reject empty Flask Blueprint names. All environments came from the same pinned SWE image digest.','',
      '| Measurement | Local Docker | Local E2B | Remote E2B-compatible |','|---|---:|---:|---:|',
      '| Resolved / valid trajectories | '+' | '.join(f'{groups[n]["resolved"]}/3; {groups[n]["valid_trajectories"]}/3 valid' for n in names)+' |']
    for key,label in [('wall_seconds','Mean complete rollout'),('model_api_s','Mean model API time'),('tools_s','Mean tool-call time'),('provision_s','Mean preparation: agent + verifier'),('verifier_s','Mean verification')]:
        lines.append('| '+label+' | '+' | '.join(f'{groups[n]["means"][key]:.2f} s' for n in names)+' |')
    lines += ['| Observed total-time range | '+' | '.join(f'{groups[n]["wall_min_s"]:.1f}–{groups[n]["wall_max_s"]:.1f} s' for n in names)+' |',
      '| Mean generated tokens | '+' | '.join(f'{groups[n]["means"]["generated_tokens"]:.0f}' for n in names)+' |',
      '| Mean tool calls, including submit | '+' | '.join(f'{groups[n]["means"]["tool_calls"]:.1f}' for n in names)+' |','',
      '![Rollout comparison](figures/rollout-comparison.svg)','',
      'These are independently sampled trajectories. Different token counts, commands, and tests change the amount of work, so total-time ratios are not pure platform speedups. Three repeats of one previously observed issue are not a general SWE-bench score.','',
      '## Fixed tool operations','',
      'To separate tool execution from model sampling, each deployment also ran the same 100 fixed operations. These measurements use an already-running sandbox; they exclude image/template builds and sandbox preparation.','',
      '| Median operation | Local Docker | Local E2B | Remote E2B-compatible |','|---|---:|---:|---:|']
    for key,label in [('exec_true','SDK/provider exec: true'),('read_1024','Read 1 KiB'),('write_1024','Write 1 KiB'),('read_1048576','Read 1 MiB'),('write_1048576','Write 1 MiB'),('stateful_shell_true','Stateful tmux shell: true')]:
        lines.append('| '+label+' | '+' | '.join(f'{micro[n,key]["median_s"]*1000:.1f} ms' for n in names)+' |')
    lines += ['','![Fixed operations](figures/operation-comparison.svg)','',
      'Local E2B removes much of the remote cost for file operations, but the current exec path still has a substantial startup cost. One tmux no-op makes four exec calls plus one file write, magnifying that cost. Local deployment alone does not remove the adapter\'s call sequence.','',
      '## Why local E2B exec can still be slow','',
      'E2B Python SDK 2.49.1 implements `commands.run()` using `/bin/bash -l -c`. Our provider maps Uni-Agent argv execution onto that SDK call. The official E2B template provisioning script adds shell-profile sourcing, and the SWE image contains Conda initialization. A local network therefore does not guarantee a cheap one-shot command.','']
    if probe.get('success'):
        lines += [f'After the nine main runs, we timed Bash startup **inside one disposable local E2B sandbox**, with no network round trip between measurements. Ten login-shell runs had median **{probe["median_s"]["login"]*1000:.1f} ms**; ten `--noprofile --norc` runs had median **{probe["median_s"]["plain"]*1000:.1f} ms**. This isolates login initialization overhead. The saved startup-script excerpts show Conda initialization, but this probe does not isolate the cost of each individual startup line. [Evidence](results/login-probe.json)','']
    else:lines += ['The login-startup diagnostic did not finish successfully; no isolated startup-cost claim is made. [Evidence](results/login-probe.json)','']
    for row in data['native_shell']:
        if row.get('success'):lines.append(f'- Persistent-stdin no-op prototype on **{labels[row["variant"]]}**: median **{row["median_s"]*1000:.1f} ms**, {row["n"]} commands, cwd/environment preserved.')
    lines += ['', 'The persistent-shell results are transport prototypes, not full SWE task runs with an optimized provider. The primary comparison uses the same tmux tool on all three deployments.','',
      '**Next implementation to evaluate:** initialize the shell environment once per sandbox/session; use a native persistent shell for agent commands and a native argv path for simple metadata operations. Preserve cwd, environment, timeouts, cancellation, and output semantics, then repeat the task comparison.','',
      '## Fairness and scope','',
      '- Six fresh baseline/gold controls passed qualification; initial repository HEAD and Git tree matched across all three deployments.',
      '- One pinned vLLM ROCm image and serving replica: BF16, TP1, AITER + graphs, 128K context, 16 active sequences, 8192 batched tokens, GPU memory fraction 0.65. The configured sandbox limits were 4 CPU / 8 GiB.',
      '- ReAct used 100 steps, up to 4096 new tokens per model call, temperature 0.2, top-p 0.9, and no explicit sampling seed. Every rollout used a separate fresh verifier and retained its token/mask/logprob audit.',
      '- Local E2B runs inside the dedicated KVM VM and is accessed through managed SSH tunnels. Remote E2B is the supplied compatible service; its internal implementation has not been identified. CPU, storage, kernel, and network placement differ.',
      '- Matching source images and repository trees do not imply identical full filesystems: E2B provisioning adds runtime components and shell setup. Fixed-operation timings include these provider semantics, not just wire latency.',
      '- Images/templates and model weights were already available. Cold download/build time, training updates, and multi-node scaling were not measured.',
      '- The study model was stopped afterward; the local E2B service and reusable templates remain available. Cleanup targeted only this study\'s instances.','',
      '## Evidence','',
      '[Per-run CSV](results/cases.csv) · [Tool calls](results/tool-calls.csv) · [Controls](results/controls-audit.json) · [Cleanup](results/cleanup-audit.json) · [Replay steps](STEPS.md)','',
      f'Full logs, spans, trajectories, and GPU telemetry remain at `{root}`. [Copied artifact hashes](results/source-manifest.json).','',
      '[Back to overview](../README.md)']
    (target/'README.md').write_text('\n'.join(lines)+'\n')
    steps='''# Replay the three-deployment comparison

The prepared lab requires the existing local E2B service, remote E2B credentials, the pinned SWE templates, and model weights. Secrets stay in `/home/yuhanya/uni-agent-lab/secrets/`.

## 1. Prepare a fresh run

```bash
cd /home/yuhanya/uni-agent-lab
THREEWAY_NAME="sandbox-three-way-$(date -u +%Y%m%dT%H%M%SZ)"
python3 scripts/sandbox_three_way/prepare.py --root "results/$THREEWAY_NAME"
```

This creates a new manifest and reuses the pinned templates. It starts or reuses `ua-threeway-model`; review GPU availability first. The model endpoint is `http://127.0.0.1:18087/v1` on the host.

## 2. Collect telemetry and run the pipeline

In a separate host terminal, with the same THREEWAY_NAME:

```bash
python3 scripts/swe_sandbox_profile/monitor.py \
  --root "results/$THREEWAY_NAME" --metrics-url http://127.0.0.1:18087/metrics \
  --output-name telemetry.jsonl --stop-file STOP_MONITOR
```

Run the experiment from the host:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/sandbox_three_way/pipeline.py --root "/lab/results/$THREEWAY_NAME"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/sandbox_three_way/login_probe.py --root "/lab/results/$THREEWAY_NAME"
```

The pipeline runs 300 fixed-operation samples, two persistent-shell probes, six controls, and nine serial agent rollouts. Provider order rotates between the three repetitions. The login probe runs afterward and leaves the sandbox image and profile files unchanged.

## 3. Analyze and clean up

```bash
python3 scripts/sandbox_three_way/analyze.py --root "results/$THREEWAY_NAME"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/sandbox_three_way/cleanup_and_audit.py --root "/lab/results/$THREEWAY_NAME"

touch "results/$THREEWAY_NAME/STOP_MONITOR"
docker stop ua-threeway-model
```

The analysis includes each rollout, component times, generated tokens, and tool-call counts. `figures.py` renders the figures with Matplotlib in the existing report environment. Keep the local E2B VM/API running for subsequent tasks.

`worker.py` reuses the existing profiled task and provider implementations. It changes credentials by deployment and sets a common 3,600-second sandbox lifetime. The original code at run start is preserved under `code/executed/`; current reporting helpers are under `code/`.
'''
    (target/'STEPS.md').write_text(steps)
    (target/'results/source-manifest.json').write_text(json.dumps({'run_root':str(root),'copied_files':records},indent=2)+'\n')
    print(json.dumps({'published':str(target),'artifacts':len(records)}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);main(p.parse_args().root)
