"""Recover only processes whose PID, birth time, command and process group match."""
import json
import os
import subprocess
import time
from pathlib import Path


def identity(pid):
    try:
        value = subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','pgid=','-o','command='],capture_output=True,text=True,timeout=2).stdout.strip()
        if not value or os.getpgid(pid) != pid:
            return None
        return value
    except (OSError, subprocess.TimeoutExpired):
        return None


def record(process):
    # Homebrew's macOS Python launcher re-execs the framework binary. Record
    # the settled command, not the short-lived launcher's argv[0].
    previous = None
    stable = 0
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = identity(process.pid)
        if current and current == previous:
            stable += 1
            if stable >= 3:
                return dict(pid=process.pid, identity=current)
        else:
            stable = 0
        previous = current
        if process.poll() is not None:
            return dict(pid=process.pid, identity=None)
        time.sleep(.05)
    return dict(pid=process.pid, identity=previous)


def write(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream)
    os.replace(temp,path)


class RecoveredProcess:
    def __init__(self, proof, result=None):
        self.pid = proof['pid']
        self.proof = proof
        self.result = Path(result) if result else None
        self.returncode = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self.proof.get('identity') and identity(self.pid) == self.proof['identity']:
            return None
        self.returncode = 1
        if self.result:
            try:
                stage = json.loads(self.result.read_text()).get('stage')
                self.returncode = 0 if stage == 'done' else 130 if stage == 'cancelled' else 1
            except (OSError,ValueError):
                pass
        return self.returncode

    def wait(self, timeout=None):
        start = time.monotonic()
        while self.poll() is None:
            if timeout is not None and time.monotonic()-start >= timeout:
                raise subprocess.TimeoutExpired('recovered process',timeout)
            time.sleep(.1)
        return self.returncode


def recover(proof, result=None):
    if not isinstance(proof,dict) or not isinstance(proof.get('pid'),int) or not proof.get('identity'):
        return None
    process = RecoveredProcess(proof,result)
    return process if process.poll() is None else None
