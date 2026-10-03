"""Run the existing MLX Image Kit from stdin, keeping prompts out of argv."""

from __future__ import annotations

import json
import sys

from config import IMAGE_ROOT
from progress import Recorder


def main() -> int:
    # A series uses JSON lines and keeps stdin open. A single-image caller closes
    # stdin after one JSON object, preserving the original subprocess contract.
    session = None
    recorder = None
    code = 0
    try:
        for line in sys.stdin:
            job = json.loads(line)
            recorder = Recorder(job["progress"], job["steps"])
            sys.path.insert(0, str(IMAGE_ROOT))
            # Use package-owned code even when weights/runtime come from an
            # external Image Kit. The standalone installation is left unchanged.
            from config import ROOT
            packaged = ROOT.parent / 'image-kit'
            if (packaged / 'mlx_image').is_dir():
                sys.path.insert(0, str(packaged))
            from pathlib import Path
            from mlx_image.engine import run_jobs
            from mlx_image.presentation import run_presented, report_failure
            from mlx_image.types import Job
            from mlx_image.cli import _cache_config
            persistent = job.get('session', False)
            acceleration = job.get('acceleration', False)
            if persistent and session is None:
                from mlx_image.session import ImageSession
                session = ImageSession(acceleration=acceleration)
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
                if persistent:
                    return session.run_jobs(jobs, **kwargs)
                return run_jobs(jobs, acceleration=acceleration, **kwargs)
            try:
                summary = run_presented(runner, [image], model_path=Path(job["model_path"]),
                                        cache_config=_cache_config(job["cache"]))
                recorder.data["stage"] = "done" if summary.completed else "cancelled" if summary.interrupted else "failed"
                if summary.failed:
                    recorder.data['error'] = summary.failed[0].message
                recorder.write()
                for failure in summary.failed:
                    report_failure(failure)
                code = 0 if summary.completed else 130 if summary.interrupted else 1
            except KeyboardInterrupt:
                recorder.data["stage"] = "cancelled"
                recorder.write()
                code = 130
            except Exception:
                recorder.data["stage"] = "failed"
                recorder.write()
                raise
            if code or not persistent:
                break
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        if recorder:
            recorder.data.update(stage='failed', error=str(exc))
            recorder.write()
        raise
    finally:
        if session:
            session.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
