"""Full-pipeline parity gate with local weights; timings are cold checks, not medians."""
import argparse
import fcntl
import gc
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import mlx.core as mx
import numpy as np
from PIL import Image
from experiments.benchmark_qwen21 import PROMPT, require_idle
from mlx_image import engine
from mlx_image.types import Job


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt-file',type=Path)
    parser.add_argument('--output',type=Path,default=Path('local/production-parity'))
    args=parser.parse_args()
    prompt=args.prompt_file.read_text().strip() if args.prompt_file else PROMPT
    args.output.mkdir(parents=True,exist_ok=True)
    require_idle('http://127.0.0.1:1924')
    with (Path.home()/'.local/share/mlx-manager/heavy.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require_idle('http://127.0.0.1:1924')
        snapshot=engine._snapshot(None)
        report=dict(scope='Full pipeline parity check; one cold run per mode, no warm-up; timings are not a benchmark median',width=1152,height=768,steps=20,seed=1977,cache='off',mlx=mx.__version__,snapshot_revision=snapshot.name,runs=[])
        denoise=engine._denoise
        for mode in ('baseline','accelerated'):
            require_idle('http://127.0.0.1:1924')
            gc.collect();mx.clear_cache();mx.reset_peak_memory()
            stages={}
            def capture(*a,**kw):
                result=denoise(*a,**kw)
                mx.eval(result)
                mx.savez(str(args.output/(mode+'.npz')),latents=result)
                return result
            def step(index,n,total):
                if n==1 or n%5==0 or n==total:
                    print(f'{mode}: {n}/{total}',flush=True)
                    require_idle('http://127.0.0.1:1924')
            began=time.monotonic()
            job=Job(1,prompt,args.output/(mode+'.png'),1152,768,20,1977,1.0)
            with patch.object(engine,'_denoise',side_effect=capture):
                result=engine.run_jobs([job],model_path=snapshot,acceleration=mode=='accelerated',cache_config=None,progress=False,show_library_progress=False,on_step=step,on_stage_timing=lambda i,n,t:stages.update({n:t}))
            if result.failed or result.interrupted or len(result.completed)!=1:
                raise RuntimeError(str(result))
            report['runs'].append(dict(mode=mode,seconds=time.monotonic()-began,peak_mlx_gib=mx.get_peak_memory()/2**30,stages=stages))
            print(json.dumps(report['runs'][-1]),flush=True)
        a=mx.load(str(args.output/'baseline.npz'))['latents']
        b=mx.load(str(args.output/'accelerated.npz'))['latents']
        report['latents_bit_exact']=bool(mx.array_equal(a,b).item())
        with np.load(args.output/'baseline.npz') as left,np.load(args.output/'accelerated.npz') as right:
            x,y=left['latents'],right['latents']
            report['raw_latent_bytes_identical']=x.shape==y.shape and x.dtype==y.dtype and x.tobytes()==y.tobytes()
        with Image.open(args.output/'baseline.png') as a,Image.open(args.output/'accelerated.png') as b:
            pa,pb=a.tobytes(),b.tobytes()
            report['pixels_bit_exact']=a.size==b.size and a.mode==b.mode and pa==pb
            report['pixel_sha256']=hashlib.sha256(pa).hexdigest()
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        if not report['latents_bit_exact'] or not report['raw_latent_bytes_identical'] or not report['pixels_bit_exact']:
            raise RuntimeError('Production parity gate failed; report saved')
        print('PRODUCTION PARITY PASSED',flush=True)


if __name__=='__main__':
    main()
