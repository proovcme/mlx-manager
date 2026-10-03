import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import core
from progress import Recorder, from_log


class ProgressTests(unittest.TestCase):
    def test_snapshots_update_each_step_without_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "progress.json"
            recorder = Recorder(path, 20)
            recorder.event("denoising_start", 1)
            recorder.forward(1, True)
            recorder.step(1, 1, 20)
            first = json.loads(path.read_text())
            self.assertEqual((first["step"], first["computed"], first["stage"]), (1, 1, "denoising"))
            recorder.forward(1, False)
            recorder.step(1, 2, 20)
            second = json.loads(path.read_text())
            self.assertEqual((second["step"], second["skipped"]), (2, 1))
            self.assertNotIn("prompt", second)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_worker_log_uses_latest_step(self):
        state = from_log("Generating\nDenoising 5/20\nDenoising 10/20\n")
        self.assertEqual((state["step"], state["total"], state["stage"]), (10, 20, "denoising"))
        self.assertEqual(from_log("Generating\nDecoding\n✓ Saved")['stage'], 'done')


class StreamingTests(unittest.TestCase):
    def manager(self):
        manager = core.Manager.__new__(core.Manager)
        manager._mu = threading.RLock()
        manager._active_chat = 0
        manager._mode = "model"
        manager._reconcile = Mock()
        manager._runtime = Mock()
        return manager

    def test_stream_holds_active_request_until_closed(self):
        manager = self.manager()
        manager._runtime.open_chat.return_value = io.BytesIO(b'data: {"choices":[]}\n\ndata: [DONE]\n\n')
        stream = manager.stream_chat({"messages": [{"role": "user", "content": "test"}]})
        self.assertTrue(next(stream).startswith(b'data:'))
        self.assertEqual(manager._active_chat, 1)
        stream.close()
        self.assertEqual(manager._active_chat, 0)
        manager._runtime.open_chat.assert_called_once_with([{"role": "user", "content": "test"}], 1024, 0.7, stream=True)

    def test_upstream_error_releases_active_request(self):
        manager = self.manager()
        manager._runtime.open_chat.side_effect = RuntimeError("not ready")
        stream = manager.stream_chat({"messages": [{"role": "user", "content": "test"}]})
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            next(stream)
        self.assertEqual(manager._active_chat, 0)


if __name__ == "__main__":
    unittest.main()
