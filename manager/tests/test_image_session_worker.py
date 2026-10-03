import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import core


class ResidentWorkerTests(unittest.TestCase):
    def fixture(self,folder):
        manager=core.Manager.__new__(core.Manager)
        manager._mu=threading.RLock()
        worker=Mock();worker.poll.return_value=None
        worker.stdin.closed=False
        manager._worker=manager._session_worker=worker
        manager._prompt_guard=Mock();manager._save_job=Mock()
        output=Path(folder)/'out.png';output.write_bytes(b'png')
        manager._job=dict(id='abc',state='running',output=str(output),cancel_reason=None)
        return manager,worker

    def test_completed_image_keeps_worker_for_next_image(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,worker=self.fixture(folder)
            (Path(folder)/'image-abc-progress.json').write_text(json.dumps({'stage':'done'}))
            with patch.object(core.config,'DATA_ROOT',Path(folder)),patch.object(core.memory,'system_memory',return_value={'swap_used_bytes':0}):
                manager._watch_job(worker,'abc',True)
            self.assertEqual(manager._job['state'],'done')
            self.assertIs(manager._worker,worker)
            worker.wait.assert_not_called()
            manager.close_image_session()
            worker.stdin.close.assert_called_once()
            worker.wait.assert_called_once_with(timeout=30)
            self.assertIsNone(manager._worker)
            self.assertIsNone(manager._session_worker)

    def test_earlier_job_completion_cannot_overwrite_next_job(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,worker=self.fixture(folder)
            manager._job['id']='next'
            (Path(folder)/'image-abc-progress.json').write_text('{"stage":"done"}')
            with patch.object(core.config,'DATA_ROOT',Path(folder)),patch.object(core.memory,'system_memory',return_value={'swap_used_bytes':0}):
                manager._watch_job(worker,'abc',True)
            self.assertEqual(manager._job['state'],'running')
            manager._save_job.assert_not_called()
