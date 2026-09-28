"""English executive summary: two slides, conclusions first, minimal labels."""
import json
from pathlib import Path
import pandas as pd
from build_slides import ROOT, OUT, W, H, C, Scene, render, pptx

STEM='Uni-Agent_Executive_Summary_EN'


def header(s,title,subtitle,page):
    s.rect(0,0,W,H,C['white'])
    s.rect(42,30,5,30,C['blue'],2)
    s.text(title,58,28,866,28,bold=True)
    s.text(subtitle,58,76,850,13,C['muted'])
    s.text(f'{page} / 2',871,514,46,10,C['muted'],align='right')


def main():
    summary=json.loads((ROOT/'data/summary.json').read_text())
    ep=pd.read_csv(ROOT/'data/episodes.csv')
    batch=summary['batches']['1'];metric=summary['step1_metrics']
    group=ep[(ep.batch==1)&(ep.instance_id=='agronholm__anyio-639')]
    slow=group.loc[group.episode_s.idxmax()];fast=group.loc[group.episode_s.idxmin()]
    rollout=metric['timing_s/gen'];step=metric['timing_s/step'];training=step-rollout

    a=Scene()
    header(a,'Rollouts take 84% of the training step',
           'Pilot run  |  16 tasks × 8 attempts = 128 rollouts  |  35.3 min total',1)
    # Authoritative two-way wall time. Grading is explicitly nested in rollout.
    barx,bary,barw,barh=44,122,872,108
    cut=barw*rollout/step
    a.rect(barx,bary,cut,barh,C['blue'],0)
    a.rect(barx+cut,bary,barw-cut,barh,C['purple'],0)
    a.text('84%',64,133,208,46,C['white'],bold=True)
    a.text('Rollout',239,142,282,25,C['white'],bold=True)
    a.text('29.6 min · includes verification',239,182,491,17,C['white'])
    a.text('16%',barx+cut+12,133,barw-cut-24,36,C['white'],bold=True,align='center')
    a.text('Training',barx+cut+8,178,barw-cut-16,17,C['white'],align='center')
    a.text('5.7 min',barx+cut+8,202,barw-cut-16,13,C['white'],align='center')
    a.text('Final verification: only 16 seconds (<1% of the step)',44,245,872,17,C['green'],bold=True)

    a.rect(44,296,415,180,C['bg'],7)
    a.rect(481,296,435,180,C['bg'],7)
    a.text('WAITING FOR THE LAST FEW',64,313,377,13,C['muted'],bold=True)
    a.text('9.4 min',64,343,376,45,C['red'],bold=True)
    a.text('after 90% of attempts finish',64,408,375,18)
    a.text('32% of rollout time',64,440,375,16,C['red'],bold=True)

    a.text('SAME TASK. 8 ATTEMPTS.',501,313,396,13,C['muted'],bold=True)
    a.text('Fastest',501,349,92,15,C['muted'])
    a.text('Slowest',501,389,92,15,C['red'])
    x,w=593,215
    a.rect(x,353,w*fast.episode_s/slow.episode_s,18,C['pale'],2)
    a.rect(x,393,w,18,C['red'],2)
    a.text(f'{fast.episode_s/60:.1f}m',x+w*fast.episode_s/slow.episode_s+10,350,75,16,C['muted'])
    a.text(f'{slow.episode_s/60:.1f}m',x+w+10,390,83,16,C['red'],bold=True)
    a.text(f'{slow.episode_s/fast.episode_s:.1f}× runtime',501,437,388,22,C['red'],bold=True)
    a.text('Measured on completed step 1. Training includes synchronization. Task example: AnyIO #639.',44,515,800,9,C['muted'])

    b=Scene()
    header(b,'Faster startup. Less waiting between batches.',
           'Where to focus the next run',2)
    # Single, simple attempt sequence: no API method names or fine-grained timings.
    b.text('ONE SLOW ATTEMPT',44,121,380,13,C['muted'],bold=True)
    boxw=270
    for x,value,label,color in [(44,'~2 min','Start environment',C['purple']),
                               (345,'~27 min','Agent works',C['blue']),
                               (646,'16 sec','Verify result',C['green'])]:
        b.rect(x,151,boxw,98,C['bg'],6)
        b.text(value,x+16,167,boxw-32,33,color,bold=True,align='center')
        b.text(label,x+12,216,boxw-24,16,C['muted'],align='center')
    b.text('→',316,180,27,24,C['muted'],align='center')
    b.text('→',617,180,27,24,C['muted'],align='center')

    b.text('STARTUP BOTTLENECK',44,288,430,13,C['muted'],bold=True)
    b.text('6 min',44,317,386,48,C['purple'],bold=True)
    b.text('to create 128 sandboxes',44,382,406,21)
    b.text('Client allows 3 startups at once',44,418,402,17,C['muted'])

    b.line(480,295,480,448,C['line'])
    b.text('NEXT ACTIONS',518,288,394,13,C['muted'],bold=True)
    b.rect(518,327,5,40,C['purple'],2)
    b.text('Preinstall tools',537,326,374,23,bold=True)
    b.text('Reduce startup queues',537,358,374,15,C['muted'])
    b.rect(518,395,5,40,C['blue'],2)
    b.text('Overlap batches',537,394,374,23,bold=True)
    b.text('Keep GPUs busy during long tails',537,426,374,15,C['muted'])

    b.rect(44,477,872,28,'FFF1EB',3)
    b.text('Reliability first: GPU fault stopped step 2; no trained checkpoint saved.',57,483,850,12,C['red'],bold=True)
    b.text('Startup includes client queuing. Actions are proposed improvements, not measured speedups.',44,517,810,9,C['muted'])

    scenes=[a,b]
    render(scenes,STEM,'executive-en')
    pptx(scenes,STEM,'en-US')
    evidence={'scope':'Completed step 1 and batch 1',
              'step_s':step,'rollout_including_verification_s':rollout,'training_sync_s':training,
              'final_verifier_s':float(slow.verifier_s),
              'accounting':'84% rollout includes verification. <1% is the final non-overlapped verifier tail, not an additional percentage. Earlier verification overlaps continuing agent attempts.',
              'after90_remaining_s':batch['tail_after_90pct_s'],
              'example':{'task':slow.instance_id,'fastest_s':float(fast.episode_s),'slowest_s':float(slow.episode_s),
                         'startup_s':float(slow.agent_start-slow.task_start),'agent_loop_s':float(slow.agent_s),'verifier_s':float(slow.verifier_s)},
              'creation_ramp_s':batch['creation_ramp_s'],
              'rendering':'Editable native PPTX shapes; PDF and PNG use the same scene. No PowerPoint renderer is installed.'}
    (OUT/'executive-en-data.json').write_text(json.dumps(evidence,indent=2)+'\n')
    (OUT/'executive-en-scene.json').write_text(json.dumps([s.items for s in scenes],ensure_ascii=False)+'\n')
    print(json.dumps({'pptx':str(OUT/(STEM+'.pptx')),'slides':2,
                      'words_per_slide':[len(' '.join(o['text'] for o in s.items if o['kind']=='text').split()) for s in scenes]},indent=2))


if __name__=='__main__':main()
