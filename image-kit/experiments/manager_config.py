"""Shared benchmark locations, including ignored local overrides and environment."""
import importlib.util
from pathlib import Path

file=Path(__file__).resolve().parents[2]/'manager/config.py'
if not file.is_file():
    raise RuntimeError('Run manager benchmarks from the combined toolkit checkout')
spec=importlib.util.spec_from_file_location('benchmark_manager_config',file)
settings=importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)
MANAGER_URL=f'http://{settings.MANAGER_HOST}:{settings.MANAGER_PORT}'
LOCK_PATH=settings.DATA_ROOT/'heavy.lock'

def add_arguments(parser):
    parser.add_argument('--manager-url',default=MANAGER_URL)
    parser.add_argument('--lock-path',type=Path,default=LOCK_PATH)

def local_snapshot():
    snapshot=settings.image_snapshot()
    if snapshot is None:
        raise RuntimeError('Configured local image snapshot is incomplete; benchmark never downloads weights')
    return snapshot
