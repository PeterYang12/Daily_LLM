#!/usr/bin/env python3
"""Read-only acceptance of the exact initial R2 checkpoint-1 hold; never resumes or edits runtime files."""
from __future__ import annotations
import argparse,contextlib,fcntl,hashlib,importlib.util,json,math,os,sys,time,uuid
from pathlib import Path

DEFAULT_ROOT=Path('/data2/yuhanya/e2b-rl-pathfinding-20260920-r2')
RUN_ID='ua-e2b-pathfinding-20260920-r2'
SESSION_ID='20260920T184935Z-3c9aeb9d'
ATTEMPT_ID=SESSION_ID+'-attempt-01'
OBSERVER_SHA='881992549cf840989b5c36704a244382000d657b606c3a8ce4e3ee983fdd93ef'


class Rejected(RuntimeError):pass


def need(condition,message):
    if not condition:raise Rejected(message)


def read(path):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError) as exc:raise Rejected('Missing or invalid JSON: '+str(path)) from exc


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(2**20),b''):digest.update(block)
    return digest.hexdigest()


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def digest_string(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


@contextlib.contextmanager
def quiescent_locks(root):
    """Read existing lock files only; never wait for or start a trainer."""
    with contextlib.ExitStack() as stack:
        for relative in ('results/supervision/supervisor.lock','results/trainer.lock'):
            stream=stack.enter_context((root/relative).open('rb'))
            try:fcntl.flock(stream,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise Rejected('Run still holds '+relative) from exc
        yield


def file_record(root,path,prefix=False):
    path=Path(path).resolve();need(path.is_relative_to(root),'Evidence path leaves R2 root')
    result={'path':str(path.relative_to(root)),'sha256':sha(path),'bytes':path.stat().st_size}
    if prefix:
        with path.open('rb') as stream:
            if result['bytes']:stream.seek(-1,os.SEEK_END);need(stream.read(1)==b'\n','Append-only prefix lacks complete newline: '+str(path))
        result['verification_after_resume']='Compare exactly the first bytes bytes; preserve later appended rows separately'
    return result


def exited(pid,ticks):
    need(type(pid) is int and pid>0 and str(ticks).isdigit(),'Missing prior process identity')
    try:fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
    except FileNotFoundError:return
    need(fields[19]!=str(ticks) or fields[0] in ('Z','X'),'Prior host supervisor/Docker client is still live')


def validate_observer_proof(path):
    proof=read(path)
    need(proof.get('applied') is True and not proof.get('errors'),'Observer patch was not successfully applied')
    need(proof.get('namespace')==RUN_ID and proof.get('attempt_id')==ATTEMPT_ID,'Observer patch owner differs')
    need(proof.get('replacement_source_sha256')==OBSERVER_SHA,'Observer helper differs from reviewed source')
    rows=proof.get('patch_acknowledgements',[])
    need(len(rows)==8 and {r.get('rank') for r in rows}==set(range(8)),'Observer patch lacks eight distinct rank acknowledgements')
    need(len({r.get('actor_id') for r in rows})==8 and all(r.get('actor_id') for r in rows),'Observer actor identities are missing or repeated')
    workers=proof.get('workers',[])
    need(len(workers)==8 and {r.get('rank') for r in workers}==set(range(8)),'Observer discovery lacks eight distinct ranks')
    workers={row['rank']:row for row in workers}
    for row in rows:
        need(row.get('applied') is True and row.get('replacement_code_equal_after') is True,'Observer code equality/apply acknowledgement failed')
        need(row.get('other_module_bindings_changed')==[],'Observer patch changed an additional module binding')
        need(row.get('run_id')==RUN_ID and row.get('attempt_id')==ATTEMPT_ID and row.get('namespace')==RUN_ID,'Observer rank belongs to another run/attempt')
        need(row.get('experiment_root')=='/experiment' and row.get('is_actor') is True,'Observer target is not the expected actor engine')
        need(row.get('replacement_source_sha256')==OBSERVER_SHA,'Observer rank has a different replacement source')
        need(row.get('world_size')==8 and row.get('role') in ('actor_rollout','actor_rollout_ref'),'Observer actor role/world size differs')
        need(type(row.get('pid')) is int and row['pid']>0 and str(row.get('pid_start_ticks')).isdigit(),'Observer process identity is missing')
        need(row.get('job_id') and row.get('node_id'),'Observer Ray identity is missing')
        identity=('rank','actor_id','pid','pid_start_ticks','job_id','node_id','namespace','run_id','attempt_id')
        need(all(row.get(key)==workers[row['rank']].get(key) for key in identity),'Observer acknowledgement differs from discovered worker')
        need(row.get('patch_scope')=='runtime_observe._optimizer_checkpoint_scalars binding only' and
             row.get('model_optimizer_device_methods_called') is False and row.get('live_source_file_modified') is False,
             'Observer acknowledgement exceeds the reviewed scope')
        need(row.get('frozen_code_equal_before') is True or row.get('replacement_code_equal_before') is True,
             'Observer did not recognize the prior helper')
    # Raw marshal hashes may differ for equal code objects: record them, do not use them as equality tests.
    return proof


def runtime_path(root,value):
    need(isinstance(value,str),'Expected a single local dataset path')
    path=Path(value)
    if path.is_relative_to('/experiment'):path=root/path.relative_to('/experiment')
    path=path.resolve();need(path.is_relative_to(root),'Dataset path leaves R2')
    return path


def validate(root,api,observer_path=None):
    root=Path(root).resolve();need(api.RUN_ID==RUN_ID,'Wrong supervisor implementation/run identity')
    directory=root/'results/supervision'/SESSION_ID
    status_path=directory/'status.json';status=read(status_path)
    need(status.get('run_id')==RUN_ID and status.get('session_id')==SESSION_ID,'Wrong initial R2 supervisor')
    need(status.get('state')=='paused' and status.get('exit_code')==0 and status.get('ended_utc'),'Initial R2 supervisor has not finished a clean paused exit')
    attempts=status.get('attempts',[])
    need(len(attempts)==1 and attempts[0].get('attempt_id')==ATTEMPT_ID,'Initial hold contains unexpected attempts')
    attempt=attempts[0]
    need(attempt.get('exit_code')==0 and number(attempt.get('ended_unix')),'Initial Docker training attempt has not exited zero')
    need(attempt.get('kind')=='checkpoint1_acceptance' and number(attempt.get('started_unix')) and
         attempt['started_unix']<=attempt['ended_unix'],'Invalid original acceptance attempt window')
    exited(status.get('supervisor_pid'),status.get('supervisor_start_ticks'))
    exited(attempt.get('docker_client_pid'),attempt.get('docker_client_start_ticks'))
    attempt_dir=directory/'attempt-01'
    outcome_path=attempt_dir/'trainer-outcome.json';outcome=read(outcome_path)
    need(outcome.get('run_id')==RUN_ID and outcome.get('supervisor_attempt_id')==ATTEMPT_ID,'Wrong native trainer outcome owner')
    need(outcome.get('status')=='paused_after_checkpoint' and outcome.get('exit_code')==0,'Native trainer did not finish its checkpoint pause')
    need(outcome.get('paused_checkpoint',{}).get('global_step')==1,'Native trainer paused at a different checkpoint')
    cleanup_path=attempt_dir/'cleanup.json';cleanup=read(cleanup_path)
    need(cleanup.get('remaining')==[] and cleanup.get('errors')==[],'Owned sandbox cleanup is incomplete')
    need(cleanup.get('supervisor_attempt_id')==ATTEMPT_ID,'Cleanup acknowledgement belongs to another attempt')
    checkpoint=api.checkpoint(root,1)
    need(checkpoint.get('run_id')==RUN_ID and checkpoint.get('global_step')==1 and checkpoint.get('complete') is True,
         'Native checkpoint inspection does not identify complete R2 step1')
    need(outcome['paused_checkpoint'].get('native_manifest_sha256')==checkpoint['native_manifest_sha256'],
         'Trainer outcome and saved checkpoint manifest differ')
    metrics=api.metric_snapshot(root)
    need(metrics['observed_step_numbers']==[1] and not metrics['duplicate_step_numbers'],'Expected exactly native training step1, with no duplicates')
    source_snapshot_path=attempt_dir/'source-and-config.json';source_snapshot=read(source_snapshot_path)
    need(source_snapshot.get('attempt_id')==ATTEMPT_ID,'Wrong baseline source/config snapshot')
    need(api.source_hashes(root)==source_snapshot['source_sha256'],'Frozen source files changed before reviewed persistence')
    immutable=[status_path,outcome_path,cleanup_path,source_snapshot_path]
    for name in ('train.yaml','task_config.yaml','training-plan.json','source-provenance.json'):
        archive=attempt_dir/name;live=root/'configs'/name;expected=source_snapshot['config_sha256'].get('configs/'+name)
        need(expected and sha(archive)==expected and sha(live)==expected,'Archived/current baseline config differs: '+name)
        immutable.append(archive)
    for name in ('datasets-qualified.json','datasets-template-map.json','supervisor-prerequisites.json'):
        path=root/'configs'/name;expected=source_snapshot['config_sha256'].get('configs/'+name)
        need(expected and sha(path)==expected,'Frozen data/template/review manifest differs: '+name)
        immutable.append(path)
    resolved_path=root/'results/resolved-config.json';resolved=read(resolved_path)
    datasets={}
    for split,key in [('train','train_files'),('validation','val_files')]:
        path=runtime_path(root,resolved['data'][key]);expected=outcome.get('data',{}).get(split,{}).get('sha256')
        need(expected and sha(path)==expected,'Dataset bytes changed since launch: '+split)
        datasets[split]=file_record(root,path)
    if observer_path is None:
        candidates=[]
        for path in (root/'results/operator/checkpoint-scalar-compat').glob('apply-*.json'):
            if read(path).get('applied') is True:candidates.append(path)
        need(len(candidates)==1,'Select exactly one successful observer proof with --observer-proof')
        observer_path=candidates[0]
    observer_path=Path(observer_path).resolve();need(observer_path.is_relative_to(root),'Observer proof leaves R2')
    observer_proof=validate_observer_proof(observer_path);immutable.append(observer_path)
    need(number(observer_proof.get('time_unix')) and attempt['started_unix']<=observer_proof['time_unix']<=attempt['ended_unix'],
         'Observer apply proof is outside the original attempt window')
    saved=[]
    for rank in range(8):
        path=root/f'results/reload-proof/save/rank-{rank}.json';row=read(path)
        need(row.get('phase')=='save' and row.get('global_step')==1 and row.get('rank')==rank,'Invalid saved rank proof identity')
        need(row.get('supervisor_attempt_id')==ATTEMPT_ID,'Saved rank proof owner differs')
        need(number(row.get('time_unix')) and observer_proof['time_unix']<=row['time_unix']<=attempt['ended_unix'],'Saved proof predates the observer patch or is outside its attempt window')
        samples=row.get('model_probes',[])
        need(isinstance(samples,list) and 1<=len(samples)<=3 and all(
            isinstance(item.get('name'),str) and item['name'] and type(item.get('sample_count')) is int and
            0<item['sample_count']<=256 and digest_string(item.get('sha256_float32')) for item in samples),
            'Missing, invalid or unbounded model samples')
        scheduler=row.get('scheduler_scalars',{})
        need(number(scheduler.get('num_steps')) and scheduler['num_steps']>0,'Saved scheduler has no advanced native counter')
        counters=row.get('optimizer',{}).get('step_counters',{})
        need(counters and all(number(v) and v>=0 for v in counters.values()) and any(v>0 for v in counters.values()),'Saved optimizer counters are unavailable or unadvanced')
        saved.append({'rank':rank,'file':file_record(root,path),'model_probes':samples,
                      'optimizer':row['optimizer'],'scheduler_scalars':scheduler})
        immutable.append(path)
    optimizer=api.optimizer_audit(root,[1],{ATTEMPT_ID})
    for row in api.jsonlines(root/'results/train-metrics.jsonl'):
        need(type(row.get('step')) is int and row['step'] in (0,1),'Unexpected metric row before manual resume')
        native_step=row.get('data',{}).get('training/global_step')
        need(native_step is None or type(native_step) is int and native_step==row['step']==1,'Metric/native step differs')
    append_only=[file_record(root,root/'results/train-metrics.jsonl',True)]
    for rank in range(8):
        path=root/f'results/optimizer/rank-{rank}.jsonl'
        for index,row in enumerate(api.jsonlines(path),1):
            need(row.get('supervisor_attempt_id')==ATTEMPT_ID and row.get('global_step')==1 and row.get('rank')==rank,
                 'Unexpected optimizer owner/step/rank before manual resume')
            need(row.get('optimizer_update')==index and number(row.get('time_unix')) and
                 attempt['started_unix']<=row['time_unix']<=attempt['ended_unix'],'Invalid optimizer counter or attempt window')
        append_only.append(file_record(root,path,True))
    evaluations=api.validate_evaluations(root,final=False)
    eval_files=[]
    for path in (root/'results/sessions').glob('*/result.json'):
        row=read(path)
        if row.get('phase')!='validation':continue
        need(row.get('global_step')==0 and row.get('supervisor_attempt_id')==ATTEMPT_ID,'Unexpected validation round/owner before resume')
        eval_files.append(path)
    need(len(eval_files)==16,'Initial R2 hold does not retain exactly16 baseline evaluation episodes')
    immutable.extend(eval_files);immutable.append(root/'results/validation/0.jsonl')
    budgets={}
    for kind in ('train_prompts','validation_episodes'):
        path=root/f'results/budgets/{kind}.json';row=read(path)
        need(type(row.get('submitted')) is int and row['submitted']>=0 and type(row.get('limit')) is int and row['limit']>0 and
             all(type(v) is int and v>=0 for v in row.get('attempted_by_reason',{}).values()),'Invalid budget counters: '+kind)
        need(row.get('run_id')==RUN_ID and row.get('submitted')==sum(row.get('attempted_by_reason',{}).values()),'Budget accounting mismatch: '+kind)
        need(row['submitted']<=row['limit'],'Budget exceeded: '+kind)
        budgets[kind]={'file':file_record(root,path),'snapshot':row}
    need(budgets['validation_episodes']['snapshot']['submitted']==16 and budgets['validation_episodes']['snapshot']['limit']==80,'Validation budget must retain16 of80')
    train_budget=budgets['train_prompts']['snapshot']
    need(train_budget['submitted']>=32 and train_budget['limit']==3520 and train_budget.get('planned_training_prompts')==3200,
         'Training budget differs from the original run contract')
    stop_path=root/'results/stop-after-checkpoint.request';stop=read(stop_path)
    need(stop.get('after_step')==1 and stop.get('supervisor_session')==SESSION_ID,'Stop request does not belong to this exact checkpoint hold')
    stop_evidence={'file':file_record(root,stop_path),'snapshot':stop,
                   'after_capture':'May be removed only as part of the reviewed resume; this snapshot preserves the request'}
    tiny_checkpoint={}
    for path in (root/'checkpoints/global_step_1/actor/ckpt_contents.json',root/'checkpoints/global_step_1/checkpoint-record.json',root/'checkpoints/latest_checkpointed_iteration.txt'):
        tiny_checkpoint[str(path.relative_to(root))]={'sha256':sha(path),'text':path.read_text()}
    return {'schema_version':1,'checkpoint1_prefix_valid':True,'run_id':RUN_ID,'baseline_session_id':SESSION_ID,
        'baseline_attempt_id':ATTEMPT_ID,'checked_unix':time.time(),'checkpoint':checkpoint,
        'metrics':{'observed_step_numbers':[1],'duplicates':[]},'optimizer_audit':optimizer,'saved_rank_proofs':saved,
        'evaluations':evaluations,'immutable_evidence':[file_record(root,path) for path in immutable],
        'append_only_prefixes':append_only,'dataset_evidence':datasets,'budget_snapshots':budgets,
        'archived_stop_request':stop_evidence,
        'baseline_source_sha256':source_snapshot['source_sha256'],'baseline_config_sha256':source_snapshot['config_sha256'],
        'baseline_resolved_config':resolved,'baseline_resolved_config_file':file_record(root,resolved_path),
        'archived_checkpoint_metadata':tiny_checkpoint,
        'checkpoint_dataloader':file_record(root,root/'checkpoints/global_step_1/data.pt'),
        'observer_proof':{'file':file_record(root,observer_path),'replacement_source_sha256':OBSERVER_SHA,'ranks':8},
        'resume_requirements':{'actor.optim.use_checkpoint_opt_param_scheduler':True,
            'native_load_contents':['model','optimizer','extra'],'persist_identical_safe_observer':True,
            'fresh_scheduler_then_one_native_load':True,'append_metrics_and_preserve_budgets':True,
            'do_not_rerun_evaluation0':True,'preserve_exact_baseline_owner_prefix':True,
            'review_source_config_changes_before_resume':True},
        'limits':['This accepts the saved prefix, not the resumed run. No model/GPU/network operation is performed.',
                  'Native metric rows do not carry attempt IDs; their exact byte prefix is attributed to the single clean original attempt.',
                  'Checkpoint inspection validates native manifest and payload presence/size; actual reload still needs eight matching rank proofs.',
                  'Large checkpoint1 payloads may later be pruned after a verified reload; this audit retains small metadata only.',
                  'A separate operator check must confirm no residual owned container processes before restarting.']}


def write_new(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(payload,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.link(temp,path)  # atomic no-overwrite publication
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:temp.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--observer-proof',type=Path);p.add_argument('--output',type=Path,
        help='Optional new private audit JSON; written only after every check passes, never overwrites')
    args=p.parse_args();root=args.root.resolve();sys.path.insert(0,str(root/'code'))
    sys.dont_write_bytecode=True
    try:
        with quiescent_locks(root):
            snapshot=read(root/'results/supervision'/SESSION_ID/'attempt-01/source-and-config.json')
            for name in ('supervise_run.py','verify_checkpoint.py','runtime_common.py'):
                need(sha(root/'code'/name)==snapshot['source_sha256'].get('code/'+name),'Frozen checker dependency changed: '+name)
            spec=importlib.util.spec_from_file_location('checkpoint_prefix_supervisor_api',root/'code/supervise_run.py')
            api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
            result=validate(root,api,args.observer_proof)
            if args.output:
                destination=args.output.resolve()
                need(not destination.is_relative_to(root) or destination.is_relative_to(root/'results/operator'),
                     'New audit output inside R2 must be under results/operator')
                write_new(destination,result)
        print(json.dumps({'checkpoint1_prefix_valid':True,'run_id':RUN_ID,'baseline_attempt_id':ATTEMPT_ID,
            'native_steps':1,'saved_rank_proofs':8,'baseline_evaluation':result['evaluations'],
            'resume_still_requires_review':True,'output':str(args.output) if args.output else None}))
        return 0
    except Exception as exc:
        print(json.dumps({'checkpoint1_prefix_valid':False,'error_type':type(exc).__name__,'reason':str(exc),
                          'output_written':False}));return 2


if __name__=='__main__':raise SystemExit(main())
