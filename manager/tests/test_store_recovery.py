import json
import tempfile
import subprocess
import sys
import core
import unittest
from pathlib import Path
from unittest.mock import patch
import config
import ownership
from runtime import api_id
from store import Store


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.patch=patch.multiple(config,DATA_ROOT=root,OUTPUT_ROOT=root/'outputs')
        self.patch.start();config.OUTPUT_ROOT.mkdir();self.store=Store()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def test_chat_survives_reopening_and_stale_tab_cannot_overwrite(self):
        spec=dict(id='chat-1',kind='chat',model='local',revision=0,value=dict(title='Question',messages=[dict(role='user',content='hello')]))
        self.assertEqual(self.store.put(spec)['revision'],1)
        reopened=Store()
        self.assertEqual(reopened.get('chat-1')['value'],spec['value'])
        with self.assertRaisesRegex(ValueError,'another tab'): reopened.put(spec)
        self.assertEqual(reopened.get('chat-1')['value'],spec['value'])
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)
    def test_running_image_is_adopted_after_manager_restart_and_can_be_cancelled(self):
        proc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        job=dict(id='abcdef123456',state='running',owner=ownership.record(proc),steps=1,started_at=core.now(),output=str(config.OUTPUT_ROOT/'abcdef123456.png'),log=str(config.DATA_ROOT/'image.log'),cancel_reason=None,peak_footprint_bytes=None,min_pressure_free_percent=80)
        ownership.write(config.DATA_ROOT/'last_job.json',job)
        manager=None
        try:
            with patch('core.threading.Thread'), patch('core.memory.listener_pid',return_value=None), patch('core.memory.image_processes',return_value=[]), patch('core.memory.system_memory',return_value=dict(swap_used_bytes=0,pressure_free_percent=80)):
                manager=core.Manager()
                self.assertEqual(manager._worker.pid,proc.pid)
                self.assertEqual(manager._mode,'image')
                manager.cancel();proc.wait(timeout=5)
                manager._watch_job(manager._worker)
                self.assertEqual(manager._job['state'],'cancelled')
                self.assertEqual(manager.store.gallery(),[])
        finally:
            if proc.poll() is None:
                proc.terminate();proc.wait(timeout=5)
            if manager:
                manager._heavy.close();manager._singleton.close()

    def test_output_path_traversal_and_symlink_escape_refused(self):
        outside=config.DATA_ROOT/'outside.png';outside.write_bytes(b'fixture')
        (config.OUTPUT_ROOT/'abcdef123456.png').symlink_to(outside)
        for key in ('../outside','abcdef123456'):
            with self.assertRaises(ValueError):self.store.output(key)
    def test_existing_image_is_visible_without_invented_parameters(self):
        (config.OUTPUT_ROOT/'abcdef123456.png').write_bytes(b'fixture')
        item=self.store.gallery()[0]
        self.assertTrue(item['legacy']);self.assertNotIn('parameters',item)
    def test_image_parameters_and_all_history_survive_restart(self):
        for key in ('abcdef123456','abcdef654321'):
            (config.OUTPUT_ROOT/(key+'.png')).write_bytes(b'fixture')
            self.store.image(dict(id=key,state='done',parameters=dict(prompt='neutral',seed=42)))
        self.assertEqual(len(Store().gallery()),2)
        self.assertEqual(Store().gallery()[0]['parameters']['seed'],42)


class OwnershipTests(unittest.TestCase):
    def test_reused_pid_does_not_get_adopted(self):
        with patch('ownership.identity',return_value='different birth time'):
            self.assertIsNone(ownership.recover(dict(pid=123,identity='old birth time')))
    def test_matching_process_recovers_then_reads_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            result=Path(temp)/'progress.json';result.write_text(json.dumps(dict(stage='done')))
            with patch('ownership.identity',return_value='same'):
                proc=ownership.recover(dict(pid=123,identity='same'),result)
                self.assertIsNone(proc.poll())
            with patch('ownership.identity',return_value=None):
                self.assertEqual(proc.wait(timeout=1),0)
    def test_mlx_requests_selected_path_even_when_other_model_listed_first(self):
        self.assertEqual(api_id([dict(id="wrong"),dict(id="selected")],"mlx",dict(path="/tmp/selected-model")),str(Path("/tmp/selected-model").resolve()))

    def test_no_proof_never_adopted(self):
        self.assertIsNone(ownership.recover(dict(pid=123)))

if __name__=='__main__':unittest.main()
