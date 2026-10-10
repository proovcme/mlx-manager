import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
import config
import core
import rag

DATASETS={'datasets':[{'id':'set','display_name':'Fixture','document_count':1,'chunk_count':3},{'id':'system','dataset_scope':'system','chunk_count':2}]}
SEARCH={'chunks':[{'doc_id':'doc','doc_name':'Test.pdf','content':'Fact','metadata':{'dataset_id':'set','page':2},'context':{'content':'Context'}}],'retrieval_trace':{'status':'ok'}}
class Fixture(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def reply(self,value,status=200,sse=False):
        raw=json.dumps(value).encode()
        if sse:raw=b': keepalive\n\ndata: '+raw+b'\n\n'
        self.send_response(status);self.send_header('Content-Type','text/event-stream' if sse else 'application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Mcp-Session-Id','fixture');self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        self.server.calls.append(('GET',self.path,None))
        if self.path.startswith('/api/documents/datasets'):self.reply(DATASETS)
        elif self.path=='/denied':self.reply({},401)
        elif self.path=='/redirect':
            self.send_response(302);self.send_header('Location','/api/documents/datasets');self.send_header('Content-Length','0');self.end_headers()
        else:self.reply({},404)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.server.calls.append(('POST',self.path,body))
        if self.path=='/api/search':self.reply(SEARCH);return
        method=body['method']
        if method=='initialize':result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}}}
        else:
            assert self.headers.get('Mcp-Session-Id')=='fixture'
            if method=='notifications/initialized':self.reply({},202);return
            if method=='tools/list':result={'tools':[{'name':'list_datasets'},{'name':'search_sources'}]}
            else:
                assert method=='tools/call'
                value={'datasets':[{'id':'set','name':'Fixture','documents':1,'declared_chunks':3}]} if body['params']['name']=='list_datasets' else {'hits':[{'dataset_id':'set','document':'Test.pdf','excerpt':'Fact','context':'Context'}],'retrieval':{'status':'ok'}}
                result={'content':[{'type':'text','text':json.dumps(value)}]}
        self.reply({'jsonrpc':'2.0','id':body['id'],'result':result},sse=True)

class RagTests(unittest.TestCase):
    def setUp(self):
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Fixture);self.http.daemon_threads=True;self.http.calls=[]
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.http.server_port)
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join()
    def client(self,transport='api'):return rag.Client({'url':self.base+('/mcp' if transport=='mcp' else ''),'transport':transport})
    def call(self,name,args,id='call'):return {'role':'assistant','content':'','tool_calls':[{'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
    def consume(self,flow):
        events=[]
        while True:
            try:events.append(next(flow))
            except StopIteration as done:return events,done.value
    def manager(self):
        m=core.Manager.__new__(core.Manager);m._mu=threading.RLock();m._mode='model';m._active_chat=0;m._reconcile=Mock();m._runtime=Mock();m.rag=Mock();m.rag.client.return_value=self.client();return m
    def test_api_provenance_and_scope(self):
        client=self.client();self.assertEqual([r['id'] for r in client.datasets()],['set'])
        source=client.search('chosen query',['set'])['sources'][0];self.assertEqual(source['page'],2);self.assertEqual(source['context'],'Context');self.assertIn('/by-id/doc/viewer?',source['url'])
        sent=self.http.calls[-1][2];self.assertEqual(sent['dataset_ids'],['set']);self.assertEqual(sent['query'],'chosen query');self.assertTrue(sent['include_trace'])
    def test_mcp_session_and_sse(self):
        client=self.client('mcp');self.assertEqual(client.datasets()[0]['chunks'],3);self.assertEqual(client.search('q',['set'])['sources'][0]['snippet'],'Fact')
        methods=[b['method'] for _,_,b in self.http.calls];self.assertEqual(methods[:4],['initialize','notifications/initialized','tools/list','tools/call']);self.assertEqual(methods.count('initialize'),1)
    def test_model_selects_search_and_reads_context(self):
        model=Mock(side_effect=[self.call('search_sources',{'query':'Fact'}),self.call('read_source',{'source_id':1},'read'),{'content':'done'}])
        original=[{'role':'system','content':'Rules'},{'role':'user','content':'Question'}]
        events,messages=self.consume(rag.retrieve(original,['set'],self.client(),model));self.assertEqual(len(original),2)
        self.assertEqual([e['phase'] for e in events],['planning','searching','ready','planning','reading','planning'])
        results=[json.loads(m['content']) for m in messages if m['role']=='tool'];self.assertEqual(results[1]['context'],'Context');self.assertIn('[D1]',messages[1]['content']);self.assertIn('недоверенные',messages[1]['content'])
    def test_unsupported_tools_no_silent_fallback(self):
        with self.assertRaisesRegex(rag.RagError,'не вызвала'):self.consume(rag.retrieve([{'role':'user','content':'q'}],['set'],self.client(),Mock(return_value={'content':'invented'})))
    def test_unknown_dataset_blocks_model(self):
        model=Mock()
        with self.assertRaisesRegex(rag.RagError,'не доступен'):self.consume(rag.retrieve([{'role':'user','content':'q'}],['system'],self.client(),model))
        model.assert_not_called()
    def test_wrong_scope_and_blocked_index(self):
        values=[dict(SEARCH,chunks=[dict(SEARCH['chunks'][0],metadata={'dataset_id':'other'})]),{'retrieval_trace':{'status':'blocked'}},{'retrieval_trace':{}}]
        for value in values:
            with patch.object(rag.Client,'request',return_value=value),self.assertRaises(rag.RagError):self.client().search('q',['set'])
    def test_no_chunks(self):
        with patch.object(rag.Client,'datasets',return_value=[{'id':'set','chunks':0}]),self.assertRaisesRegex(rag.RagError,'ещё нет'):self.consume(rag.retrieve([{'role':'user','content':'q'}],['set'],self.client(),Mock()))
    def test_forbidden_tools_and_parameters(self):
        for name,args in [('delete_document',{}),('read_source',{'source_id':9}),('search_sources',{'query':'q','dataset_ids':['other']})]:
            with self.assertRaises(rag.RagError):self.consume(rag.retrieve([{'role':'user','content':'q'}],['set'],self.client(),Mock(return_value=self.call(name,args))))
    def test_cancel_releases_request(self):
        m=self.manager();m.rag.client.side_effect=lambda cancelled,track:rag.Client({'url':self.base,'transport':'api'},cancelled,track)
        flow=m.stream_chat({'messages':[{'role':'user','content':'q'}],'rag':['set'],'request_id':'cancel'});next(flow);m.cancel_chat('cancel')
        with self.assertRaisesRegex(rag.RagError,'отменён'):list(flow)
        self.assertEqual(m._active_chat,0);self.assertEqual(m._chat_requests,{})
    def test_chat_stream_tool_results_and_sources(self):
        m=self.manager();m._runtime.open_chat.side_effect=[io.BytesIO(json.dumps({'choices':[{'message':self.call('search_sources',{'query':'q'})}]}).encode()),io.BytesIO(b'{"choices":[{"message":{"content":"done"}}]}'),io.BytesIO(b'data: {"choices":[{"delta":{"content":"Answer [D1]"}}]}\n\ndata: [DONE]\n\n')]
        reply=m.chat({'messages':[{'role':'user','content':'Question'}],'rag':['set']});self.assertEqual(reply['choices'][0]['message']['content'],'Answer [D1]');self.assertEqual(reply['rag']['sources'][0]['title'],'Test.pdf');self.assertEqual(m._active_chat,0)
        self.assertEqual(m._runtime.open_chat.call_args.args[0][-1]['role'],'tool')
    def test_secret_storage_and_address_validation(self):
        with tempfile.TemporaryDirectory() as root,patch.object(config,'DATA_ROOT',Path(root)):
            c=rag.Connection();c.save({'url':self.base,'transport':'api','api_key':'test-secret'});self.assertNotIn('api_key',c.public());self.assertEqual(c.path.stat().st_mode&0o777,0o600)
            c.save({'url':self.base,'transport':'api'});self.assertEqual(c.client().settings['api_key'],'test-secret')
            c.save({'url':self.base+'/new','transport':'api'});self.assertFalse(c.public()['has_key'])
            with self.assertRaises(rag.RagError):c.save({'url':'http://example.com','transport':'api','api_key':'secret'})
        for url in ('file:///tmp/a','http://u:secret@example.com','http://example.com?token=x','http://localhost:bad'):
            with self.assertRaises(rag.RagError):rag.endpoint(url)
    def test_auth_and_redirects(self):
        with self.assertRaisesRegex(rag.RagError,'ключ'):self.client().request('GET','/denied')
        with self.assertRaisesRegex(rag.RagError,'302'):self.client().request('GET','/redirect')
        self.assertFalse(any('datasets' in p for _,p,_ in self.http.calls))
    def test_invalid_selection(self):
        for value in ([],True,'set',[1],['x']*9):
            with self.assertRaises(rag.RagError):core.Manager.validate_chat({'messages':[{'role':'user','content':'q'}],'rag':value})
if __name__=='__main__':unittest.main()
