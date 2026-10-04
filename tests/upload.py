#!/usr/bin/env python3
"""Opt-in retrieval boundaries, command outcome and durable replay behavior."""
import base64
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from observecontext_client import capture, dashboard, cli as oc, uploader
import runpy


def run_instrumented(args):
    with capture.capture_session(args):
        try:
            runpy.run_path(args.script, run_name='__main__')
        except SystemExit as error:
            return error.code or 0
    return 0



def event(key='a'):
    return dict(version=1,request_id=key*32,correlation_id='correlated',service='source.server',method='GET',
                route='/api/context/schema',started_at='2026-09-27T12:00:00Z',duration_ms=2,status=200,spans=[])


class FakeOC:
    def __init__(self, root):
        self.root=root;self.operations={};self.traces={};self.owner='user00000000001';self.fail_after_write=False
        self.TRACE_FIELDS=oc.TRACE_FIELDS;self.literal=oc.literal;self.records=oc.records;self.canonical=oc.canonical
    def cache_file(self,cfg):return self.root/'session.json'
    def load_session(self,cfg):
        encoded=base64.urlsafe_b64encode(json.dumps({'id':self.owner}).encode()).decode().rstrip('=')
        return {'token':'header.'+encoded+'.signature'}
    def auth_session(self,cfg,data,method):return {'token':data['token']}
    def send(self,cfg,method,path,body,token,timeout):
        assert 0 < timeout <= 2
        if path.endswith('/auth-refresh'):
            return 200,{'token':'OBSERVE_SECRET','record':{'id':self.owner,'collectionName':'users','email':cfg['email']}}
        if path=='/api/context/query':
            sql=body['sql']
            assert "owner='"+self.owner+"'" in sql or "created_by='"+self.owner+"'" in sql
            values=[]
            if 'FROM operations' in sql:
                key=re.search("client_key='([^']+)'",sql)[1]
                if key in self.operations:values=[self.operations[key]]
            else:
                key=re.search("request_id='([^']+)'",sql)[1]
                if key in self.traces:values=[self.traces[key]]
            columns=list(values[0]) if values else []
            return 200,{'columns':columns,'rows':[[r[k] for k in columns] for r in values],'truncated':False}
        if path==oc.records('operations'):
            record=dict(body,id='operation000001',owner=self.owner);self.operations[body['client_key']]=record
        elif path==oc.records('traces'):
            record=dict(body);self.traces[body['request_id']]=record
        else:raise AssertionError(path)
        if self.fail_after_write:
            self.fail_after_write=False
            raise OSError('synthetic uncertain transport')
        return 200,record


class QueueTests(unittest.TestCase):
    def test_uncertain_operation_and_trace_writes_replay_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=FakeOC(Path(tmp));cfg={'url':'https://observe.example.test','email':'one@example.test'}
            queue=uploader.Delivery(fake,cfg)
            queue.enqueue('b'*32,'skill.client',[event(),event('c')])
            pending=list(queue.directory.glob('*.json'));self.assertEqual(len(pending),1)
            self.assertNotIn('OBSERVE_SECRET',pending[0].read_text())
            self.assertEqual(pending[0].stat().st_mode&0o777,0o600)
            fake.fail_after_write=True
            with self.assertRaises(OSError):queue.flush()
            self.assertEqual(len(fake.operations),1)
            fake.fail_after_write=True
            with self.assertRaises(OSError):queue.flush()
            self.assertEqual(len(fake.traces),1)
            self.assertEqual(queue.flush(),1)
            self.assertEqual(len(fake.operations),1);self.assertEqual(len(fake.traces),2)
            self.assertFalse(list(queue.directory.glob('*.json')))
            self.assertEqual(queue.flush(),0)

    def test_changed_identity_and_conflicting_payload_are_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=FakeOC(Path(tmp));cfg={'url':'https://observe.example.test','email':'one@example.test'}
            queue=uploader.Delivery(fake,cfg);queue.enqueue('b'*32,'skill.client',[event()])
            fake.owner='other0000000001'
            with self.assertRaises(ValueError):queue.flush()
            self.assertFalse(fake.operations)
            fake.owner=queue.owner
            self.assertEqual(queue.flush(),1)
            queue.enqueue('b'*32,'skill.client',[dict(event(),duration_ms=3)])
            with self.assertRaises(ValueError):queue.flush()
            self.assertTrue(list(queue.directory.glob('*.json')))

    def test_account_tampering_symlink_and_capacity_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=FakeOC(Path(tmp));cfg={'url':'https://observe.example.test','email':'one@example.test'}
            queue=uploader.Delivery(fake,cfg);queue.enqueue('b'*32,'skill.client',[event()])
            file=next(queue.directory.glob('*.json'));data=json.loads(file.read_text());data['account']['id']='other';file.write_text(json.dumps(data))
            with self.assertRaises(ValueError):queue.flush()
            file.unlink();file.symlink_to(Path(tmp)/'secret')
            with self.assertRaises(OSError):queue.flush()
            file.unlink()
            with patch.object(uploader,'MAX_PENDING_BYTES',1):
                with self.assertRaises(ValueError):queue.enqueue('b'*32,'skill.client',[event()])


@contextlib.contextmanager
def serving(handler):
    with http.server.ThreadingHTTPServer(('127.0.0.1',0),handler) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield server
        finally:server.shutdown();thread.join()


class CaptureUploadTests(unittest.TestCase):
    def test_opt_in_owner_retrieval_no_tokens_and_nonfatal_upload(self):
        seen=[];queued=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                seen.append((self.path,dict(self.headers)))
                retrieving=self.path.startswith('/api/context/traces/')
                # Match the production edge policy: urllib's default agent is blocked.
                status=403 if retrieving and self.headers.get('User-Agent','').startswith('Python-urllib/') else 200
                body=json.dumps(event()).encode() if retrieving and status==200 else b'{}'
                self.send_response(status);self.send_header('X-Context-Request-Id','a'*32)
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        class Queue:
            def __init__(self,*a,**kw):pass
            def enqueue(self,key,source,events):queued.append(events)
            def flush(self,**kw):raise OSError('Observe is unavailable')
        with tempfile.TemporaryDirectory() as tmp,serving(Handler) as server:
            url=f'http://127.0.0.1:{server.server_port}';script=Path(tmp)/'script.py'
            script.write_text("import urllib.request,sys\nrequest=urllib.request.Request("+repr(url+'/api/context/schema')+",headers={'Authorization':'SOURCE_SECRET'})\nwith urllib.request.urlopen(request) as response:response.read()\nsys.exit(7)\n")
            args=SimpleNamespace(url=url,service='skill.client',script=str(script),output=None,capture_sql=True,arguments=[],upload=True)
            with patch.object(oc,'config',return_value={}),patch.object(uploader,'Delivery',Queue),contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(run_instrumented(args),7)
            self.assertIn('remains in the private queue',error.getvalue())
            self.assertEqual(len(queued),1);self.assertEqual(len(queued[0]),2)
            self.assertEqual(seen[0][1]['X-Context-Trace'],'1')
            self.assertEqual(seen[0][1]['X-Context-Capture-Sql'],'1')
            self.assertEqual(seen[1][1]['Authorization'],'SOURCE_SECRET')
            self.assertEqual(seen[1][1]['User-Agent'],oc.USER_AGENT)
            self.assertNotIn('X-Context-Trace',seen[1][1])
            self.assertNotIn('SOURCE_SECRET',json.dumps(queued))

    def test_authenticated_redirect_is_refused_without_forwarding(self):
        leaked=[]
        class Other(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):leaked.append(dict(self.headers));self.send_response(200);self.end_headers()
        with tempfile.TemporaryDirectory() as tmp,serving(Other) as other:
            class Redirect(http.server.BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{other.server_port}/api/context/schema');self.end_headers()
            with serving(Redirect) as server:
                url=f'http://127.0.0.1:{server.server_port}';script=Path(tmp)/'script.py'
                script.write_text("import urllib.request,urllib.error\nr=urllib.request.Request("+repr(url+'/api/context/schema')+",headers={'Authorization':'SOURCE_SECRET'})\ntry:urllib.request.urlopen(r)\nexcept urllib.error.HTTPError as e:e.close()\n")
                args=SimpleNamespace(url=url,service='client',script=str(script),output=None,capture_sql=False,arguments=[],upload=True)
                with patch.object(oc,'config',side_effect=ValueError()),contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(run_instrumented(args),0)
                self.assertEqual(leaked,[])
                # A source client may override redirect_request itself. The
                # nested opener guard must still refuse forwarding its bearer.
                script.write_text("import urllib.request,urllib.error\nclass Redirect(urllib.request.HTTPRedirectHandler):\n def redirect_request(self,req,fp,code,msg,headers,newurl):return urllib.request.Request(newurl,headers=dict(req.headers))\nrequest=urllib.request.Request("+repr(url+'/api/context/schema')+",headers={'Authorization':'SOURCE_SECRET'})\ntry:urllib.request.build_opener(Redirect).open(request)\nexcept urllib.error.URLError:pass\n")
                with patch.object(oc,'config',side_effect=ValueError()),contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(run_instrumented(args),0)
                self.assertEqual(leaked,[])


class MultiOriginTests(unittest.TestCase):
    def test_two_origins_share_operation_with_separate_labels_and_credentials(self):
        seen=[];queued=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                seen.append((self.server.server_port,self.path,dict(self.headers)))
                payload=event('a' if self.server.label=='catalog' else 'b')
                payload['service']=self.server.label+'.server'
                body=json.dumps(payload).encode() if self.path.startswith('/api/context/traces/') else b'{}'
                self.send_response(200);self.send_header('X-Context-Request-Id',payload['request_id'])
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        class Queue:
            def __init__(self,*a,**kw):pass
            def enqueue(self,key,source,events):queued.append((key,source,events))
            def flush(self,**kw):return len(queued)
        with tempfile.TemporaryDirectory() as tmp,serving(Handler) as catalog,serving(Handler) as source:
            catalog.label='catalog';source.label='source'
            catalog_url=f'http://127.0.0.1:{catalog.server_port}';source_url=f'http://127.0.0.1:{source.server_port}'
            script=Path(tmp)/'meta.py'
            script.write_text("import urllib.request,urllib.error\nfor url,token in "+repr([(catalog_url,'CATALOG_SECRET'),(source_url,'SOURCE_SECRET')])+":\n request=urllib.request.Request(url+'/api/context/schema',headers={'Authorization':token})\n with urllib.request.urlopen(request) as response:response.read()\ntry:urllib.request.urlopen(urllib.request.Request("+repr(source_url+'/api/context/schema')+",headers={'Authorization':'CATALOG_SECRET'}))\nexcept urllib.error.URLError:pass\n")
            args=SimpleNamespace(url=None,origin=['meta.client='+catalog_url,'source.client='+source_url],service='meta.ingest',script=str(script),output=None,capture_sql=False,arguments=[],upload=True)
            with patch.dict(os.environ,{'OBSERVECONTEXT_URL':'http://127.0.0.1:9'}),patch.object(oc,'config',return_value={}),patch.object(uploader,'Delivery',Queue),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_instrumented(args),0)
            self.assertEqual(len(queued),2)
            self.assertEqual(queued[0][0],queued[1][0]);self.assertEqual({item[1] for item in queued},{'meta.ingest'})
            self.assertEqual([item[2][0]['service'] for item in queued],['meta.client','source.client'])
            self.assertEqual([item[2][1]['service'] for item in queued],['catalog.server','source.server'])
            self.assertEqual(len(seen),4)
            for port,path,headers in seen:
                self.assertEqual(headers['Authorization'],'CATALOG_SECRET' if port==catalog.server_port else 'SOURCE_SECRET')
                if '/traces/' in path:self.assertNotIn('X-Context-Trace',headers)
            self.assertNotIn('SECRET',json.dumps(queued))

    def test_explicit_mapping_validation_and_observe_exclusion(self):
        with patch.dict(os.environ,{'OBSERVECONTEXT_URL':'https://observe.example.test'}):
            invalid=[['client=https://observe.example.test'],['one=https://app.test','two=https://APP.test:443/'],
                     ['one=https://app.test','one=https://other.test'],['one=https://user:secret@app.test'],
                     ['one=https://app.test/path'],['one=https://app.test?query=secret'],['malformed']]
            for origins in invalid:
                with self.subTest(origins=origins),self.assertRaises(ValueError):
                    capture.capture_origins(SimpleNamespace(service='meta.ingest',origin=origins,url=None,upload=True))
            result=capture.capture_origins(SimpleNamespace(service='app.client',url='https://APP.test/',upload=True))
            self.assertEqual(result,{('https','app.test',443):('app.client','https://APP.test')})

if __name__=='__main__':unittest.main()
