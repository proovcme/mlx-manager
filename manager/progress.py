"""Small progress snapshots without prompts or GPU imports."""
import json
import os
import re
import time
from pathlib import Path


class Recorder:
    def __init__(self, path, total):
        self.path = Path(path)
        self.started = time.monotonic()
        self.data = {"stage": "preparing", "step": 0, "total": total, "computed": 0, "skipped": 0}
        self.write()

    def write(self):
        self.data["elapsed_seconds"] = time.monotonic() - self.started
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(self.data, stream)
        os.replace(temporary, self.path)

    def event(self, name, index):
        stages = {"model_ready": "loading_encoder", "text_encoder_ready": "encoding",
                  "prompt_encoded": "loading_transformer", "prompt_reused": "loading_transformer", "transformer_ready": "denoising",
                  "denoising_start": "denoising", "vae_ready": "decoding", "decoding_start": "decoding"}
        self.data["stage"] = stages.get(name, self.data["stage"])
        self.write()

    def step(self, index, step, total):
        # The cache-off engine intentionally omits forward callbacks.
        self.data["computed"] += max(0, step - self.data["computed"] - self.data["skipped"])
        self.data.update(stage="denoising", step=step, total=total)
        self.write()

    def forward(self, index, computed):
        self.data["computed" if computed else "skipped"] += 1

    def timing(self, index, name, seconds):
        self.data.setdefault("timings", {})[name] = seconds


def from_log(text):
    """Compatibility with workers already running when progress was added."""
    result = {"stage": "preparing", "step": 0, "total": None}
    if "Generating" in text:
        result["stage"] = "denoising"
    matches = re.findall(r"Denoising (\d+)/(\d+)", text)
    if matches:
        result.update(step=int(matches[-1][0]), total=int(matches[-1][1]))
    if "Decoding" in text:
        result["stage"] = "decoding"
    if "✓ Saved" in text:
        result["stage"] = "done"
    return result
