"""Portability and responsiveness gates without production services or weights."""
import threading
import unittest
from unittest.mock import Mock, patch
import core
import config
import install_service
from telemetry import Telemetry


class PortabilityTests(unittest.TestCase):
    def test_metrics_cannot_block_status_or_catalog(self):
        entered, release = threading.Event(), threading.Event()
        manager = core.Manager.__new__(core.Manager)
        manager._mu = threading.RLock()
        manager._telemetry = Telemetry()
        manager._reconcile = Mock()
        manager._worker = manager._job = None
        manager._mode, manager._heavy_held, manager._active_chat = 'idle', False, 0
        manager._enhancer = Mock(snapshot=lambda: {})
        manager._runtime = Mock(status=lambda: {})
        manager._external_reservation = lambda: False
        manager._catalog = {'models': []}
        def collect():
            entered.set()
            release.wait(3)
            return {'system': {}, 'image': {}, 'external_chat': {}}
        manager._collect_metrics = collect
        try:
            first=manager.status()
            self.assertTrue(entered.wait(1))
            self.assertTrue(first['metrics']['refreshing'])
            self.assertEqual(manager.status()['mode'], 'idle')
            self.assertEqual(manager.model_catalog(), {'models': []})
            manager.chat_leave()  # cancellation completion can also acquire the lock
        finally:
            release.set()

    def test_catalog_discovery_does_not_hold_control_lock(self):
        entered, release=threading.Event(),threading.Event()
        manager=core.Manager.__new__(core.Manager)
        manager._mu=threading.RLock();manager._catalog=None;manager._active_chat=1
        def discover():
            entered.set();release.wait(3);return {'models':[]}
        with patch.object(core.catalog,'discover',side_effect=discover):
            worker=threading.Thread(target=manager.model_catalog)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                manager.chat_leave()
                self.assertEqual(manager._active_chat,0)
            finally:
                release.set();worker.join(2)

    def test_disabled_adapter_does_not_probe_or_stop_services(self):
        manager=core.Manager.__new__(core.Manager)
        with patch.object(config,'EXTERNAL_ENABLED',False),patch.object(core.memory,'listener_pid') as listener,patch.object(core,'run') as command:
            self.assertIsNone(manager._external_listener())
            manager._stop_external()
            with self.assertRaisesRegex(core.ManagerError,'disabled'):manager._start_external()
            listener.assert_not_called();command.assert_not_called()

    def test_installer_preserves_paths_and_port_but_not_credentials(self):
        env={'PATH':'/custom/bin','HF_HUB_CACHE':'/data/cache','MLX_MANAGER_PORT':'2000','MLX_MANAGER_MODEL_ROOTS':'["/data/models"]','MLX_MANAGER_EXTERNAL_ENABLED':'true','AWS_SECRET_ACCESS_KEY':'secret','MLX_MANAGER_API_KEY':'secret'}
        result=install_service.service_environment(env)
        self.assertEqual(result,{k:v for k,v in env.items() if 'SECRET' not in k and not k.endswith('API_KEY')})

if __name__=='__main__':unittest.main()
