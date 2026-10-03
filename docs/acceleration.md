# Exact conditioning reuse and resident series

The manager enables Qwen 2.1 text-prefix K/V reuse and a fused RoPE kernel for its pinned MFLUX 0.20 / MLX 0.32.2 backend. The prefix is independent of the image denoising timestep. Quantized output projection row counts and the original multiply/add order are retained. Padded prompts and multi-image batches fall back to the original joint forward.

This acceleration changes neither weights, precision, steps, seed nor guidance. It is separate from the optional **Balanced** noise cache, which approximates omitted transformer evaluations and may change images. New manager drafts and API requests default to cache `off`. Saved drafts retain the user's earlier choice.

An explicit series keeps its transformer, VAE and last exact prompt embeddings in one worker. It resets conditioning state for every image and saves each PNG before starting the next. A changed prompt or checkpoint invalidates resident state. Completion, failure and cancellation close the session. A text model used for prompt expansion is unloaded before the image session starts.

## Local measurements, 2026-10-03

M4, 24 GB; local Q4 snapshot; 207-token neutral prompt; cache off. The manager and competing model processes were idle and the manager's workload lock was held throughout. One warm-up per mode was excluded from the single-image and denoising medians; orders were interleaved.

| Scope | Before | After | Time reduction | Samples |
| --- | ---: | ---: | ---: | --- |
| Warm denoising, 512×512, 6 steps | 36.63 s | 30.47 s | 16.8% | 3 per mode |
| Full single pipeline, 512×512, 4 steps | 31.37 s | 28.73 s | 8.4% | 3 per mode |
| Two-image series, 512×512, 4 steps each | 62.34 s | 50.42 s | 19.1% | one pair of sequences |

All measured PNG pixel buffers were identical; the denoising comparison also matched latent arrays bit for bit. The two series images used distinct seeds. These bounded tests do not establish the speedup or numerical parity for every resolution, prompt, dependency version or checkpoint. The series result is a single measurement, not a median. Peak MLX allocation increased from 5.87 GiB for the staged single pipeline to 9.71 GiB for the resident series; process footprint and system swap are different metrics.

Raw timing and parity evidence: [denoising](benchmarks/qwen21-conditioning-512.json), [full pipeline](benchmarks/qwen21-pipeline-512.json). Reports omit prompt contents and machine paths.

## Native-size release gate

A separate full-pipeline check at **1152×768, 20 steps, seed 1977, cache off** matched both latent arrays and PNG pixels bit for bit. Each mode was run once with freshly loaded models; no warm-up or repeated median was used. Baseline took **395.64 s**, accelerated **375.87 s** (5.0% less elapsed time in this check). Peak MLX allocation was **17.07 / 17.21 GiB**. These are control-run timings, not a statistically established speedup.

[Native-size parity report](benchmarks/qwen21-production-parity.json).

The resident first image at 20 steps also matched the staged reference PNG. Its peak MLX allocation was **17.17 GiB**. A separate next-image probe at 4 steps, seed 1978, matched the staged PNG byte for byte at the pixel-buffer level; both decodes contained finite values. The reused prompt took less than a microsecond to retrieve and session close released its models. These probes validate native-size conditioning reuse and memory, not the throughput of two complete 20-step images. The resident first image took 400.31 s; the separate 4-step reused/staged probes took 79.42 / 89.93 s. They are single control samples with different warm states, not benchmark medians.

An initial one-step next-image probe emitted a nonfinite-decode warning and was excluded from the quality gate. The follow-up probe used a one-step image only to prepare resident state, then checked finite 4-step outputs against the staged reference. One-step renders should not be used as quality or parity evidence. The default reproducible series check below uses a 20-step first image and a 4-step next image and rejects nonfinite quality-check outputs.

[Native resident-series report](benchmarks/qwen21-production-series.json).


To repeat with installed dependencies and local weights, run from `image-kit`:

```sh
python -m experiments.benchmark_qwen21 --width 512 --height 512 --steps 6 --reps 3 --modes prefix-rope --cache-modes off --decode --fail-on-drift --prompt-file experiments/prompts/teapot.txt
python -m experiments.benchmark_pipeline --prompt-file experiments/prompts/teapot.txt
python -m experiments.check_production_parity --prompt-file experiments/prompts/teapot.txt
python -m experiments.check_series_parity --prompt-file experiments/prompts/teapot.txt
```

The commands use the published neutral [teapot prompt](../image-kit/experiments/prompts/teapot.txt), matching the 207-token measurements. Omitting `--prompt-file` uses a shorter built-in prompt and will yield different timings. Outputs and raw reports remain under ignored `local/`.

For a baseline or troubleshooting, set `MLX_MANAGER_IMAGE_ACCELERATION=false` before starting the manager. Resident-series reuse remains independent of that setting.
