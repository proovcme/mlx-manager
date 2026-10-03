"""Paired denoising-only experiment using local weights and a neutral prompt.

Run from the repo: .venv/bin/python -m experiments.benchmark_qwen21
No model downloads or changes to the production pipeline. Optional --decode
saves comparison images under the ignored local report directory.
Requires the local manager to be idle and holds its heavy-workload file lock.
"""

import argparse
import fcntl
import gc
import json
import statistics
import time
import urllib.request
from pathlib import Path

import mlx.core as mx
from mflux.models.common.config import ModelConfig
from mflux.models.qwen21.qwen21_initializer import Qwen21Initializer
from mflux.models.qwen21.variants.txt2img.qwen_image_21 import QwenImage21
from mflux.models.qwen21.model.qwen21_text_encoder.qwen21_prompt_encoder import Qwen21PromptEncoder

from mlx_image import engine
from mlx_image.cache import CacheConfig
from mlx_image.types import Job
from mlx_image.accelerate import ConditionedTransformer as ExperimentalTransformer


PROMPT = ("A cobalt blue ceramic teapot on a worn oak table beside a clear glass of water, "
          "soft morning window light, pale plaster wall, realistic photography, "
          "one teapot with an intact handle and spout, no writing, no people.")


def status(url):
    with urllib.request.urlopen(url + "/api/status", timeout=5) as response:
        return json.load(response)


def require_idle(url):
    state = status(url)
    if (state["mode"] != "idle" or state["heavy_lock"]
            or state["image"]["pid"] or state["image"]["processes"]
            or state["external_chat"]["pid"] or state["external_chat"]["other_listener_pid"]
            or state["active_chat_requests"]):
        raise RuntimeError("manager or external model is busy; benchmark refused")
    pressure = state["system"]["pressure_free_percent"]
    if pressure is None or pressure < 40:
        raise RuntimeError("insufficient or unknown memory headroom")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", type=Path, help="local test prompt; contents are never included in the report")
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=("rope", "prefix", "prefix-rope"), default=["prefix-rope"])
    parser.add_argument("--cache-modes", nargs="+", choices=("off", "balanced"), default=["off", "balanced"])
    parser.add_argument("--decode", action="store_true", help="decode and compare every measured PNG after releasing transformer")
    parser.add_argument("--fail-on-drift", action="store_true", help="stop at the first nonidentical latent, including warmup")
    parser.add_argument("--manager-url", default="http://127.0.0.1:1924")
    parser.add_argument("--lock-path", type=Path, default=Path.home() / ".local/share/mlx-manager/heavy.lock")
    parser.add_argument("--report", type=Path, default=Path("local/qwen21-optimizations.json"))
    args = parser.parse_args()
    prompt = args.prompt_file.read_text().strip() if args.prompt_file else PROMPT
    job = Job(1, prompt, Path("unused.png"), args.width, args.height, args.steps, 1977, 1.0)
    engine.validate_job(job)
    if args.reps < 1:
        parser.error("reps must be positive")
    require_idle(args.manager_url)
    # The same lock prevents manager-controlled starts throughout this experiment.
    with args.lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_idle(args.manager_url)
        cache = Path.home() / ".cache/huggingface/hub/models--mlx-community--Qwen-Image-2.1-MLX-4bit"
        revision = (cache / "refs/main").read_text().strip()
        snapshot = engine._snapshot(cache / "snapshots" / revision)
        model_config = ModelConfig.qwen_image_21()
        print("Encoding neutral prompt with the existing native Q4 encoder", flush=True)
        began_encoding = time.perf_counter()
        te = engine._load_text_encoder(snapshot)
        qwen = QwenImage21.__new__(QwenImage21)
        super(QwenImage21, qwen).__init__()
        Qwen21Initializer._init_config(qwen, model_config)
        Qwen21Initializer._init_tokenizers(qwen, str(snapshot))
        embeds, mask = Qwen21PromptEncoder.encode_prompt(prompt=prompt,
            prompt_cache=qwen.prompt_cache, tokenizer=qwen.tokenizers["qwen21"], text_encoder=te)
        mx.eval(embeds, mask)
        encoding_seconds = time.perf_counter() - began_encoding
        del te, qwen
        gc.collect()
        mx.clear_cache()
        print("Loading existing native Q4 transformer", flush=True)
        tr = engine._load_transformer(snapshot)
        report = {"scope": "warm denoising only; excludes text encoding, model loading and VAE",
                  "shape": [args.width, args.height], "steps": args.steps, "seed": 1977,
                  "tokens": embeds.shape[1], "reps": args.reps, "warmup_per_mode": 1,
                  "mlx": mx.__version__, "snapshot_revision": revision, "results": {}}
        report["encoder_load_and_encoding_seconds"] = encoding_seconds
        args.report.parent.mkdir(parents=True, exist_ok=True)
        spool = args.report.parent / (args.report.stem + "-artifacts")
        if args.decode:
            spool.mkdir(exist_ok=True)
        baselines = {}
        for cache_mode in args.cache_modes:
            cache_config = None if cache_mode == "off" else CacheConfig(0.08)
            modes = {"vanilla": tr, **{name: ExperimentalTransformer(tr, name)
                     for name in args.modes}}
            records = {name: [] for name in modes}
            # Warm every mode, then interleave measured runs to reduce order bias.
            for rep in range(-1, args.reps):
                order = list(modes) if rep < 0 or rep % 2 else list(reversed(modes))
                for name in order:
                    # A direct external launch can bypass the file lock. Stop between runs.
                    require_idle(args.manager_url)
                    model = modes[name]
                    if isinstance(model, ExperimentalTransformer):
                        model.reset()
                    gc.collect()
                    mx.clear_cache()
                    mx.reset_peak_memory()
                    forward_calls = 0

                    def counted_forward(*positional, **keyword):
                        nonlocal forward_calls
                        forward_calls += 1
                        return model(*positional, **keyword)

                    began = time.perf_counter()
                    def step_progress(step, total):
                        if step == 1 or step % 5 == 0 or step == total:
                            print(f"{cache_mode}/{name}/rep{rep}: {step}/{total}", flush=True)
                    output = engine._denoise(job, counted_forward, embeds, mask, model_config,
                        cache_config=cache_config, show_library_progress=False, on_step=step_progress)
                    mx.eval(output)
                    seconds = time.perf_counter() - began
                    if name == "vanilla" and cache_mode not in baselines:
                        baselines[cache_mode] = output
                        if args.decode:
                            mx.savez(str(spool / f"{cache_mode}-reference.npz"), latents=output)
                    exact = bool(mx.array_equal(output, baselines[cache_mode]).item())
                    if args.fail_on_drift and not exact:
                        drift = mx.abs(output.astype(mx.float32) - baselines[cache_mode].astype(mx.float32))
                        report["rejected"] = {"cache_mode": cache_mode, "mode": name,
                            "rep": rep, "bit_exact": False, "seconds": seconds,
                            "max_abs_error": float(mx.max(drift).item()),
                            "relative_l1": float((mx.mean(drift) / mx.maximum(mx.mean(mx.abs(baselines[cache_mode].astype(mx.float32))), 1e-8)).item())}
                        mx.savez(str(args.report.parent / (args.report.stem + "-rejected.npz")),
                                 reference=baselines[cache_mode], candidate=output)
                        args.report.write_text(json.dumps(report, indent=2) + "\n")
                        raise RuntimeError(f"lossless gate failed: {cache_mode}/{name}; report saved")
                    if rep < 0:
                        print(f"{cache_mode}/{name}: warmup excluded", flush=True)
                    else:
                        baseline = baselines[cache_mode]
                        error = mx.abs(output.astype(mx.float32) - baseline.astype(mx.float32))
                        relative = mx.mean(error) / mx.maximum(mx.mean(mx.abs(baseline.astype(mx.float32))), 1e-8)
                        mx.eval(error, relative)
                        row = {"seconds": seconds, "peak_mlx_gib": mx.get_peak_memory() / 2**30,
                               "transformer_forwards": forward_calls,
                               "noise_cache_skips": args.steps - forward_calls,
                               "bit_exact": bool(mx.array_equal(output, baseline).item()),
                               "max_abs_error": float(mx.max(error).item()),
                               "relative_l1": float(relative.item())}
                        if isinstance(model, ExperimentalTransformer):
                            row.update(prefix_captures=model.capture_count,
                                       prefix_reuses=model.reuse_count,
                                       fallbacks=model.fallback_count)
                        records[name].append(row)
                        if args.decode:
                            row["latent_file"] = f"{cache_mode}-{name}-{rep}.npz"
                            mx.savez(str(spool / row["latent_file"]), latents=output)
                        print(f"{cache_mode}/{name}: {seconds:.2f}s; relative L1 {row['relative_l1']:.6g}", flush=True)
                    del output
            reference = statistics.median(row["seconds"] for row in records["vanilla"])
            report["results"][cache_mode] = {name: {"runs": rows,
                "median_seconds": statistics.median(row["seconds"] for row in rows),
                "speedup": reference / statistics.median(row["seconds"] for row in rows)}
                for name, rows in records.items()}
            for model in modes.values():
                if isinstance(model, ExperimentalTransformer):
                    model.reset()
            del modes, model
            gc.collect()
            mx.clear_cache()
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        # Drop wrappers and compiled closures before loading VAE (staged memory).
        del tr, counted_forward, baselines
        gc.collect()
        mx.clear_cache()
        if args.decode:
            from mflux.models.common.vae.vae_util import VAEUtil
            from mflux.models.qwen21.latent_creator.qwen21_latent_creator import Qwen21LatentCreator
            from mflux.utils.image_util import ImageUtil
            require_idle(args.manager_url)
            vae = engine._load_vae(snapshot)

            def decode(path):
                data = mx.load(str(path))
                unpacked = Qwen21LatentCreator.unpack_latents(latents=data["latents"], height=args.height, width=args.width)
                decoded = VAEUtil.decode(vae=vae, latent=unpacked, tiling_config=None)
                mx.eval(decoded)
                return ImageUtil.to_pil(decoded)

            for cache_mode, results in report["results"].items():
                reference = decode(spool / f"{cache_mode}-reference.npz")
                reference.save(spool / f"{cache_mode}-reference.png")
                for name, result in results.items():
                    for rep, row in enumerate(result["runs"]):
                        require_idle(args.manager_url)
                        began = time.perf_counter()
                        image = decode(spool / row["latent_file"])
                        row["decode_seconds"] = time.perf_counter() - began
                        row["pixels_bit_exact"] = (image.mode == reference.mode and image.size == reference.size
                                                   and image.tobytes() == reference.tobytes())
                        image.save(spool / f"{cache_mode}-{name}-{rep}.png")
                        print(f"{cache_mode}/{name}/{rep}: pixels exact={row['pixels_bit_exact']}", flush=True)
                        del image
                        gc.collect()
                        mx.clear_cache()
                args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Report: {args.report}", flush=True)


if __name__ == "__main__":
    main()
