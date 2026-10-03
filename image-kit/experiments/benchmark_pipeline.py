"""Matched local-weight pipeline and resident-series parity check; no downloads."""
import time,json,fcntl,gc,argparse
from pathlib import Path
from experiments.benchmark_qwen21 import require_idle,PROMPT
from mlx_image import engine
from mlx_image.session import ImageSession
from mlx_image.types import Job
from PIL import Image
import mlx.core as mx
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--prompt-file',type=Path)
parser.add_argument('--output',type=Path,default=Path('local/pipeline-parity'))
args=parser.parse_args()
out=args.output;out.mkdir(parents=True,exist_ok=True)
require_idle('http://127.0.0.1:1924')
lock=(Path.home()/'.local/share/mlx-manager/heavy.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
prompt=args.prompt_file.read_text().strip() if args.prompt_file else PROMPT
report={'scope':'full pipeline including model loading, encoding, denoising, decoding and saving; 512x512, 4 steps, cache off','single':[],'series':[]}
def run(mode,seed,name,session=None):
 job=Job(1,prompt,out/(name+'.png'),512,512,4,seed,1.0)
 stages={};gc.collect();mx.clear_cache();mx.reset_peak_memory();began=time.monotonic()
 runner=session.run_jobs if session else engine.run_jobs
 kwargs={} if session else {'acceleration':mode=='fast'}
 result=runner([job],cache_config=None,progress=False,show_library_progress=False,on_stage_timing=lambda i,n,t:stages.update({n:t}),**kwargs)
 if result.failed or result.interrupted:raise RuntimeError(str(result))
 row=dict(mode=mode,seed=seed,seconds=time.monotonic()-began,peak_gib=mx.get_peak_memory()/2**30,stages=stages)
 print(json.dumps({'name':name,**row}),flush=True);return row
for rep in range(-1,3):
 for mode in (('baseline','fast') if rep%2 else ('fast','baseline')):
  row=run(mode,1977,f'single-{rep}-{mode}')
  if rep>=0:report['single'].append(row)
 a=Image.open(out/f'single-{rep}-baseline.png');b=Image.open(out/f'single-{rep}-fast.png')
 if a.tobytes()!=b.tobytes():raise RuntimeError('single pixels changed')
for mode in ('baseline','resident'):
 session=ImageSession(acceleration=True) if mode=='resident' else None
 try:
  for idx,seed in enumerate((1978,1979)):
   report['series'].append(run('fast' if session else 'baseline',seed,f'series-{idx}-{mode}',session))
 finally:
  if session:session.close()
for idx in range(2):
 if Image.open(out/f'series-{idx}-baseline.png').tobytes()!=Image.open(out/f'series-{idx}-resident.png').tobytes():raise RuntimeError('series pixels changed')
report['pixels_bit_exact']=True
(out/'report.json').write_text(json.dumps(report,indent=2))
print('PARITY PASSED',flush=True)
