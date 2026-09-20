import json,re,shutil,time
from pathlib import Path
from verl.trainer.ppo.checkpoint_callback import CheckpointCallback
from .common import ROOT,save

class KeepLatest(CheckpointCallback):
    def on_save(self,trainer,global_step,checkpoint_dir,async_save=False,**kwargs):
        assert not async_save,'Retention requires a completed synchronous checkpoint'
        current=Path(checkpoint_dir).resolve()
        assert (current/'actor/ckpt_contents.json').exists(),'Incomplete checkpoint manifest'
        removed=[]
        for old in current.parent.glob('global_step_*'):
            match=re.fullmatch(r'global_step_(\d+)',old.name)
            if match and int(match[1])<global_step and old.is_dir() and not old.is_symlink():
                shutil.rmtree(old);removed.append(old.name)
        save(ROOT/'results'/f'checkpoint-{global_step}.json',{'global_step':global_step,'completed_unix':time.time(),'directory':str(current),'manifest':json.loads((current/'actor/ckpt_contents.json').read_text()),'older_directories_removed':removed})
