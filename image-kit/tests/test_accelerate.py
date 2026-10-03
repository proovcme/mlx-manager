"""Small Metal tests: no weights downloaded and no production model loaded."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mlx.core as mx
from mlx import nn

if not mx.metal.is_available():
    raise unittest.SkipTest("Metal parity requires an Apple Silicon GPU; hosted CPU contracts are checked separately")
from mflux.models.qwen21.model.qwen21_transformer.qwen21_attention import Qwen21Attention
from mflux.models.qwen21.model.qwen21_transformer.qwen21_transformer import Qwen21Transformer

from mlx_image import accelerate
from mlx_image.accelerate import ConditionedTransformer as ExperimentalTransformer, fused_rope


class FusedRopeTests(unittest.TestCase):
    def test_matches_reference_for_dtypes_and_noncontiguous_inputs(self):
        mx.random.seed(17)
        for dtype in (mx.float32, mx.float16, mx.bfloat16):
            for batch, tokens, heads, dim in ((1, 7, 2, 8), (2, 33, 3, 128)):
                with self.subTest(dtype=dtype, shape=(batch, tokens, heads, dim)):
                    x = mx.random.normal((batch, tokens * 2, heads, dim)).astype(dtype)[:, ::2]
                    angles = mx.random.normal((tokens, dim // 2))
                    cosine, sine = mx.cos(angles), mx.sin(angles)
                    expected = Qwen21Attention._apply_rope(x, cosine, sine)
                    actual = fused_rope(x, cosine, sine)
                    mx.eval(expected, actual)
                    self.assertTrue(mx.array_equal(actual, expected).item())

    def test_unknown_gpu_or_backend_does_not_enable_conditioning_reuse(self):
        for name,architecture,expected in [('Apple M4','applegpu_g16g',True),('Apple M1','applegpu_g13g',False),('Apple M4 Pro','applegpu_g16s',False),('Unknown','applegpu_g16g',False)]:
            with self.subTest(device=name),patch.object(mx,'device_info',return_value={'device_name':name,'architecture':architecture}),patch.object(mx,'default_device',return_value=mx.gpu),patch.object(mx,'__version__','0.32.2'):
                self.assertEqual(accelerate.exact_conditioning_supported(),expected)
        with patch.object(mx,'default_device',return_value=mx.cpu):
            self.assertFalse(accelerate.exact_conditioning_supported())
        with patch.object(mx,'__version__','unknown'):
            self.assertFalse(accelerate.exact_conditioning_supported())

    def test_invalid_angle_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            fused_rope(mx.zeros((1, 4, 2, 8)), mx.zeros((3, 4)), mx.zeros((3, 4)))


class PrefixReuseTests(unittest.TestCase):
    def setUp(self):
        mx.random.seed(19)
        self.tr = Qwen21Transformer(in_channels=64, out_channels=64, num_layers=2,
            attention_head_dim=32, num_attention_heads=2, context_in_dim=64,
            axes_dims_rope=(8, 12, 12), mlp_ratio=2)
        nn.quantize(self.tr, group_size=32, bits=4)
        self.tr.set_dtype(mx.bfloat16)
        mx.eval(self.tr.parameters())
        self.config = SimpleNamespace(width=64, height=64,
            scheduler=SimpleNamespace(sigmas=[0.9, 0.6, 0.3]))
        self.embeds = mx.random.normal((1, 7, 64)).astype(mx.bfloat16)
        self.mask = mx.ones((1, 7))
        self.hidden = mx.random.normal((1, 16, 64)).astype(mx.bfloat16)

    def test_capture_then_reuse_matches_joint_forward_at_each_timestep(self):
        for mode in ("rope", "prefix", "prefix-rope"):
            wrapper = ExperimentalTransformer(self.tr, mode)
            for t in range(3):
                # Change image input as well as timestep; text K/V must stay invariant.
                hidden = self.hidden + t * 0.125
                expected = self.tr(t, self.config, hidden, self.embeds, self.mask)
                actual = wrapper(t, self.config, hidden, self.embeds, self.mask)
                mx.eval(expected, actual)
                with self.subTest(mode=mode, t=t):
                    self.assertTrue(mx.array_equal(actual, expected).item())
            self.assertEqual(wrapper.reuse_count, 0 if mode == "rope" or not wrapper.reuse_supported else 2)

    def test_unverified_device_returns_original_forward_bit_for_bit(self):
        with patch.object(accelerate,'exact_conditioning_supported',return_value=False):
            wrapper=ExperimentalTransformer(self.tr)
        for t in range(3):
            expected=self.tr(t,self.config,self.hidden,self.embeds,self.mask)
            actual=wrapper(t,self.config,self.hidden,self.embeds,self.mask)
            mx.eval(expected,actual)
            self.assertTrue(mx.array_equal(actual,expected).item())
        self.assertEqual(wrapper.fallback_count,3)
        self.assertEqual(wrapper.capture_count,0)
        self.assertEqual(wrapper.reuse_count,0)

    def test_new_conditioning_and_explicit_reset_clear_prefix(self):
        wrapper = ExperimentalTransformer(self.tr, "prefix")
        mx.eval(wrapper(0, self.config, self.hidden, self.embeds, self.mask))
        new_embeds = self.embeds + 1
        expected = self.tr(1, self.config, self.hidden, new_embeds, self.mask)
        actual = wrapper(1, self.config, self.hidden, new_embeds, self.mask)
        mx.eval(expected, actual)
        self.assertTrue(mx.array_equal(actual, expected).item())
        self.assertEqual(wrapper.reuse_count, 0)
        wrapper.reset()
        self.assertIsNone(wrapper._kv)
        self.assertIsNone(wrapper._conditioning)

    def test_padding_falls_back_to_original(self):
        wrapper = ExperimentalTransformer(self.tr)
        mask = mx.array([[1, 1, 1, 1, 1, 0, 0]])
        expected = self.tr(0, self.config, self.hidden, self.embeds, mask)
        actual = wrapper(0, self.config, self.hidden, self.embeds, mask)
        mx.eval(expected, actual)
        self.assertTrue(mx.array_equal(actual, expected).item())
        self.assertEqual(wrapper.fallback_count, 1)
        self.assertIsNone(wrapper._kv)


if __name__ == "__main__":
    unittest.main()
