"""Exact Qwen 2.1 conditioning reuse for the supported mflux 0.20 backend.

Retains the original quantized projection shapes and arithmetic order. Padding
and multi-image batches fall back to the original joint transformer path.
"""

from importlib.metadata import version, PackageNotFoundError

import mlx.core as mx
from mlx import nn
from mlx.core.fast import scaled_dot_product_attention as sdpa
from mflux.models.qwen21.model.qwen21_transformer.qwen21_attention import Qwen21Attention
from mflux.models.qwen21.model.qwen21_transformer.qwen21_transformer import Qwen21Transformer


_rope_kernel = mx.fast.metal_kernel(
    name="qwen21_conditioned_rope",
    input_names=["x", "cosine", "sine"],
    output_names=["out"],
    source="""
        uint p = thread_position_in_grid.x;
        uint half_dim = x_shape[3] / 2;
        uint head_count = x_shape[2];
        uint token = (p / (head_count * half_dim)) % x_shape[1];
        uint f = token * half_dim + p % half_dim;
        float a = float(x[2 * p]);
        float b = float(x[2 * p + 1]);
        float c = float(cosine[f]);
        float s = float(sine[f]);
        float ac = precise::fma(a, c, 0.0f);
        float bs = precise::fma(b, s, 0.0f);
        float as = precise::fma(a, s, 0.0f);
        float bc = precise::fma(b, c, 0.0f);
        out[2 * p] = T(ac - bs);
        out[2 * p + 1] = T(as + bc);
    """,
)


def fused_rope(x, cosine, sine):
    if x.ndim != 4 or x.shape[-1] % 2:
        raise ValueError("RoPE expects [batch, tokens, heads, even head_dim]")
    if cosine.shape != (x.shape[1], x.shape[-1] // 2) or sine.shape != cosine.shape:
        raise ValueError("RoPE angles do not match activation shape")
    return _rope_kernel(
        inputs=[mx.contiguous(x), mx.contiguous(cosine), mx.contiguous(sine)],
        template=[("T", x.dtype)],
        grid=(x.size // 2, 1, 1),
        threadgroup=(256, 1, 1),
        output_shapes=[x.shape],
        output_dtypes=[x.dtype],
    )[0]


def exact_conditioning_supported():
    """Only the pinned backend and GPU with measured bitwise parity are enabled.

    M1 CI demonstrated that splitting the same attention arithmetic can select
    different reductions on another GPU. Unknown devices keep the joint path.
    """
    if mx.default_device() != mx.gpu or mx.__version__ != '0.32.2':
        return False
    try:
        if version('mflux') != '0.20.0':
            return False
    except PackageNotFoundError:
        return False
    info = mx.device_info()
    return info.get('device_name') == 'Apple M4' and info.get('architecture') == 'applegpu_g16g'


class ConditionedTransformer:
    """One denoising job's wrapper; reset between images, even for equal prompts.

No global monkey patches or changes to the model's weights/compiled functions.
Modes isolate fused RoPE from prefix reuse so timing can attribute each gain.
"""

    def __init__(self, transformer, mode="prefix-rope"):
        if mode not in ("rope", "prefix", "prefix-rope"):
            raise ValueError("unknown optimization mode")
        self.model = transformer
        self.reuse_supported = exact_conditioning_supported()
        self.prefix = mode != "rope"
        self.rope = fused_rope if mode != "prefix" else Qwen21Attention._apply_rope
        self._first = mx.compile(self._joint)
        self._later = mx.compile(self._image_only)
        self.reset()

    def reset(self):
        self._kv = None
        self._conditioning = None
        self._shape = None
        self.capture_count = 0
        self.reuse_count = 0
        self.fallback_count = 0

    def __call__(self, t, config, hidden_states, encoder_hidden_states, encoder_hidden_states_mask=None):
        if not self.reuse_supported:
            self.fallback_count += 1
            return self.model(t, config, hidden_states, encoder_hidden_states, encoder_hidden_states_mask)
        shape = (config.width, config.height, hidden_states.shape, encoder_hidden_states.shape)
        if (self._conditioning is None or self._shape != shape
                or self._conditioning[0] is not encoder_hidden_states
                or self._conditioning[1] is not encoder_hidden_states_mask):
            self.reset()
            self._conditioning = (encoder_hidden_states, encoder_hidden_states_mask)
            self._shape = shape
        tr = self.model
        cos, sin, mask = tr._geometry(encoder_hidden_states.shape[1], config.height // 16,
                                      config.width // 16, encoder_hidden_states_mask)
        if mask is not None or hidden_states.shape[0] != 1:
            self.fallback_count += 1
            return tr(t, config, hidden_states, encoder_hidden_states, encoder_hidden_states_mask)
        timestep = tr._compute_timestep(t, config)
        rows = mx.concatenate([timestep, mx.zeros((1,), dtype=timestep.dtype)])
        if self._kv is None or not self.prefix:
            noise, kv = self._first(hidden_states, encoder_hidden_states, rows, cos, sin)
            if self.prefix:
                # Realize K/V once. Retaining a lazy graph could retain the joint activations.
                mx.eval(noise, kv)
                self._kv = kv
                self.capture_count += 1
            return noise
        self.reuse_count += 1
        return self._later(hidden_states, rows, cos, sin, self._kv,
                           encoder_hidden_states.shape[1])

    def _attention(self, attn, hidden, cos, sin, text_len, cached=None):
        shape = (*hidden.shape[:-1], attn.num_heads, attn.head_dim)
        q = self.rope(attn.norm_q(mx.reshape(attn.to_q(hidden), shape)), cos, sin)
        k = self.rope(attn.norm_k(mx.reshape(attn.to_k(hidden), shape)), cos, sin)
        v = mx.reshape(attn.to_v(hidden), shape)
        q, k, v = [mx.transpose(a, (0, 2, 1, 3)) for a in (q, k, v)]
        scale = attn.head_dim**-0.5
        if cached is None:
            kv = (mx.contiguous(k[:, :, :text_len]), mx.contiguous(v[:, :, :text_len]))
            text = sdpa(q[:, :, :text_len], k[:, :, :text_len], v[:, :, :text_len],
                        scale=scale, mask="causal")
            image = sdpa(q[:, :, text_len:], k, v, scale=scale)
            output = mx.concatenate([text, image], axis=2)
        else:
            kv = cached
            k = mx.concatenate([cached[0], k], axis=2)
            v = mx.concatenate([cached[1], v], axis=2)
            output = sdpa(q, k, v, scale=scale)
        output = mx.transpose(output, (0, 2, 1, 3))
        output = mx.reshape(output, (*output.shape[:-2], attn.num_heads * attn.head_dim))
        return attn.to_out[0](output), kv

    def _block(self, block, hidden, mod1, mod2, cos, sin, text_len, cached=None):
        scale1, gate1 = mx.split(mod1, 2, axis=-1)
        scale2, gate2 = mx.split(mod2, 2, axis=-1)
        output, kv = self._attention(block.attn, block.img_norm1(hidden) * (1 + scale1),
                                     cos, sin, text_len, cached)
        hidden = hidden + nn.tanh(gate1) * output
        hidden = hidden + nn.tanh(gate2) * block.img_mlp(block.img_norm2(hidden) * (1 + scale2))
        return hidden, kv

    def _joint(self, hidden, embeds, rows, cos, sin):
        tr = self.model
        text_len, image_len = embeds.shape[1], hidden.shape[1]
        temb = tr.time_text_embed(rows)
        mods = [tr._select_modulation_rows(m, text_len, image_len)
                for m in mx.split(tr.modulation(temb), 2, axis=-1)]
        hidden = mx.concatenate([tr.txt_in(embeds), tr.img_in(hidden)], axis=1)
        caches = []
        for block in tr.transformer_blocks:
            hidden, kv = self._block(block, hidden, *mods, cos, sin, text_len)
            caches.append(kv)
        scale = tr._select_modulation_rows(tr.norm_out.linear(nn.silu(temb)), text_len, image_len)
        return tr.proj_out(tr.norm_out(hidden, scale))[:, text_len:], caches

    def _image_only(self, hidden, rows, cos, sin, caches, text_len):
        tr = self.model
        temb = tr.time_text_embed(rows)
        mods = [m[0][None, None, :] for m in mx.split(tr.modulation(temb), 2, axis=-1)]
        hidden = tr.img_in(hidden)
        for block, kv in zip(tr.transformer_blocks, caches, strict=True):
            hidden, _ = self._block(block, hidden, *mods, cos[text_len:], sin[text_len:], text_len, kv)
        scale = tr.norm_out.linear(nn.silu(temb))[0][None, None, :]
        hidden = tr.norm_out(hidden, scale)
        # Quantized matmul may choose a different kernel for very short sequences.
        # Match the original joint row count at the output projection.
        hidden = mx.concatenate([mx.zeros((1, text_len, hidden.shape[-1]), dtype=hidden.dtype), hidden], axis=1)
        return tr.proj_out(hidden)[:, text_len:]
