import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import core
import memory


class SafetyTests(unittest.TestCase):
    def setUp(self):
        patcher=patch.object(core.config,"EXTERNAL_ENABLED",True)
        patcher.start();self.addCleanup(patcher.stop)

    def test_size_units(self):
        self.assertEqual(memory.parse_size("15.7G"), int(15.7 * 1024**3))
        self.assertEqual(memory.parse_size("542.4M"), int(542.4 * 1024**2))

    def test_stop_refuses_active_chat(self):
        manager = core.Manager.__new__(core.Manager)
        manager._active_chat = 1
        with patch.object(memory, "listener_pid", return_value=123), \
             patch.object(memory, "launch_agent_pid", return_value=123), \
             patch.object(core, "run") as launchctl:
            with self.assertRaisesRegex(core.ManagerError, "active requests"):
                manager._stop_external()
            launchctl.assert_not_called()

    def test_stop_refuses_foreign_listener(self):
        manager = core.Manager.__new__(core.Manager)
        with patch.object(memory, "listener_pid", return_value=123), \
             patch.object(memory, "launch_agent_pid", return_value=456), \
             patch.object(core, "run") as launchctl:
            with self.assertRaisesRegex(core.ManagerError, "not owned"):
                manager._stop_external()
            launchctl.assert_not_called()

    def test_start_refuses_missing_data(self):
        manager = core.Manager.__new__(core.Manager)
        with patch.object(core.config, "EXTERNAL_MODEL", Path("/nonexistent-mlx-manager-test-model")), \
             patch.object(core, "run") as launchctl:
            with self.assertRaisesRegex(core.ManagerError, "unavailable"):
                manager._start_external()
            launchctl.assert_not_called()

    def test_generate_refuses_running_external_chat(self):
        manager = core.Manager.__new__(core.Manager)
        manager._mu = threading.RLock()
        manager._mode = "image"
        manager._heavy_held = True
        manager._worker = None
        manager._reconcile = lambda: None
        with patch.object(memory, "listener_pid", return_value=123), \
             patch.object(core.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(core.ManagerError, "External model is still running"):
                manager.generate({"prompt": "test"})
            spawn.assert_not_called()

    def test_chat_does_not_start_external_from_idle(self):
        manager = core.Manager.__new__(core.Manager)
        manager._mu = threading.RLock()
        manager._mode = "idle"
        manager._reconcile = lambda: None
        with patch.object(manager, "_ensure_proxy") as proxy:
            with self.assertRaisesRegex(core.ManagerError, "External model is unavailable"):
                manager.chat_enter()
            proxy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
