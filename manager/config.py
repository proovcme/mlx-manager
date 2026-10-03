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
# Optional, explicitly configured external LaunchAgent; disabled on clean installs.
EXTERNAL_ENABLED = str(setting('EXTERNAL_ENABLED',False)).lower() in ('true','1','yes')
EXTERNAL_LABEL = setting('EXTERNAL_LABEL','org.mlx-manager.external')
EXTERNAL_PLIST = path('EXTERNAL_PLIST',Path.home()/'Library/LaunchAgents'/f'{EXTERNAL_LABEL}.plist')
EXTERNAL_MODEL = path('EXTERNAL_MODEL',DATA_ROOT/'external-model')
EXTERNAL_LOG = path('EXTERNAL_LOG',DATA_ROOT/'external.log')
EXTERNAL_MODEL_ID = setting('EXTERNAL_MODEL_ID',EXTERNAL_MODEL.name)
EXTERNAL_ENDPOINT = setting('EXTERNAL_ENDPOINT','http://127.0.0.1:1926')
PROXY_ENDPOINT = setting('PROXY_ENDPOINT',EXTERNAL_ENDPOINT)
PROXY_LABEL = setting('PROXY_LABEL','')
PROXY_PLIST = path('PROXY_PLIST',Path.home()/'Library/LaunchAgents'/f'{PROXY_LABEL}.plist')
SESSION_PATH = setting('SESSION_PATH','')
SESSION_HEADER = setting('SESSION_HEADER','')
STATS_PATH = setting('STATS_PATH','')
from urllib.parse import urlsplit
for endpoint in (EXTERNAL_ENDPOINT,PROXY_ENDPOINT):
    parsed=urlsplit(endpoint)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost','127.0.0.1') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('External chat endpoints must be loopback HTTP origins')
EXTERNAL_PORT = urlsplit(EXTERNAL_ENDPOINT).port or 80
PROXY_PORT = urlsplit(PROXY_ENDPOINT).port or 80
BUNDLED_IMAGE_ROOT = ROOT.parent/'image-kit'
IMAGE_ROOT = path('IMAGE_ROOT',BUNDLED_IMAGE_ROOT if BUNDLED_IMAGE_ROOT.is_dir() else ROOT.parent/'mlx-image-kit')
IMAGE_ENVIRONMENTS = [IMAGE_ROOT/'.venv/bin/python', ROOT.parent/'.venv/bin/python']
IMAGE_PYTHON = path('IMAGE_PYTHON',next((p for p in IMAGE_ENVIRONMENTS if p.is_file()),IMAGE_ENVIRONMENTS[0]))
def hub_cache(environ=None):
    env = os.environ if environ is None else environ
    cache = Path(env.get('XDG_CACHE_HOME', str(Path.home()/'.cache'))).expanduser()
    hf_home = Path(env.get('HF_HOME', str(cache/'huggingface'))).expanduser()
    return Path(env.get('HF_HUB_CACHE', env.get('HUGGINGFACE_HUB_CACHE', str(hf_home/'hub')))).expanduser()


HUB = hub_cache()
IMAGE_CACHE = path('IMAGE_CACHE',HUB/'models--mlx-community--Qwen-Image-2.1-MLX-4bit')
MANAGER_HOST = '127.0.0.1'
MANAGER_PORT = int(setting('PORT',1924))
def configured_model_roots():
    values = setting('MODEL_ROOTS', [])
    if isinstance(values, str):
        try:values = json.loads(values)
        except ValueError as exc:raise ValueError('MLX_MANAGER_MODEL_ROOTS must be a JSON array of directory paths') from exc
    if not isinstance(values, list) or not all(isinstance(p,str) and p.strip() for p in values):
        raise ValueError('MODEL_ROOTS must be an array of non-empty directory paths')
    return [Path(os.path.expandvars(p)).expanduser() for p in values]


MODEL_ROOTS = configured_model_roots()


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
