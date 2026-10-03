"""Portable defaults; machine-specific overrides stay in ignored config.local.json."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    LOCAL = json.loads((ROOT / 'config.local.json').read_text())
    if not isinstance(LOCAL,dict):
        raise ValueError('config.local.json must be an object')
except FileNotFoundError:
    LOCAL = {}


def setting(name, default):
    return os.environ.get('MLX_MANAGER_'+name, LOCAL.get(name,default))


def path(name, default):
    return Path(setting(name,str(default))).expanduser()


DATA_ROOT = path('DATA_ROOT',Path.home()/'.local/share/mlx-manager')
OUTPUT_ROOT = DATA_ROOT / 'outputs'
MARA_LABEL = setting('MARA_LABEL','org.mlx-manager.mara')
MARA_PLIST = path('MARA_PLIST',Path.home()/'Library/LaunchAgents'/f'{MARA_LABEL}.plist')
MARA_MODEL = path('MARA_MODEL',Path.home()/'models/Mara')
MARA_LOG = path('MARA_LOG',DATA_ROOT/'mara.log')
IMAGE_ROOT = path('IMAGE_ROOT',ROOT.parent/'mlx-image-kit')
IMAGE_PYTHON = path('IMAGE_PYTHON',IMAGE_ROOT/'.venv/bin/python')
HUB = Path(os.environ.get('HF_HUB_CACHE',str(Path(os.environ.get('HF_HOME',str(Path.home()/'.cache/huggingface')))/'hub'))).expanduser()
IMAGE_CACHE = path('IMAGE_CACHE',HUB/'models--mlx-community--Qwen-Image-2.1-MLX-4bit')
GUARDIAN_V2 = 'http://127.0.0.1:1923'
GUARDIAN_LABEL = setting('GUARDIAN_LABEL','org.mlx-manager.guardian')
GUARDIAN_PLIST = path('GUARDIAN_PLIST',Path.home()/'Library/LaunchAgents'/f'{GUARDIAN_LABEL}.plist')
MARA_ENDPOINT = 'http://127.0.0.1:1919'
MANAGER_HOST = '127.0.0.1'
MANAGER_PORT = 1924
MODEL_ROOTS = [Path(p).expanduser() for p in LOCAL.get('MODEL_ROOTS',[])]


def image_snapshot() -> Path | None:
    try:
        revision = (IMAGE_CACHE/'refs/main').read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if not revision or '/' in revision or '..' in revision:
        return None
    snapshot = IMAGE_CACHE/'snapshots'/revision
    required = ('text_encoder','transformer','vae')
    if not all((snapshot/part/'model.safetensors').is_file() for part in required):
        return None
    return snapshot
