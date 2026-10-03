import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch
from mlx_image import session as module
from mlx_image.types import Job


class SessionTests(unittest.TestCase):
    def test_series_retains_models_and_saves_each_image_before_next_job(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            session=module.ImageSession()
            transformer,vae=Mock(),Mock()
            snapshot=Path(folder)
            stack.enter_context(patch.object(module.engine,'_snapshot',return_value=snapshot))
            loads=stack.enter_context(patch.object(module.engine,'_load_transformer',return_value=transformer))
            vaes=stack.enter_context(patch.object(module.engine,'_load_vae',return_value=vae))
            stack.enter_context(patch.object(module.engine,'_denoise',return_value=Mock()))
            stack.enter_context(patch.object(module.engine.Qwen21LatentCreator,'unpack_latents',return_value=Mock()))
            stack.enter_context(patch.object(module.engine.VAEUtil,'decode',return_value=Mock()))
            saved=stack.enter_context(patch.object(module.engine,'_save_png'))
            for name in ('eval','clear_cache','reset_peak_memory'):
                stack.enter_context(patch.object(module.mx,name))
            stack.enter_context(patch.object(module.mx,'get_peak_memory',return_value=1))
            def encode(prompt,path):
                session.embeds=session.mask=Mock()
                return False
            stack.enter_context(patch.object(session,'_encode',side_effect=encode))
            for seed in (1,2):
                job=Job(1,'A cat.',snapshot/f'{seed}.png',512,512,2,seed,1)
                result=session.run_jobs([job])
                self.assertEqual(len(result.completed),1)
                self.assertEqual(saved.call_count,seed)
                self.assertIs(session.transformer,transformer)
                self.assertIs(session.vae,vae)
            loads.assert_called_once();vaes.assert_called_once()
            session.close()
            self.assertIsNone(session.transformer)
            self.assertIsNone(session.vae)
            self.assertIsNone(session.embeds)

    def test_exact_prompt_reuse_and_close_invalidate_conditioning(self):
        session=module.ImageSession()
        session.prompt='cat';session.embeds=Mock();session.mask=Mock()
        with patch.object(module.engine,'_load_text_encoder') as load, patch.object(module.mx,'clear_cache'):
            self.assertTrue(session._encode('cat',Path('.')))
            load.assert_not_called()
            session.close()
            self.assertIsNone(session.prompt)
            self.assertIsNone(session.embeds)

    def test_invalid_job_releases_resident_models(self):
        session=module.ImageSession();session.transformer=Mock();session.vae=Mock()
        job=Job(1,'',Path('out.png'),512,512,2,1,1)
        with patch.object(module.mx,'clear_cache'),patch.object(module.mx,'reset_peak_memory'):
            result=session.run_jobs([job])
        self.assertEqual(len(result.failed),1)
        self.assertIsNone(session.transformer)
        self.assertIsNone(session.vae)
