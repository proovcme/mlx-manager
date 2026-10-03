import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import catalog
import core
import memory
from runtime import ModelRuntime


class CatalogTests(unittest.TestCase):
    def make_model(self, root, name="model"):
        path = root / name
        path.mkdir()
        (path / "config.json").write_text(json.dumps({"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"]}))
        (path / "tokenizer_config.json").write_text("{}")
        (path / "model.safetensors").write_bytes(b"test fixture")
        return path

    def test_incomplete_shards_and_embedding_are_not_chat_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.make_model(Path(temporary))
            self.assertIsNotNone(catalog.model_info(path))
            self.assertIsNone(catalog.model_info(path, "Qwen3-Embedding"))
            (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "missing.safetensors"}}))
            self.assertIsNone(catalog.model_info(path))

    def test_symlink_aliases_share_identity_and_refresh_finds_new_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_model(root)
            (root / "alias").symlink_to(path, target_is_directory=True)
            with patch.object(catalog, "roots", return_value=[root]), patch.object(catalog, "runtimes", return_value={"mlx": {"id": "mlx"}}), patch.object(catalog.config, "image_snapshot", return_value=None):
                self.assertEqual(len(catalog.discover()["models"]), 1)
                self.make_model(root, "second")
                self.assertEqual(len(catalog.discover()["models"]), 2)

    def test_vision_needs_compatible_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.make_model(Path(temporary))
            (path / "config.json").write_text(json.dumps({"model_type": "qwen3_vl", "vision_config": {"hidden_size": 128}}))
            self.assertEqual(catalog.model_info(path)["backends"], ["omlx"])


class RuntimeSafetyTests(unittest.TestCase):
    def manager(self):
        manager = core.Manager.__new__(core.Manager)
        manager._mu = threading.RLock()
        manager._active_chat = 0
        manager._worker = None
        manager._mode = "idle"
        manager._runtime = Mock()
        manager._runtime.running.return_value = False
        manager._reconcile = lambda: None
        manager.model_catalog = Mock(return_value={"models": [{"id": "valid", "available": True, "kind": "chat", "backends": ["mlx"]}], "runtimes": [{"id": "mlx", "executable": "fixture"}]})
        return manager

    def test_busy_heavy_lock_prevents_launch_and_model_stop(self):
        manager = self.manager()
        manager._lock_heavy = Mock(side_effect=core.ManagerError("Heavy-memory lock held"))
        with patch.object(manager, "_stop_external_chat") as stop:
            with self.assertRaisesRegex(core.ManagerError, "lock held"):
                manager.start_model("valid")
            stop.assert_not_called()
            manager._runtime.start.assert_not_called()
            manager._runtime.stop.assert_not_called()

    def test_arbitrary_model_path_is_rejected(self):
        manager = self.manager()
        with self.assertRaisesRegex(core.ManagerError, "unavailable"):
            manager.start_model("/arbitrary/path")
        manager._runtime.start.assert_not_called()

    def test_chat_validation_happens_before_model_request(self):
        manager = self.manager()
        with self.assertRaisesRegex(core.ManagerError, "Invalid chat"):
            manager.chat({"messages": [{"role": "user", "content": {"bad": "object"}}]})
        manager._runtime.chat.assert_not_called()

    def test_foreign_port_is_not_adopted_or_killed(self):
        runtime = ModelRuntime()
        with patch.object(memory, "listener_pid", return_value=123), patch("runtime.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                runtime.start({"id": "fixture", "path": "/unused"}, "mlx", "fixture")
            spawn.assert_not_called()


class DeletionSafetyTests(unittest.TestCase):
    def manager(self, path):
        manager = core.Manager.__new__(core.Manager)
        manager._mu = threading.RLock()
        manager._mode = "idle"
        manager._active_chat = 0
        manager._worker = None
        manager._reconcile = Mock()
        manager._external_reservation = Mock(return_value=False)
        manager._lock_heavy = Mock()
        manager._unlock_heavy = Mock()
        manager._delete_plans = {}
        manager.model_catalog = Mock(return_value={"models": [{"id": "fixture", "name": "Fixture", "path": str(path), "kind": "chat"}]})
        return manager

    def test_cached_model_preview_includes_blobs_and_all_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "models--test--fixture"
            snapshot = repo / "snapshots" / "revision"
            snapshot.mkdir(parents=True)
            (snapshot / "config.json").write_text("{}")
            (repo / "blobs").mkdir()
            (repo / "blobs" / "weight").write_bytes(b"12345")
            (snapshot / "model.safetensors").symlink_to(repo / "blobs" / "weight")
            plan = self.manager(snapshot).preview_delete("fixture")
            self.assertEqual(plan["path"], str(repo.resolve()))
            self.assertTrue(plan["all_snapshots"])
            self.assertEqual(plan["bytes"], 7)

    def test_confirmation_and_busy_reservation_prevent_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model"
            path.mkdir()
            (path / "config.json").write_text("{}")
            manager = self.manager(path)
            plan = manager.preview_delete("fixture")
            with self.assertRaisesRegex(core.ManagerError, "does not match"):
                manager.trash_model(plan["token"], "wrong")
            manager._external_reservation.return_value = True
            with self.assertRaisesRegex(core.ManagerError, "benchmarks"):
                manager.trash_model(plan["token"], "Fixture")
            manager._lock_heavy.assert_not_called()
            self.assertTrue(path.exists())

    def test_move_is_recoverable_and_single_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / "model"
            path.mkdir()
            (path / "config.json").write_text("{}")
            manager = self.manager(path)
            plan = manager.preview_delete("fixture")
            with patch.object(core.Path, "home", return_value=home):
                result = manager.trash_model(plan["token"], "Fixture")
            destination = Path(result["trash_path"])
            self.assertFalse(path.exists())
            self.assertEqual((destination / "config.json").read_text(), "{}")
            destination.rename(path)
            with self.assertRaisesRegex(core.ManagerError, "expired"):
                manager.trash_model(plan["token"], "Fixture")
            manager._unlock_heavy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
