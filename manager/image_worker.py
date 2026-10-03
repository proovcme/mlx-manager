"""Run the existing MLX Image Kit from stdin, keeping prompts out of argv."""

from __future__ import annotations

import json
import sys

from config import IMAGE_ROOT
from progress import Recorder


def main() -> int:
    job = json.load(sys.stdin)
    recorder = Recorder(job["progress"], job["steps"])
    sys.path.insert(0, str(IMAGE_ROOT))
    from pathlib import Path
    from mlx_image.engine import run_jobs
    from mlx_image.presentation import run_presented, report_failure
    from mlx_image.types import Job
    from mlx_image.cli import _cache_config
    image = Job(1, job["prompt"], Path(job["output"]), job["width"], job["height"],
                job["steps"], job["seed"], 1.0, job["cache"])

    def runner(jobs, **kwargs):
        for key, callback in (("on_stage_event", recorder.event), ("on_step", recorder.step),
                              ("on_forward", recorder.forward), ("on_stage_timing", recorder.timing)):
            original = kwargs.get(key)
            def combined(*args, callback=callback, original=original):
                callback(*args)
                if original:
                    original(*args)
            kwargs[key] = combined
        return run_jobs(jobs, **kwargs)
    try:
        summary = run_presented(runner, [image], model_path=Path(job["model_path"]),
                                cache_config=_cache_config(job["cache"]))
        recorder.data["stage"] = "done" if summary.completed else "cancelled" if summary.interrupted else "failed"
        recorder.write()
        for failure in summary.failed:
            report_failure(failure)
        return 0 if summary.completed else 130 if summary.interrupted else 1
    except KeyboardInterrupt:
        recorder.data["stage"] = "cancelled"
        recorder.write()
        return 130
    except Exception:
        recorder.data["stage"] = "failed"
        recorder.write()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
