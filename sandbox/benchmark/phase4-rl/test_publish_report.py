"""CPU-only publication checks; no watcher, GPU, service or external API is started."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import hashlib
import csv

SPEC=importlib.util.spec_from_file_location('publisher',Path(__file__).with_name('publish_report.py'))
publisher=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ua-publisher-test-')
        self.root=Path(self.tmp.name)/'run';self.benchmark=Path(self.tmp.name)/'benchmark'
        self.run_id='ua-e2b-pathfinding-20260920'
        self.put('configs/training-plan.json',{'run_id':self.run_id,'training_steps':100,
            'train_rows':62,'validation_rows':16,'separate_create_gate':'0','private_ignored':'PRIVATE_CONFIG_SENTINEL'})
        self.status_path=self.root/'results/supervision/test/status.json'
        self.status={'state':'acceptance_running','requested_native_global_steps':100,
            'supervisor_pid':os.getpid(),'supervisor_start_ticks':Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()[19],
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'heartbeat_unix':publisher.time.time(),
            'heartbeat_utc':publisher.utc(),'progress':{'completed_native_global_step':0},
            'error':'API_KEY=PRIVATE_ERROR_SENTINEL','attempts':[]}
        self.put('results/supervision/test/status.json',self.status)
        self.put('results/supervision/latest.json',{'status_file':str(self.status_path)})

    def tearDown(self):self.tmp.cleanup()

    def put(self,relative,data):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data));return path

    def metrics(self,rows,tail=''):
        path=self.root/'results/train-metrics.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows)+tail)

    def test_running_snapshot_never_claims_training_complete_and_has_public_mode(self):
        old=os.umask(0o077)
        try:result=publisher.publish(self.root,self.benchmark)
        finally:os.umask(old)
        report=self.benchmark/'phase4-rl/REPORT.md';text=report.read_text()
        self.assertIn('**0/100 recorded**',text.splitlines()[0])
        self.assertIn('acceptance_running',text.splitlines()[0])
        self.assertIn('has not yet passed final acceptance',text)
        self.assertNotIn('PRIVATE_CONFIG_SENTINEL',text);self.assertNotIn('PRIVATE_ERROR_SENTINEL',text)
        self.assertEqual(report.stat().st_mode&0o777,0o644)
        self.assertEqual((self.benchmark/'phase4-rl/results').stat().st_mode&0o777,0o755)
        self.assertTrue(result['validation']['output_hashes_match'])
        self.assertEqual(result['wandb'],'pending')

    def test_only_complete_native_steps_and_exact_phase_profile_are_used(self):
        self.metrics([{'step':0,'data':{'val/reward':1}},
            {'step':1,'data':{'training/global_step':1,'timing_s/step':10,'timing_s/gen':6,
                             'timing_s/update_actor':2,'timing_s/update_weights':1,'timing_s/save_checkpoint':1,
                             'critic/rewards/mean':.25,'ignored_payload':'PRIVATE_TASK_SENTINEL'}}],tail='{partial')
        analysis=self.put('results/analysis/summary.json',{'phase_filter':'phase5-startup-gate',
            'operation_summary':[{'operation':'sdk_create','p50_s':999999}]})
        self.put('results/supervision/latest-analysis.json',{'output':str(analysis.parent),'exit_code':0})
        result=publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertEqual(result['recorded_steps'],1)
        self.assertNotIn('PRIVATE_TASK_SENTINEL',text);self.assertNotIn('999999',text)
        self.assertIn('train-filtered profile summary is pending',text)
        self.assertIn('| 1 | 10.00 | 6.00 | 2.00 | 1.00 | 1.00 | 0.2500 |',text)
        self.assertGreater(result['quality_issue_count'],0)

    def test_completed_state_needs_checkpoint_and_final_acceptance(self):
        self.status.update(state='completed',progress={'completed_native_global_step':100})
        self.put('results/supervision/test/status.json',self.status)
        self.metrics([{'step':i,'data':{'training/global_step':i,'timing_s/step':1}} for i in range(1,101)])
        result=publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertIn('completion_unverified',text.splitlines()[0])
        self.put('results/latest-checkpoint.json',{'global_step':100})
        self.status['final_acceptance']={'native_training_steps':100}
        self.put('results/supervision/test/status.json',self.status)
        publisher.publish(self.root,self.benchmark)
        self.assertIn('The supervisor accepted', (self.benchmark/'phase4-rl/REPORT.md').read_text())

    def test_validation_text_is_not_exported(self):
        path=self.root/'results/validation/0.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps({'step':0,'score':i%2,'uid':str(i),
            'input':'PRIVATE_INPUT_SENTINEL','output':'PRIVATE_OUTPUT_SENTINEL'})+'\n' for i in range(16)))
        publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertIn('| 0 | 8 / 16 | Native dump complete; supervisor join pending |',text)
        self.assertNotIn('PRIVATE_INPUT_SENTINEL',text);self.assertNotIn('PRIVATE_OUTPUT_SENTINEL',text)

    def test_wandb_url_requires_initialization_evidence(self):
        artifact=self.root/f'results/wandb/run-test-{self.run_id}/run-{self.run_id}.wandb'
        artifact.parent.mkdir(parents=True);artifact.write_bytes(b'native-local-run-artifact')
        directory=self.root/'results/supervision/test/attempt-01';directory.mkdir(parents=True)
        url=f'https://wandb.ai/yuhanya-amd/e2b-rl-pathfinding/runs/{self.run_id}'
        (directory/'trainer-current.log').write_text('PRIVATE_LOG_SENTINEL\nwandb: View run at '+url+'\n')
        self.status['attempts']=[{'directory':str(directory)}]
        self.put('results/supervision/test/status.json',self.status)
        result=publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertEqual(result['wandb'],'initialized');self.assertIn(url,text)
        self.assertNotIn('PRIVATE_LOG_SENTINEL',text)
        self.assertEqual((self.root/'results/report-publisher/wandb-initialized.json').stat().st_mode&0o777,0o600)

    def test_r2_reuses_hash_checked_pretraining_without_original_folders(self):
        value={'episodes':2,'actions':5,'statuses':{'replayed':1,'observation_divergence':1}}
        path=self.put('results/pretraining-review/phase3.json',value)
        self.put('configs/supervisor-prerequisites.json',{'evidence':{'phase3':{
            'path':str(path),'review_status':'accepted','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'original_root':str(self.root/'missing-original')}}})
        public=self.benchmark/'phase3-replay/results/episodes.csv';public.parent.mkdir(parents=True)
        with public.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['status','planned_actions','executed_actions']);writer.writeheader()
            writer.writerows([{'status':'replayed','planned_actions':2,'executed_actions':2},
                             {'status':'observation_divergence','planned_actions':3,'executed_actions':1}])
        result=publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertIn('hash-checked R1 evidence',text);self.assertIn('executed 3 of 5 recorded actions',text)
        self.assertEqual(result['recorded_steps'],0)
        value['episodes']=999;path.write_text(json.dumps(value))
        result=publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertNotIn('999 older 8B',text);self.assertGreater(result['quality_issue_count'],0)

    def test_r2_baseline_progress_and_r1_history_stay_separate(self):
        self.status['attempts']=[{'attempt_id':'r2-attempt'}]
        self.put('results/supervision/test/status.json',self.status)
        for i in range(3):
            self.put(f'results/sessions/current-{i}/result.json',{'phase':'validation','global_step':0,
                'status':'completed','instance_id':f'current-{i}','session_id':f's-{i}','supervisor_attempt_id':'r2-attempt'})
        self.put('results/sessions/foreign/result.json',{'phase':'validation','global_step':0,
            'status':'completed','instance_id':'foreign','supervisor_attempt_id':'r1-attempt'})
        fix=self.benchmark/'phase5-optimization/GATE-CANCELLATION.md';fix.parent.mkdir(parents=True);fix.write_text('Fix evidence')
        self.put('configs/previous-attempts.json',{'previous_attempts':[{'run_id':self.run_id,
            'native_steps_completed':0,'checkpoint':None,'training_results_at_stall':85,'training_results_final':86,
            'startup_create_cancellations':185,'cancelled_exec_gate_waits':57,'fix_report':str(fix),
            'cause':'PRIVATE_CAUSE_SENTINEL'}]})
        publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertIn('**3/16 episodes completed**',text);self.assertIn('**0/100 recorded**',text)
        self.assertIn('85 at stall; 86 final',text);self.assertIn('185 startup/create cancellations',text)
        self.assertIn('57 cancelled exec-gate waits',text);self.assertNotIn('PRIVATE_CAUSE_SENTINEL',text)

    def test_wandb_link_uses_active_r2_run_id(self):
        old_id=self.run_id;self.run_id+='-r2'
        self.put('configs/training-plan.json',{'run_id':self.run_id,'training_steps':100,'train_rows':62,'validation_rows':16})
        old_url=f'https://wandb.ai/yuhanya-amd/e2b-rl-pathfinding/runs/{old_id}'
        self.put('results/report-publisher/wandb-initialized.json',{'run_id':old_id,'url':old_url})
        self.put('results/wandb-initialized.json',{'initialized':True,'run_id':self.run_id,'entity':'yuhanya-amd','project':'e2b-rl-pathfinding'})
        publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertIn(f'[W&B run]({old_url}-r2)',text)
        self.assertNotIn(f'[W&B run]({old_url})',text)

    def first_batch(self):
        self.run_id='ua-e2b-pathfinding-20260920-r2'
        self.put('configs/training-plan.json',{'run_id':self.run_id,'training_steps':100,'train_rows':62,'validation_rows':16})
        self.status['attempts']=[{'attempt_id':'original-r2-attempt'}]
        self.put('results/supervision/test/status.json',self.status)
        directory=self.benchmark/'phase4-rl/results/first-batch-r2';directory.mkdir(parents=True)
        path=directory/'first-batch.json'
        row={'run_id':self.run_id,'attempt_id':'original-r2-attempt','associated_training_step':1,
            'episodes':256,'prompt_groups':32,'rollouts_per_prompt':8,
            'collection_wall_seconds':3017.70720410347,'after_p95_wall_seconds':445.76763010025024,
            'after_p95_wall_fraction':445.76763010025024/3017.70720410347,
            'tail_threshold_rank':244,'tail_remaining_episode_count':12,'tail_host_samples':89,
            'tail_mean_low_busy_gpu_count':4.741573033707865,'low_busy_gpu_threshold_pct':5,'gpu_device_count':8,
            'ignored_private_payload':'PRIVATE_FIRST_BATCH_SENTINEL'}
        path.write_text(json.dumps(row))
        (directory/'first-batch-timeline.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        (self.benchmark/'phase4-rl/ATTEMPT-2-DIAGNOSTICS.md').write_text('R2 reviewed startup diagnosis')
        return path,row

    def accepted_prefix(self):
        proofs=[];sessions=[]
        for rank in range(8):
            path=self.put(f'results/reload-proof/save/rank-{rank}.json',{
                'phase':'save','global_step':1,'rank':rank,'supervisor_attempt_id':'original-r2-attempt'})
            proofs.append({'rank':rank,'file':{'path':str(path.relative_to(self.root)),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}})
        dump=self.root/'results/validation/0.jsonl';dump.parent.mkdir(parents=True,exist_ok=True)
        dump.write_text(''.join(json.dumps({'step':0,'score':int(i<6),'uid':f'baseline-{i}'})+'\n' for i in range(16)))
        for index in range(16):
            path=self.put(f'results/sessions/baseline-{index}/result.json',{
                'phase':'validation','global_step':0,'status':'completed','instance_id':f'task-{index}',
                'supervisor_attempt_id':'original-r2-attempt','reward':int(index<6)})
            sessions.append({'path':str(path.relative_to(self.root)),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
        return self.put('results/operator/checkpoint1-prefix-original.json',{
            'run_id':self.run_id,'checkpoint1_prefix_valid':True,'baseline_session_id':'original-r2-session',
            'baseline_attempt_id':'original-r2-attempt','saved_rank_proofs':proofs,'immutable_evidence':sessions,
            'evaluations':[{'global_step':0,'tasks':16,'resolved':6,'task_session_reward_join':'passed',
                            'sha256':hashlib.sha256(dump.read_bytes()).hexdigest()}]})

    def switch_to_resumed_supervisor(self):
        self.status.update(session_id='resumed-r2-session',state='running',
            attempts=[{'attempt_id':'resumed-r2-attempt','kind':'owned_resume'}],
            reload_acceptance=[{'rank':rank,'matched':True} for rank in range(8)])
        # This matches native manual resume: a new status file, with no inherited-prefix field.
        self.status.pop('baseline_prefix',None)
        self.status_path=self.put('results/supervision/resumed-r2-session/status.json',self.status)
        self.put('results/supervision/latest.json',{'session_id':'resumed-r2-session','status_file':str(self.status_path)})

    def test_r2_runner_curve_precedes_retained_findings_without_counting_native_step(self):
        self.first_batch()
        result=publisher.publish(self.root,self.benchmark)
        self.assertEqual(result['recorded_steps'],0)
        for relative in ('phase4-rl/REPORT.md','OVERNIGHT-20260920.md'):
            report=self.benchmark/relative;text=report.read_text()
            self.assertIn('**0/100 recorded**',text.splitlines()[0])
            self.assertLess(text.index('**R2 first batch.**'),text.index('**Three measured findings**'))
            self.assertIn('**256 rollouts**',text)
            self.assertIn('50.3 min',text);self.assertIn('7.4 min (14.8%',text)
            self.assertIn('**4.7 of 8 GPUs**',text);self.assertIn('**less than 5% device activity**',text)
            self.assertIn('52–59 s of a 60 s budget waiting for command permits',text);self.assertIn('all ten recovered',text)
            self.assertIn('first-batch-timeline.png)',text)
            self.assertNotIn('PRIVATE_FIRST_BATCH_SENTINEL',text)
        manifest=json.loads((self.benchmark/'phase4-rl/report-validation.json').read_text())
        self.assertEqual(manifest['first_batch_attempt_id'],'original-r2-attempt')
        self.assertTrue(any(p['path'].endswith('first-batch-timeline.png') for p in manifest['inputs']))

    def test_r2_runner_curve_rejects_foreign_owner_and_inconsistent_tail(self):
        path,row=self.first_batch()
        for key,value in [('run_id','other-run'),('attempt_id','other-attempt'),
                          ('after_p95_wall_fraction',.99),('gpu_device_count',16)]:
            path.write_text(json.dumps({**row,key:value}))
            result=publisher.publish(self.root,self.benchmark)
            text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
            with self.subTest(field=key):
                self.assertNotIn('**R2 first batch.**',text)
                self.assertEqual(result['recorded_steps'],0)

    def test_manual_supervisor_session_transition_retains_curve_and_accepted_eval0(self):
        self.first_batch();self.accepted_prefix()
        self.metrics([{'step':1,'data':{'training/global_step':1,'timing_s/step':3600}}])
        self.status.update(session_id='original-r2-session',state='paused')
        self.put('results/supervision/test/status.json',self.status)
        evidence=publisher.Evidence()
        publisher.publish(self.root,self.benchmark,evidence)
        self.assertIn('**R2 first batch.**',(self.benchmark/'phase4-rl/REPORT.md').read_text())
        self.switch_to_resumed_supervisor()
        result=publisher.publish(self.root,self.benchmark,evidence)
        self.assertEqual(result['recorded_steps'],1)
        for relative in ('phase4-rl/REPORT.md','OVERNIGHT-20260920.md'):
            text=(self.benchmark/relative).read_text()
            self.assertIn('**1/100 recorded**',text.splitlines()[0]);self.assertIn('**running**',text.splitlines()[0])
            self.assertLess(text.index('**R2 first batch.**'),text.index('**Three measured findings**'))
            self.assertIn('first-batch-timeline.png)',text)
            self.assertIn('| 0 | 6 / 16 | Checkpoint-1 prefix task/session/reward join passed; evidence unchanged |',text)
            self.assertNotIn('supervisor join pending',text)
            self.assertNotIn('| 25 |',text)
        manifest=json.loads((self.benchmark/'phase4-rl/report-validation.json').read_text())
        self.assertEqual(manifest['first_batch_attempt_id'],'original-r2-attempt')
        self.assertTrue(manifest['accepted_baseline']['evaluation0_verified'])
        self.assertEqual(manifest['quality_issues'],[])

    def test_accepted_history_rejects_changed_saved_proof_or_evaluation_sessions(self):
        self.first_batch();self.accepted_prefix();self.switch_to_resumed_supervisor()
        proof=self.root/'results/reload-proof/save/rank-0.json';original=proof.read_bytes()
        value=json.loads(original);value['supervisor_attempt_id']='foreign-attempt';proof.write_text(json.dumps(value))
        publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertNotIn('**R2 first batch.**',text)
        self.assertNotIn('Checkpoint-1 prefix task/session/reward join passed',text)
        proof.write_bytes(original)
        self.put('results/sessions/baseline-0/result.json',{'changed':'after-prefix-review'})
        publisher.publish(self.root,self.benchmark)
        text=(self.benchmark/'phase4-rl/REPORT.md').read_text()
        self.assertIn('**R2 first batch.**',text)
        self.assertIn('| 0 | 6 / 16 | Native dump complete; supervisor join pending |',text)
        self.assertNotIn('Checkpoint-1 prefix task/session/reward join passed',text)


if __name__=='__main__':unittest.main()
