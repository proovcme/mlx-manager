"""Check native-size resident memory and prompt reuse against staged reference pixels."""
import argparse
import fcntl
import gc
import json
import time
from pathlib import Path
from unittest.mock import patch

import mlx.core as mx
from PIL import Image
from experiments.benchmark_qwen21 import PROMPT,require_idle
from mlx_image import engine
from mlx_image.session import ImageSession
from mlx_image.types import Job


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt-file',type=Path)
    parser.add_argument('--first-steps',type=int,default=20)
    parser.add_argument('--next-steps',type=int,default=4)
    parser.add_argument('--reference',type=Path,default=Path('local/production-parity/baseline.png'))
    parser.add_argument('--output',type=Path,default=Path('local/production-series'))
    args=parser.parse_args()
    prompt=args.prompt_file.read_text().strip() if args.prompt_file else PROMPT
    if not args.reference.is_file():raise FileNotFoundError('Run check_production_parity with the same prompt first')
    args.output.mkdir(parents=True,exist_ok=True)
    require_idle('http://127.0.0.1:1924')
    with (Path.home()/'.local/share/mlx-manager/heavy.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require_idle('http://127.0.0.1:1924')
        report=dict(scope='Native-size resident series parity and memory check; timings are not medians',width=1152,height=768,cache='off',mlx=mx.__version__,runs=[])
        def run(mode,steps,seed,index,session=None):
            stages={}
            finite=True
            save_png=engine._save_png
            def checked_save(decoded,output):
                nonlocal finite
                finite=bool(mx.all(mx.isfinite(decoded)).item())
                if not finite and (mode!='resident-first' or args.first_steps==20):
                    raise RuntimeError('Nonfinite decoded pixels; quality gate rejected')
                return save_png(decoded,output)
            def step(i,n,total):
                if n==1 or n%5==0 or n==total:
                    print(f'{mode}: {n}/{total}',flush=True)
                    require_idle('http://127.0.0.1:1924')
            job=Job(index,prompt,args.output/(mode+'.png'),1152,768,steps,seed,1.0)
            gc.collect();mx.clear_cache();mx.reset_peak_memory()
            began=time.monotonic()
            runner=session.run_jobs if session else engine.run_jobs
            with patch.object(engine,'_save_png',side_effect=checked_save):
                result=runner([job],cache_config=None,progress=False,show_library_progress=False,on_step=step,on_stage_timing=lambda i,n,t:stages.update({n:t}))
            if result.failed or result.interrupted or len(result.completed)!=1:raise RuntimeError(str(result))
            report['runs'].append(dict(mode=mode,steps=steps,seed=seed,decoded_values_finite=finite,seconds=time.monotonic()-began,peak_mlx_gib=mx.get_peak_memory()/2**30,stages=stages))
            print(json.dumps(report['runs'][-1]),flush=True)
        session=ImageSession(acceleration=True)
        try:
            run('resident-first',args.first_steps,1977,1,session)
            if args.first_steps==20:
                with Image.open(args.reference) as a,Image.open(args.output/'resident-first.png') as b:
                    report['first_pixels_bit_exact']=a.size==b.size and a.mode==b.mode and a.tobytes()==b.tobytes()
                if not report['first_pixels_bit_exact']:raise RuntimeError('Native resident first image drifted')
            else:
                report['first_pixels_bit_exact']=None
            run('resident-next',args.next_steps,1978,2,session)
        finally:
            session.close()
        report['models_released']=session.transformer is None and session.vae is None and session.embeds is None
        run('staged-next',args.next_steps,1978,2)
        with Image.open(args.output/'resident-next.png') as a,Image.open(args.output/'staged-next.png') as b:
            report['next_pixels_bit_exact']=a.size==b.size and a.mode==b.mode and a.tobytes()==b.tobytes()
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        if not report['next_pixels_bit_exact']:raise RuntimeError('Reused native conditioning drifted')
        print('NATIVE SERIES PARITY PASSED',flush=True)


if __name__=='__main__':
    main()
