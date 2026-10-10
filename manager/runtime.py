"""Single selected chat model, launched only by an explicit manager action."""
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
from urllib.parse import quote
from pathlib import Path

import config
import memory
import ownership
from transport import open_stream


PORT = 1925
BASE = f"http://127.0.0.1:{PORT}"


def api_id(entries, backend, model):
    if backend == 'mlx':
        # /v1/models enumerates the entire HF cache, not just the loaded model.
        return str(Path(model['path']).resolve())
    if len(entries) == 1:
        return entries[0]['id']
    match = next((e['id'] for e in entries if e['id'] == 'selected'), None)
    if match:
        return match
    raise ValueError('Cannot identify selected model in server catalog')


class ModelRuntime:
    def __init__(self):
        self.process = None
        self.state = "stopped"
        self.model = None
        self.backend = None
        self.api_model = None
        self.error = None
        self._generation = 0
        self._state_path = config.DATA_ROOT / 'runtime-owner.json'
        try:
            saved = json.loads(self._state_path.read_text())
            process = ownership.recover(saved.get('owner'))
            if process:
                self.process = process
                self.model, self.backend = saved['model'], saved['backend']
                self.api_model = saved.get('api_model')
                self.state = 'starting'
                self._wait_ready(process, self.backend, self._generation)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save(self):
        owner = self.process.proof if isinstance(self.process, ownership.RecoveredProcess) else ownership.record(self.process) if self.running() else None
        ownership.write(self._state_path, dict(owner=owner, model=self.model, backend=self.backend, api_model=self.api_model))

    def running(self):
        return self.process is not None and self.process.poll() is None

    def status(self):
        if self.process is not None and not self.running() and self.state != "stopped":
            self.state = "failed"
            self.error = self.error or f"Process exited ({self.process.returncode}); see model log"
        return {"state": self.state, "model": self.model, "backend": self.backend,
                "pid": self.process.pid if self.running() else None, "error": self.error,
                "api_model": self.api_model, "port": PORT}

    def start(self, model, backend, executable):
        if memory.listener_pid(PORT):
            raise RuntimeError(f"Port {PORT} is already occupied")
        self.model, self.backend, self.error = model, backend, None
        self.state = "starting"
        self._generation += 1
        generation = self._generation
        config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
        base = config.DATA_ROOT / "runtime" / model["id"]
        base.mkdir(parents=True, exist_ok=True)
        if backend == "omlx":
            model_dir = base / "models"
            model_dir.mkdir(exist_ok=True)
            link = model_dir / "selected"
            if link.is_symlink() and link.resolve() != Path(model["path"]).resolve():
                link.unlink()
            if not link.exists():
                link.symlink_to(model["path"], target_is_directory=True)
            args = [executable, "serve", "--model-dir", str(model_dir), "--base-path", str(base / "state"),
                    "--host", "127.0.0.1", "--port", str(PORT), "--no-hf-cache",
                    "--max-concurrent-requests", "1"]
        else:
            args = [executable, "--model", model["path"], "--host", "127.0.0.1", "--port", str(PORT)]
        with open(config.DATA_ROOT / "model.log", "a", encoding="utf-8") as log:
            try:
                self.process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                    env=dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"), start_new_session=True)
            except OSError as exc:
                self.process, self.state, self.error = None, 'failed', str(exc)
                self._save()
                raise RuntimeError('Could not launch text runtime: ' + str(exc)) from exc
        process = self.process
        self._save()
        self._wait_ready(process, backend, generation)
        return self.status()

    def _wait_ready(self, process, backend, generation):
        def wait_ready():
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline and generation == self._generation:
                if process.poll() is not None:
                    self.status()
                    return
                try:
                    with urllib.request.urlopen(BASE + "/v1/models", timeout=2) as response:
                        entries = json.load(response)["data"]
                    if not entries:
                        raise ValueError("No model advertised")
                    api_model = api_id(entries, backend, self.model)
                    if backend == "omlx":
                        request = urllib.request.Request(BASE + f"/v1/models/{quote(api_model, safe='')}/load", data=b"{}",
                            headers={"Content-Type": "application/json"}, method="POST")
                        with urllib.request.urlopen(request, timeout=240) as response:
                            json.load(response)
                    if generation == self._generation:
                        self.api_model, self.state, self.error = api_model, "ready", None
                        self._save()
                    return
                except (OSError, ValueError, KeyError) as exc:
                    self.error = str(exc)
                    time.sleep(1)
            if generation == self._generation:
                self.state, self.error = "failed", "Model did not become ready; see model log"
                if process.poll() is None:
                    ownership.stop(process)
        threading.Thread(target=wait_ready, daemon=True).start()

    def stop(self):
        self._generation += 1
        if self.running():
            process = self.process
            ownership.stop(process)
        self.state, self.process, self.api_model, self.error = "stopped", None, None, None
        self._save()

    def chat(self, messages, max_tokens, temperature):
        with self.open_chat(messages, max_tokens, temperature) as response:
            return json.load(response)

    def open_chat(self, messages, max_tokens, temperature, stream=False, on_socket=None, tools=None):
        if self.state != "ready" or not self.running():
            raise RuntimeError("Selected model is not ready")
        payload = {"model": self.api_model, "messages": messages, "stream": stream,
                   "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        request = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        return open_stream(request, on_socket) if on_socket else urllib.request.urlopen(request, timeout=300)
