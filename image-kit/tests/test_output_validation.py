"""Invalid decoder values must not be published as successful black images."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mlx.core as mx
from PIL import Image
from mlx_image import engine


class OutputValidationTests(unittest.TestCase):
    def test_nonfinite_decode_preserves_existing_output_and_creates_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'image.png'
            output.write_bytes(b'previous result')
            for invalid in (float('nan'), float('inf'), float('-inf')):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'no image was saved'):
                    engine._save_png(mx.array([invalid]), output)
                self.assertEqual(output.read_bytes(), b'previous result')
                self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_finite_decode_writes_valid_png_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'image.png'
            image = Image.new('RGB', (2,2), color=(16,32,64))
            with patch.object(engine.ImageUtil, 'to_pil', return_value=image):
                engine._save_png(mx.array([0.,.5,1.]), output)
            with Image.open(output) as saved:
                self.assertEqual(saved.tobytes(), image.tobytes())
            self.assertEqual(list(Path(directory).iterdir()), [output])
