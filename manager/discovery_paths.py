"""Read model locations from runtime configuration; never execute discovered config."""
import json
import os
import plistlib
import shlex
import subprocess
from pathlib import Path
from xml.parsers.expat import ExpatError

import config


def local_path(value, cwd=None):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        return None
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        if not cwd:
            return None
        path = Path(cwd) / path
    return path


def is_model_runtime(args):
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return False
    executables = {'omlx', 'mlx_lm.server'}
    # Python can invoke a script/module; don't identify a runtime from a model name.
    if not args:return False
    executable=Path(args[0]).name.lower()
    if executable in executables:return True
    python=executable.startswith('python')
    return python and (len(args)>1 and Path(args[1]).name in executables or
        any(args[i:i+2] == ['-m','mlx_lm.server'] for i in range(1,len(args)-1)))


def launch_services():
    files = [*([config.EXTERNAL_PLIST] if config.EXTERNAL_ENABLED else []), *sorted((Path.home()/'Library/LaunchAgents').glob('*.plist'))[:200]]
    for path in dict.fromkeys(files):
        try:
            if path.stat().st_size > 1024*1024:
                continue
            data = plistlib.loads(path.read_bytes())
            args = data.get('ProgramArguments', [])
            if not args and isinstance(data.get('Program'), str):
                args = [data['Program']]
            if is_model_runtime(args):
                yield {'args': args, 'env': data.get('EnvironmentVariables', {}), 'cwd': data.get('WorkingDirectory')}
        except (OSError, ValueError, TypeError, AttributeError, ExpatError, plistlib.InvalidFileException):
            continue


def running_services():
    try:
        output = subprocess.run(['ps','-axo','command='],capture_output=True,text=True,timeout=2,check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    services=[]
    for line in output.splitlines():
        try:
            args=shlex.split(line)
        except ValueError:
            continue
        if is_model_runtime(args):
            services.append({'args':args,'env':{},'cwd':None})
    return services


def argument_values(args, name):
    for index, value in enumerate(args):
        if value == name and index+1<len(args):
            yield args[index+1]
        elif value.startswith(name+'='):
            yield value[len(name)+1:]


def omlx_base(env, args=()):
    explicit=list(argument_values(args,'--base-path'))
    if explicit:
        return explicit[-1]
    if env.get('OMLX_BASE_PATH'):
        return env['OMLX_BASE_PATH']
    try:
        path=Path.home()/'Library/Application Support/oMLX/base-path'
        if path.stat().st_size<=4096:
            value=path.read_text().strip()
            if value:return value
    except (OSError,UnicodeError):
        pass
    return str(Path.home()/'.omlx')


def settings_dirs(base):
    try:
        file=base/'settings.json'
        if file.stat().st_size>1024*1024:return []
        model=json.loads(file.read_text()).get('model',{})
        dirs=model.get('model_dirs') or [model.get('model_dir')]
        return [value for value in dirs if isinstance(value,str) and value.strip()] if isinstance(dirs,list) else []
    except (OSError,ValueError,TypeError,AttributeError):
        return []


def service_paths(service):
    args,env,cwd=service['args'],service['env'],service['cwd']
    if not isinstance(env,dict):env={}
    paths=[]
    if any(Path(a).name=='omlx' for a in args[:2]):
        base=local_path(omlx_base(env,args),cwd)
        cli=list(argument_values(args,'--model-dir'))
        raw=cli[-1] if cli else env.get('OMLX_MODEL_DIR')
        values=raw.split(',') if isinstance(raw,str) else settings_dirs(base) if base else []
        paths.extend(local_path(value.strip(),cwd) for value in values)
        if not values and base:paths.append(base/'models')
    else:
        for value in argument_values(args,'--model'):
            paths.append(local_path(value,cwd))
    if any(env.get(key) for key in ('HF_HUB_CACHE','HUGGINGFACE_HUB_CACHE','HF_HOME','XDG_CACHE_HOME')):
        paths.append(config.hub_cache(env))
    return [p for p in paths if p is not None]


def model_roots():
    home=Path.home()
    base=local_path(omlx_base(os.environ))
    paths=[*config.MODEL_ROOTS,config.hub_cache(),*([config.EXTERNAL_MODEL] if config.EXTERNAL_ENABLED else [])]
    if base:
        values=os.environ.get('OMLX_MODEL_DIR')
        dirs=values.split(',') if values else settings_dirs(base)
        paths.extend(local_path(value.strip()) for value in dirs)
        if not dirs:paths.append(base/'models')
    for service in [*launch_services(),*running_services()]:
        paths.extend(service_paths(service))
    paths.extend([home/'models',home/'.lmstudio/models'])
    roots=[]
    for path in paths:
        if path is None:continue
        try:
            resolved=path.resolve()
            if resolved.is_dir() and resolved not in roots:roots.append(resolved)
        except (OSError,RuntimeError):
            continue
    return roots
