"""Lifecycle regressions use small processes and local HTTP; no models needed."""
import io
import json
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import config
import core
import ownership
import server
from runtime import ModelRuntime
from transport import open_stream


class LifecycleTests(unittest.TestCase):
    def manager(self):
        manager = core.Manager.__new__(core.Manager)
        manager._mu = threading.RLock()
        manager._active_chat = 0
        manager._mode = 'model'
        manager._reconcile = Mock()
        manager._runtime = Mock()
        return manager

    def test_stubborn_owned_process_is_reaped_and_pid_proof_still_required(self):
        source = 'import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(60)'
        process = subprocess.Popen([sys.executable, '-c', source], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertEqual(ownership.stop(process, first=signal.SIGINT, grace=.1, terminate_grace=.1), -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            process.stdout.close()
        recovered = ownership.RecoveredProcess({'pid':123, 'identity':'old'})
        with patch('ownership.identity', return_value='new'), patch('ownership.os.killpg') as kill:
            ownership.signal_group(recovered, signal.SIGKILL)
            kill.assert_not_called()

    def test_missing_executable_is_failed_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_ROOT', Path(folder)), patch('memory.listener_pid', return_value=None):
            runtime = ModelRuntime()
            model = {'id':'fixture', 'path':folder}
            with self.assertRaisesRegex(RuntimeError, 'Could not launch'):
                runtime.start(model, 'mlx', str(Path(folder)/'missing'))
            self.assertEqual(runtime.status()['state'], 'failed')
            self.assertFalse(runtime.running())
            with patch('runtime.subprocess.Popen') as spawn, patch.object(runtime, '_save'), patch.object(runtime, '_wait_ready'):
                spawn.return_value.poll.return_value = None
                runtime.start(model, 'mlx', 'fixture')
                self.assertEqual(runtime.state, 'starting')
                self.assertIsNone(runtime.error)

    def test_launch_error_releases_heavy_reservation(self):
        manager = self.manager()
        manager._active_chat = 0
        manager._worker = None
        manager._runtime.running.return_value = False
        manager._runtime.start.side_effect = RuntimeError('failed executable')
        manager._lock_heavy = Mock()
        manager._unlock_heavy = Mock()
        manager._stop_external = Mock()
        manager._heavy_held = False
        def reconcile():
            manager._mode = 'idle'
            manager._unlock_heavy()
        manager._reconcile.side_effect = reconcile
        manager.model_catalog = Mock(return_value={'models':[{'id':'fixture','available':True,'kind':'chat','backends':['mlx']}], 'runtimes':[{'id':'mlx','executable':'fixture'}]})
        with patch('memory.established_connections', return_value=0), self.assertRaisesRegex(core.ManagerError, 'failed executable'):
            manager.start_model('fixture')
        self.assertEqual(manager._mode, 'idle')
        self.assertEqual(manager._unlock_heavy.call_count, 2)

    def test_cancellation_before_stream_registration_never_calls_model(self):
        manager = self.manager()
        manager.cancel_chat('fixture')
        stream = manager.stream_chat({'request_id':'fixture', 'messages':[{'role':'user','content':'test'}]})
        with self.assertRaisesRegex(core.ManagerError, 'cancelled'):
            next(stream)
        manager._runtime.open_chat.assert_not_called()
        self.assertEqual(manager._active_chat, 0)

    def test_successful_output_after_cancel_stays_cancelled(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'output.png'; output.write_bytes(b'fixture')
            manager = self.manager()
            worker = Mock(); worker.poll.return_value = 0; worker.wait.return_value = 0
            manager._worker = worker
            manager._job = {'id':'fixture','output':str(output),'cancel_reason':'User requested'}
            manager._save_job = Mock()
            with patch('memory.system_memory', return_value={'swap_used_bytes':0}):
                manager._watch_job(worker, 'fixture')
            self.assertEqual(manager._job['state'], 'cancelled')
            self.assertIsNone(manager._worker)

    def stalled_upstream(self, headers=True):
        release = threading.Event()
        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                if headers:
                    self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
                    self.wfile.flush()
                release.wait(5)
            def log_message(self, *args): pass
        backend = ThreadingHTTPServer(('127.0.0.1',0), Upstream)
        backend.daemon_threads = True
        thread = threading.Thread(target=backend.serve_forever, daemon=True); thread.start()
        manager = self.manager()
        def open_chat(*args, **kwargs):
            return open_stream(urllib.request.Request(f'http://127.0.0.1:{backend.server_port}/', data=b'{}'), kwargs['on_socket'], timeout=5)
        manager._runtime.open_chat.side_effect = open_chat
        return manager, backend, release

    def wait_registered(self, manager, key):
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            with manager._mu:
                if getattr(manager, '_chat_requests', {}).get(key, {}).get('socket') is not None:
                    return
            time.sleep(.01)
        self.fail('stream did not register')

    def test_cancel_interrupts_prefill_before_response_headers(self):
        manager, backend, release = self.stalled_upstream(headers=False)
        errors = []
        def read():
            try: list(manager.stream_chat({'request_id':'prefill','messages':[{'role':'user','content':'test'}]}))
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=read, daemon=True); thread.start()
        try:
            self.wait_registered(manager, 'prefill')
            manager.cancel_chat('prefill')
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(manager._active_chat, 0)
            self.assertTrue(errors)
        finally:
            release.set(); backend.shutdown(); backend.server_close(); thread.join(2)

    def test_stalled_stream_cancel_releases_request_and_allows_next_chat(self):
        manager, backend, release = self.stalled_upstream()
        errors = []
        def read():
            try: list(manager.stream_chat({'request_id':'stall','messages':[{'role':'user','content':'test'}]}))
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=read, daemon=True); thread.start()
        try:
            self.wait_registered(manager, 'stall')
            self.assertEqual(manager._active_chat, 1)
            self.assertTrue(manager.cancel_chat('stall')['cancelled'])
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(manager._active_chat, 0)
            manager._runtime.open_chat.side_effect = None
            manager._runtime.open_chat.return_value = io.BytesIO(b'data: [DONE]\n\n')
            self.assertTrue(list(manager.stream_chat({'messages':[{'role':'user','content':'next'}]})))
            self.assertEqual(manager._active_chat, 0)
        finally:
            release.set(); backend.shutdown(); backend.server_close(); thread.join(2)

    def test_browser_disconnect_closes_stalled_upstream(self):
        manager, backend, release = self.stalled_upstream()
        control = ThreadingHTTPServer(('127.0.0.1',0), server.Handler)
        control.daemon_threads = True
        thread = threading.Thread(target=control.serve_forever, daemon=True)
        client = None
        try:
            with patch.object(server, 'MANAGER', manager, create=True), patch.object(config, 'MANAGER_PORT', control.server_port):
                thread.start()
                client = socket.create_connection(('127.0.0.1',control.server_port), timeout=3)
                payload = json.dumps({'stream':True,'request_id':'disconnect','messages':[{'role':'user','content':'test'}]}).encode()
                header = f'POST /api/chat HTTP/1.0\r\nHost: 127.0.0.1:{control.server_port}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n'.encode()
                client.sendall(header+payload)
                self.wait_registered(manager, 'disconnect')
                client.close(); client = None
                deadline = time.monotonic()+3
                while manager._active_chat and time.monotonic()<deadline: time.sleep(.02)
                self.assertEqual(manager._active_chat, 0)
        finally:
            if client: client.close()
            release.set(); control.shutdown(); control.server_close(); backend.shutdown(); backend.server_close()


if __name__ == '__main__': unittest.main()
