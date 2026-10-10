import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import config
from store import Store

class GalleryTrashTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.patch=patch.multiple(config,DATA_ROOT=root,OUTPUT_ROOT=root/'outputs');self.patch.start()
        config.OUTPUT_ROOT.mkdir();self.store=Store()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def image(self,key):
        path=config.OUTPUT_ROOT/(key+'.png');path.write_bytes(b'fixture')
        self.store.image(dict(id=key,state='done',parameters={'prompt':'fixture'}));return path
    def test_moves_output_and_metadata_without_touching_workspace(self):
        path=self.image('abcdef123456')
        self.store.put(dict(id='chat',kind='chat',model='test',value=dict(title='fixture',messages=[])))
        result=self.store.trash_images(['abcdef123456']);batch=Path(result['location'])
        self.assertFalse(path.exists());self.assertEqual((batch/path.name).read_bytes(),b'fixture')
        self.assertIn('fixture',(batch/'metadata.json').read_text());self.assertEqual(self.store.gallery(),[])
        self.assertEqual(self.store.get('chat')['value']['title'],'fixture')
    def test_invalid_targets_do_not_move_valid_image(self):
        path=self.image('abcdef123456')
        for ids in [['abcdef123456','../../outside'],['abcdef123456','123456abcdef'],[{}],[],['abcdef123456']*2]:
            with self.assertRaises(ValueError):self.store.trash_images(ids)
            self.assertTrue(path.exists())
    def test_symlink_is_rejected(self):
        target=config.DATA_ROOT/'external';target.write_bytes(b'fixture')
        (config.OUTPUT_ROOT/'abcdef123456.png').symlink_to(target)
        with self.assertRaises(ValueError):self.store.trash_images(['abcdef123456'])
        self.assertTrue(target.exists())
    def test_move_failure_restores_preceding_file_and_database(self):
        first=self.image('abcdef123456');second=self.image('123456abcdef');original=Path.rename
        def rename(path,dest):
            if path==second:raise OSError('fixture failure')
            return original(path,dest)
        with patch.object(Path,'rename',rename),self.assertRaises(OSError):self.store.trash_images([first.stem,second.stem])
        self.assertTrue(first.exists());self.assertEqual(len(self.store.gallery()),2)

    def test_http_trash_route_and_empty_gallery(self):
        import json
        import threading
        import urllib.request
        from types import SimpleNamespace
        from http.server import ThreadingHTTPServer
        import server
        self.image('abcdef123456')
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        with patch.object(server,'MANAGER',SimpleNamespace(store=self.store),create=True),patch.object(config,'MANAGER_PORT',http.server_port):
            thread.start()
            base=f'http://127.0.0.1:{http.server_port}'
            try:
                req=urllib.request.Request(base+'/api/image/trash',data=json.dumps({'ids':['abcdef123456']}).encode(),headers={'Content-Type':'application/json','Origin':base})
                with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['moved'],1)
                with urllib.request.urlopen(base+'/api/image/gallery') as response:self.assertEqual(json.load(response)['images'],[])
            finally:
                http.shutdown();thread.join();http.server_close()
