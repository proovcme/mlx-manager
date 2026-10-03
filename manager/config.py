"""Portable defaults; machine-specific overrides stay in ignored config.local.json."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    LOCAL_FILE = ROOT / 'config.local.json'
    if not LOCAL_FILE.exists():
        LOCAL_FILE = ROOT.parent / 'config.local.json'
    LOCAL = json.loads(LOCAL_FILE.read_text())
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
BUNDLED_IMAGE_ROOT = ROOT.parent/'image-kit'
IMAGE_ROOT = path('IMAGE_ROOT',BUNDLED_IMAGE_ROOT if BUNDLED_IMAGE_ROOT.is_dir() else ROOT.parent/'mlx-image-kit')
IMAGE_ENVIRONMENTS = [IMAGE_ROOT/'.venv/bin/python', ROOT.parent/'.venv/bin/python']
IMAGE_PYTHON = path('IMAGE_PYTHON',next((p for p in IMAGE_ENVIRONMENTS if p.is_file()),IMAGE_ENVIRONMENTS[0]))
HUB = Path(os.environ.get('HF_HUB_CACHE',str(Path(os.environ.get('HF_HOME',str(Path.home()/'.cache/huggingface')))/'hub'))).expanduser()
IMAGE_CACHE = path('IMAGE_CACHE',HUB/'models--mlx-community--Qwen-Image-2.1-MLX-4bit')
GUARDIAN_V2 = 'http://127.0.0.1:1923'
GUARDIAN_LABEL = setting('GUARDIAN_LABEL','org.mlx-manager.guardian')
GUARDIAN_PLIST = path('GUARDIAN_PLIST',Path.home()/'Library/LaunchAgents'/f'{GUARDIAN_LABEL}.plist')
MARA_ENDPOINT = 'http://127.0.0.1:1919'
MANAGER_HOST = '127.0.0.1'
MANAGER_PORT = int(setting('PORT',1924))
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

# Conditioning reuse changes no model precision, sampling or denoising steps.
IMAGE_ACCELERATION = str(setting("IMAGE_ACCELERATION", True)).lower() in ("true", "1", "yes")
