import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import core
import config
from prompt_enhancer import PromptEnhancer, clean_prompt


class PromptTests(unittest.TestCase):
    def test_thinking_is_removed_and_rendered_text_keeps_original_script(self):
        self.assertEqual(clean_prompt('<think>reason</think>A poster reads "Котик".', 'stop'), 'A poster reads "Котик".')

    def test_truncated_empty_and_unfinished_thinking_are_rejected(self):
        for text, finish in [('A cat', 'length'), ('', 'stop'), ('<think>reason', 'stop'), ('A cat', None)]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                clean_prompt(text, finish)

    def test_dedicated_enhancer_json_is_supported(self):
        self.assertEqual(clean_prompt('```json\n{"rewritten_prompt":"A cat.","wh_ratio":"3:2"}\n```','stop'), 'A cat.')

    def fixture(self, folder, count=1, generate=True, expand=True):
        manager = SimpleNamespace(_mu=threading.RLock(), _mode='model', _worker=None)
        manager._runtime = SimpleNamespace(status=lambda: {'state':'ready'})
        manager.start_model = Mock()
        manager.close_image_session = Mock()
        manager.set_mode = Mock()
        def stream(spec):
            yield b'data: {"choices":[{"delta":{"content":"A cat in soft window light."}}]}\n'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n'
            yield b'data: [DONE]\n'
        manager.stream_chat = stream
        calls = []
        def generate_image(spec):
            calls.append(spec)
            manager._job = {'id':str(len(calls)), 'state':'done'}
            return dict(manager._job)
        manager.generate = generate_image
        with patch.object(config,'DATA_ROOT',Path(folder)):
            enhancer = PromptEnhancer(manager)
        enhancer.thread = threading.current_thread()
        enhancer.job = dict(id='abc',state='running',stage='loading',expand=expand,generate=generate,
                            original_prompt='нарисуй котика',prompt='',model='text',model_name='Test model',
                            count=count,index=0,completed=0,images=[],error=None)
        return manager, enhancer, calls

    def test_text_model_is_unloaded_before_images_and_prompt_expands_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            manager, enhancer, calls = self.fixture(folder, count=3)
            original = manager.generate
            manager.generate = lambda spec: (self.assertIn(('image',),[c.args for c in manager.set_mode.call_args_list]), original(spec))[1]
            enhancer.run('text','mlx',dict(width=512,height=512,steps=1,seed=2**32-1,cache='off'))
            self.assertEqual(enhancer.job['state'],'done')
            self.assertEqual(enhancer.job['completed'],3)
            self.assertIsNone(enhancer.job['error'])
            manager.close_image_session.assert_called_once()
            self.assertTrue(all(c['_session'] for c in calls))
            self.assertEqual([c['seed'] for c in calls],[2**32-1,0,1])
            self.assertTrue(all(c['prompt']=='A cat in soft window light.' for c in calls))
            self.assertEqual(calls[0]['prompt_expansion']['original_prompt'],'нарисуй котика')
            manager.start_model.assert_called_once()
            self.assertEqual(enhancer.path.stat().st_mode & 0o777,0o600)

    def test_unload_failure_is_visible_and_never_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            manager, enhancer, calls = self.fixture(folder, expand=False)
            manager.close_image_session.side_effect = RuntimeError('worker did not exit')
            enhancer.run(None, None, dict(width=512,height=512,steps=1,seed=42,cache='off'))
            self.assertEqual(enhancer.job['state'],'failed')
            self.assertIn('worker did not exit',enhancer.job['error'])

    def test_failed_expansion_unloads_model_and_never_starts_image(self):
        with tempfile.TemporaryDirectory() as folder:
            manager, enhancer, calls = self.fixture(folder)
            manager.stream_chat=lambda spec: iter([b'data: {"choices":[{"delta":{"content":"Partial"},"finish_reason":"length"}]}'])
            enhancer.run('text','mlx',dict(width=512,height=512,steps=1,seed=0,cache='off'))
            self.assertEqual(enhancer.job['state'],'failed')
            self.assertFalse(calls)
            manager.set_mode.assert_called_once_with('idle')

    def test_cancel_stops_remaining_images_and_keeps_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            manager, enhancer, calls = self.fixture(folder,count=3,expand=False)
            original=manager.generate
            def generate(spec):
                result=original(spec)
                if len(calls)==2:enhancer.cancelled.set()
                return result
            manager.generate=generate
            enhancer.run(None,'auto',dict(width=512,height=512,steps=1,seed=0,cache='off'))
            self.assertEqual(enhancer.job['state'],'cancelled')
            self.assertEqual(enhancer.job['completed'],2)
            self.assertEqual(len(calls),2)
            manager.start_model.assert_not_called()

    def test_reservation_rejects_other_threads_but_allows_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            manager, enhancer, _=self.fixture(folder)
            actual=core.Manager.__new__(core.Manager);actual._enhancer=enhancer
            actual._prompt_guard()
            enhancer.thread=None
            with self.assertRaises(core.ManagerError):actual._prompt_guard()

    def test_restart_marks_pending_series_interrupted_without_resuming_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'prompt-enhancement.json'
            path.write_text(json.dumps({'state':'running','completed':2}))
            with patch.object(config,'DATA_ROOT',Path(folder)):
                enhancer=PromptEnhancer(SimpleNamespace(_mu=threading.RLock()))
            self.assertEqual(enhancer.job['state'],'interrupted')
            self.assertEqual(enhancer.job['completed'],2)
            self.assertIsNone(enhancer.thread)
