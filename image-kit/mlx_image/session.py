"""One sequential image session with resident transformer/VAE and exact conditioning reuse."""
from __future__ import annotations

import gc
import time
from datetime import datetime
from pathlib import Path

import mlx.core as mx

from mlx_image import engine
from mlx_image.types import Failure, Result, Summary, validate_job


class ImageSession:
    """Owns models for one explicit series; close before loading a text model.

    Only the last exact prompt is retained. A changed prompt unloads all resident
    image models before loading the text encoder. No approximated noise is reused
    by this class: denoising and its optional cache remain the caller's settings.
    """

    def __init__(self, *, acceleration=False):
        self.acceleration = acceleration
        self.snapshot = None
        self.prompt = None
        self.embeds = self.mask = self.transformer = self.vae = None

    def close(self):
        self.prompt = None
        self.embeds = self.mask = self.transformer = self.vae = None
        gc.collect()
        mx.clear_cache()

    def _encode(self, prompt, snapshot):
        if prompt == self.prompt and self.embeds is not None:
            return True
        self.close()
        te = qwen = None
        try:
            te = engine._load_text_encoder(snapshot)
            qwen = engine.QwenImage21.__new__(engine.QwenImage21)
            super(engine.QwenImage21, qwen).__init__()
            engine.Qwen21Initializer._init_config(qwen, engine.ModelConfig.qwen_image_21())
            engine.Qwen21Initializer._init_tokenizers(qwen, str(snapshot))
            self.embeds, self.mask = engine.Qwen21PromptEncoder.encode_prompt(
                prompt=prompt, prompt_cache=qwen.prompt_cache,
                tokenizer=qwen.tokenizers['qwen21'], text_encoder=te)
            mx.eval(self.embeds, self.mask)
            self.prompt = prompt
        finally:
            del te, qwen
            gc.collect()
            mx.clear_cache()
        return False

    def run_jobs(self, jobs, *, model_path=None, on_complete=None, on_failure=None,
                 on_stage_event=None, on_step=None, on_forward=None, on_stage_timing=None,
                 cache_config=None, progress=False, show_library_progress=False):
        if len(jobs) != 1:
            raise ValueError('ImageSession accepts one sequential job at a time')
        job = jobs[0]
        summary = Summary(total=1)
        began = time.monotonic()
        mx.reset_peak_memory()
        stage = 'validation'
        def event(name):
            if on_stage_event:
                on_stage_event(name, job.index)
        def timing(name, since):
            if on_stage_timing:
                on_stage_timing(job.index, name, time.monotonic()-since)
        try:
            validate_job(job)
            stage = 'model setup'
            snapshot = engine._snapshot(model_path)
            if snapshot != self.snapshot:
                self.close()
                self.snapshot = snapshot
            event('model_ready')
            stage = 'prompt encoding'
            event('text_encoder_ready')
            encoded_at = time.monotonic()
            reused = self._encode(job.prompt, snapshot)
            timing('prompt_encoding', encoded_at)
            event('prompt_reused' if reused else 'prompt_encoded')
            stage = 'transformer'
            if self.transformer is None:
                loaded_at = time.monotonic()
                transformer = engine._load_transformer(snapshot)
                if self.acceleration:
                    from mlx_image.accelerate import ConditionedTransformer
                    transformer = ConditionedTransformer(transformer)
                self.transformer = transformer
                timing('transformer_loading', loaded_at)
            elif self.acceleration:
                self.transformer.reset()
            event('transformer_ready')
            event('denoising_start')
            stage = 'denoising'
            denoised_at = time.monotonic()
            latents = engine._denoise(job, self.transformer, self.embeds, self.mask,
                engine.ModelConfig.qwen_image_21(),
                on_step=(lambda step,total:on_step(job.index,step,total)) if on_step else None,
                cache_config=cache_config, show_library_progress=show_library_progress,
                on_forward=(lambda computed:on_forward(job.index,computed)) if on_forward else None)
            mx.eval(latents)
            timing('denoising', denoised_at)
            stage = 'VAE'
            if self.vae is None:
                loaded_at = time.monotonic()
                self.vae = engine._load_vae(snapshot)
                timing('vae_loading', loaded_at)
            event('vae_ready')
            event('decoding_start')
            decoded_at = time.monotonic()
            unpacked = engine.Qwen21LatentCreator.unpack_latents(latents=latents,height=job.height,width=job.width)
            decoded = engine.VAEUtil.decode(vae=self.vae,latent=unpacked,tiling_config=None)
            mx.eval(decoded)
            timing('vae_decode',decoded_at)
            stage = 'save'
            engine._save_png(decoded,job.output)
            result=Result(job=job,timestamp=datetime.now().astimezone().isoformat(timespec='seconds'),
                          elapsed_seconds=time.monotonic()-began,peak_metal_gb=mx.get_peak_memory()/2**30)
            summary.completed.append(result)
            if on_complete:
                on_complete(result)
            del latents,unpacked,decoded
        except KeyboardInterrupt:
            summary.interrupted=True
            self.close()
        except Exception as exc:
            message=str(exc) if stage=='validation' else f'{stage}: {type(exc).__name__}'
            failure=Failure(job.index,message)
            summary.failed.append(failure)
            if on_failure:
                on_failure(failure)
            self.close()
        finally:
            gc.collect()
            mx.clear_cache()
            summary.elapsed_seconds=time.monotonic()-began
        return summary
