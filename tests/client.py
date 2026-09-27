#!/usr/bin/env python3
"""Real HTTP client capture, private output, dashboard boundary and retry tests."""
import contextlib
import http.client
import http.server
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/observecontext/scripts'
sys.path.insert(0,str(SCRIPTS))
import capture
import dashboard
import oc

@contextlib.contextmanager
def serving(handler):
    with http.server.ThreadingHTTPServer(('127.0.0.1',0),handler) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:yield server
        finally:server.shutdown();worker.join()

class Target(http.server.BaseHTTPRequestHandler):
    received=[]
    def log_message(self,*args):pass
    def do_POST(self):self.do_GET()
    def do_GET(self):
        self.received.append((self.path,dict(self.headers)))
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        code=422 if self.path.startswith('/api/collections/errors/') else 200
        body=b'{"result":"SECRET_RESPONSE"}'
        self.send_response(code);self.send_header('Content-Length',str(len(body)));self.end_headers()
        time.sleep(.08)
        self.wfile.write(body)

class CaptureTests(unittest.TestCase):
    def run_script(self,source,url,output,sql=False):
        script=output.parent/'client.py';script.write_text(source)
        return capture.run(SimpleNamespace(url=url,service='test-client',script=str(script),output=str(output),capture_sql=sql,arguments=[]))

    def test_capture_real_http_and_private_output(self):
        Target.received=[]
        with tempfile.TemporaryDirectory() as tmp, serving(Target) as target, serving(Target) as other:
            output=Path(tmp)/'trace.jsonl';output.write_text('');output.chmod(0o644)
            url=f'http://127.0.0.1:{target.server_port}';cross=f'http://127.0.0.1:{other.server_port}'
            source='''import urllib.request,urllib.error,json
base=BASE
for path in ['/api/context/query?secret=SECRET_QUERY','/api/collections/items/records/recordsecret','/api/collections/users/auth-with-password','/api/collections/users/records','/api/collections/_superusers/records','/api/collections/errors/records']:
    request=urllib.request.Request(base+path,data=json.dumps({'sql':'SELECT 42','password':'SECRET_PASSWORD'}).encode(),headers={'Authorization':'SECRET_TOKEN'})
    try:
        with urllib.request.urlopen(request) as response:response.read()
    except urllib.error.HTTPError as error:error.read()
with urllib.request.urlopen(CROSS+'/api/context/schema') as response:response.read()
'''.replace('BASE',repr(url)).replace('CROSS',repr(cross))
            self.assertEqual(self.run_script(source,url,output,True),0)
            raw=output.read_text();events=[json.loads(line) for line in raw.splitlines()]
            self.assertEqual(len(events),3)
            self.assertEqual([e['status'] for e in events],[200,200,422])
            self.assertEqual(events[0]['sql'],'SELECT 42')
            self.assertEqual(events[1]['route'],'/api/collections/items/records/{id}')
            self.assertTrue(all(e['duration_ms']>=60 for e in events))
            self.assertTrue(all(len(e['correlation_id'])==32 for e in events))
            for secret in ['SECRET_TOKEN','SECRET_PASSWORD','SECRET_RESPONSE','SECRET_QUERY','recordsecret']:
                self.assertNotIn(secret,raw)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o600)
            for path,headers in Target.received:
                if 'auth-with-password' in path or '/users/records' in path or '_superusers' in path:
                    self.assertNotIn('X-Context-Correlation-Id',headers)
            self.assertNotIn('X-Context-Correlation-Id',Target.received[-1][1])

    def test_partial_error_and_success_reads_include_wait(self):
        with tempfile.TemporaryDirectory() as tmp,serving(Target) as target:
            output=Path(tmp)/'traces';url=f'http://127.0.0.1:{target.server_port}'
            source='''import urllib.request,urllib.error,time
for path in ['/api/context/schema','/api/collections/errors/records']:
    try:response=urllib.request.urlopen(BASE+path)
    except urllib.error.HTTPError as error:response=error
    response.read(1)
    time.sleep(.1)
    response.read()
    response.close()
'''.replace('BASE',repr(url))
            self.assertEqual(self.run_script(source,url,output),0)
            events=[json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(events),2)
            self.assertTrue(all(e['duration_ms']>=160 for e in events))
            self.assertTrue(all(e['sql']=='' for e in events))

    def test_transport_failure_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'trace';url='http://127.0.0.1:1'
            source='''import urllib.request,urllib.error
try:urllib.request.urlopen(BASE+'/api/context/schema',timeout=.2)
except urllib.error.URLError:pass
'''.replace('BASE',repr(url))
            self.assertEqual(self.run_script(source,url,output),0)
            self.assertEqual(json.loads(output.read_text())['status'],0)
            original=output.read_bytes();link=Path(tmp)/'link';link.symlink_to(output)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.run_script(source,url,link),1)
            self.assertEqual(output.read_bytes(),original)

class DashboardTests(unittest.TestCase):
    def test_http_boundaries_and_sql_only(self):
        calls=[]
        def query(cfg,sql):
            self.assertTrue(sql.startswith('SELECT '));calls.append(sql)
            self.assertNotIn('position',sql)
            return [{'id':'a'*15,'sql':'<script>untrusted</script>'}] if 'SELECT * FROM traces' in sql else []
        handler=dashboard.make_handler({'token':'SECRET'},'session',SimpleNamespace(query=query))
        with serving(handler) as server:
            def get(path,headers=None):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
                connection.request('GET',path,headers=headers or {})
                response=connection.getresponse();status=response.status;body=response.read();heads=dict(response.getheaders());connection.close()
                return status,body,heads
            for headers in [{'Host':'evil.example'},{'Origin':'https://evil.example'}]:
                self.assertEqual(get('/session/data',headers)[0],403)
            self.assertEqual(get('/wrong/data')[0],404)
            self.assertEqual(get('/session/trace?id=bad%27')[0],400)
            self.assertEqual(calls,[])
            status,body,headers=get('/session/');self.assertEqual(status,200)
            self.assertNotIn(b'SECRET',body)
            self.assertIn(b'textContent',body);self.assertNotIn(b'innerHTML',body)
            self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
            self.assertEqual(get('/session/data')[0],200)
            self.assertEqual(get('/session/trace?id='+'a'*15)[0],200)
            self.assertEqual(len(calls),4)
            self.assertIn('ordinal',calls[-1])

class ImportTests(unittest.TestCase):
    def event(self):
        return dict(version=1,request_id='a'*32,service='test',method='POST',route='/api/context/query',started_at='2026-09-27T12:00:00.123999Z',duration_ms=1,status=200)
    def test_optional_defaults_and_timestamp_retry(self):
        event=self.event();stored=dict(event,started_at='2026-09-27 12:00:00.123Z',sql='',correlation_id='',user_id='',rows=0,truncated=0,spans='[]')
        self.assertEqual(oc.canonical(event),oc.canonical(stored))
        with patch.object(oc,'query',return_value=[stored]),patch.object(oc,'call') as call:
            self.assertFalse(oc.ingest_one({},event));call.assert_not_called()
        with patch.object(oc,'query',return_value=[dict(stored,duration_ms=2)]):
            with self.assertRaises(oc.Fail) as error:oc.ingest_one({},event)
            self.assertEqual(error.exception.code,4)
    def test_race_and_escaping(self):
        event=dict(self.event(),service="quote' OR 1=1 --")
        with patch.object(oc,'query',side_effect=[[],[event]]) as query,patch.object(oc,'call',return_value=(400,{})):
            self.assertFalse(oc.ingest_one({},event))
            self.assertIn("service='quote'' OR 1=1 --'",query.call_args.args[1])
        with self.assertRaises(oc.Fail):oc.canonical(dict(event,started_at='2026-09-27T12:00:00'))

if __name__=='__main__':unittest.main()
