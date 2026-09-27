#!/usr/bin/env python3
"""Real HTTP client capture, private output, dashboard boundary and retry tests."""
import contextlib
import http.client
import http.server
import io
import json
from pathlib import Path
import stat
import sqlite3
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
    request=urllib.request.Request(base+path,data=json.dumps({'sql':'SELECT 42','password':'SECRET_PASSWORD'}).encode(),headers={'Authorization':'SECRET_TOKEN','X-Context-Correlation-Id':'previous-id'})
    try:
        with urllib.request.urlopen(request) as response:response.read()
    except urllib.error.HTTPError as error:
        error.read()
        error.close()
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
            self.assertEqual(Target.received[0][1]['X-Context-Correlation-Id'],events[0]['correlation_id'])
            for secret in ['SECRET_TOKEN','SECRET_PASSWORD','SECRET_RESPONSE','SECRET_QUERY','recordsecret']:
                self.assertNotIn(secret,raw)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o600)
            for path,headers in Target.received:
                if 'auth-with-password' in path or '/users/records' in path or '_superusers' in path:
                    self.assertEqual(headers.get('X-Context-Correlation-Id'),'previous-id')
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

    def test_cross_origin_redirect_does_not_forward_correlation_or_capture_reflections(self):
        class Redirect(http.server.BaseHTTPRequestHandler):
            location=''
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location',self.location)
                self.end_headers()
        Target.received=[]
        with tempfile.TemporaryDirectory() as tmp,serving(Target) as destination:
            Redirect.location=f'http://127.0.0.1:{destination.server_port}/api/context/schema?reflected=SECRET_LOCATION'
            with serving(Redirect) as source:
                output=Path(tmp)/'trace';url=f'http://127.0.0.1:{source.server_port}'
                script="import urllib.request\nwith urllib.request.urlopen("+repr(url+'/api/context/schema?secret=SECRET_QUERY')+") as response:response.read()\n"
                self.assertEqual(self.run_script(script,url,output),0)
                raw=output.read_text();events=[json.loads(line) for line in raw.splitlines()]
                self.assertEqual(len(events),1)
                self.assertEqual(events[0]['route'],'/api/context/schema')
                self.assertNotIn('X-Context-Correlation-Id',Target.received[-1][1])
                for secret in ['SECRET_LOCATION','SECRET_QUERY','SECRET_RESPONSE']:
                    self.assertNotIn(secret,raw)

class DashboardTests(unittest.TestCase):
    def test_http_boundaries_and_sql_only(self):
        calls=[]
        def query(cfg,sql):
            self.assertTrue(sql.startswith(('SELECT ','WITH ')));calls.append(sql)
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

    def test_public_origin_is_exact_and_does_not_trust_forwarded_headers(self):
        calls=[]
        handler=dashboard.make_handler({},'session',SimpleNamespace(query=lambda cfg,sql:calls.append(sql) or []),
                                       'https://observe-dev.example.com')
        with serving(handler) as server:
            def request(headers):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                conn.putrequest('GET','/session/data',skip_host=True)
                for name,value in headers:conn.putheader(name,value)
                conn.endheaders();response=conn.getresponse();response.read();conn.close()
                return response.status
            host=('Host','observe-dev.example.com')
            for headers in [
                [host,('Origin','https://evil.example')],
                [host,('Origin','http://observe-dev.example.com')],
                [host,('Origin',f'http://127.0.0.1:{server.server_port}')],
                [host,('Origin','null')], [host,host],
                [host,('Origin','https://observe-dev.example.com'),('Origin','https://evil.example')],
                [('Host','evil.example'),('X-Forwarded-Host','observe-dev.example.com'),
                 ('Cf-Access-Authenticated-User-Email','fake@example.com')],
            ]:self.assertEqual(request(headers),403)
            self.assertEqual(calls,[])
            self.assertEqual(request([host]),200)
            self.assertEqual(request([host,('Origin','https://observe-dev.example.com')]),200)
            self.assertEqual(request([('Host',f'127.0.0.1:{server.server_port}')]),200)
            self.assertEqual(len(calls),6)

    def test_operation_window_uses_owned_ids_and_keeps_older_peers(self):
        with contextlib.closing(sqlite3.connect(':memory:')) as db:
            db.row_factory=sqlite3.Row
            # Snapshot query databases intentionally have no source indexes.
            db.executescript('CREATE TABLE traces(id TEXT,operation TEXT,service TEXT,request_id TEXT,correlation_id TEXT,method TEXT,route TEXT,started_at TEXT,duration_ms REAL,status INTEGER); CREATE TABLE spans(trace TEXT,name TEXT);')
            def insert(index,operation=None,correlation='',role=None,service='misleading-client'):
                record=f'{index:015d}'
                db.execute('INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (record,operation or record,service,str(index),correlation,'GET','/api/context/schema',f'{index:06d}',1,200))
                if role:db.execute('INSERT INTO spans VALUES (?,?)',(record,role))
                return record
            for index in range(1,51):insert(index)
            client=insert(51,'paired','shared','http.client',service='arbitrary')
            server=insert(0,'paired','shared','auth',service='arbitrary')
            rows=[dict(row) for row in db.execute(dashboard.RECENT_SQL)]
            ids={row['id'] for row in rows}
            self.assertEqual(len(rows),51)
            self.assertIn(server,ids);self.assertIn(client,ids)
            self.assertNotIn(f'{1:015d}',ids)
            self.assertEqual(len({row['operation_key'] for row in rows}),50)
            by_id={row['id']:row for row in rows}
            self.assertEqual(by_id[client]['kind'],'client')
            self.assertEqual(by_id[server]['kind'],'server')
            self.assertEqual(by_id[f'{50:015d}']['kind'],'unknown')
            self.assertEqual(by_id[client]['operation_key'],by_id[server]['operation_key'])
            # A different owner's operation remains distinct even with the same
            # producer-supplied correlation ID and service label in an admin view.
            other=insert(52,'another-owner','shared','auth',service='arbitrary')
            rows={row['id']:dict(row) for row in db.execute(dashboard.RECENT_SQL)}
            self.assertNotEqual(rows[other]['operation_key'],rows[client]['operation_key'])
            for index in range(100,602):insert(index,'large','shared')
            self.assertEqual(len(db.execute(dashboard.RECENT_SQL).fetchall()),500)

    def test_recent_limit_is_reported(self):
        for count in [499,500]:
            query=lambda cfg,sql:[{'id':str(i)} for i in range(count)] if sql==dashboard.RECENT_SQL else []
            handler=dashboard.make_handler({},'session',SimpleNamespace(query=query))
            with serving(handler) as server:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                conn.request('GET','/session/data');response=conn.getresponse()
                self.assertEqual(response.status,200)
                data=json.loads(response.read());conn.close()
                self.assertEqual(data['recent_limited'],count==500)
                self.assertEqual(len(data['recent']),count)
                self.assertEqual(data['report'],[])

    def test_public_origin_validation(self):
        for origin in ['http://example.com','https://example.com/','https://user:pass@example.com',
                       'https://example.com:443','https://example.com?x=1','https://example.com#x',
                       'https://EXAMPLE.com','https://example.com\\evil','https://example.com\n']:
            with self.assertRaises(ValueError):dashboard.public_origin(origin)
        self.assertEqual(dashboard.public_origin('https://observe-dev.example.com'),'https://observe-dev.example.com')
        self.assertIsNone(dashboard.public_origin(None))

class ImportTests(unittest.TestCase):
    def event(self):
        return dict(version=1,request_id='a'*32,operation='operation000001',service='test',method='POST',route='/api/context/query',started_at='2026-09-27T12:00:00.123999Z',duration_ms=1,status=200)
    def test_optional_defaults_and_timestamp_retry(self):
        event=self.event();stored=dict(event,started_at='2026-09-27 12:00:00.123Z',sql='',correlation_id='',user_id='',rows=0,truncated=0,spans='[]')
        self.assertEqual(oc.canonical(event),oc.canonical(stored))
        with patch.object(oc,'query',return_value=[stored]),patch.object(oc,'call') as call:
            self.assertFalse(oc.ingest_one({'_owner_id':'owner0000000001'},event));call.assert_not_called()
        with patch.object(oc,'query',return_value=[dict(stored,duration_ms=2)]):
            with self.assertRaises(oc.Fail) as error:oc.ingest_one({'_owner_id':'owner0000000001'},event)
            self.assertEqual(error.exception.code,4)
    def test_race_and_escaping(self):
        event=dict(self.event(),service="quote' OR 1=1 --")
        with patch.object(oc,'query',side_effect=[[],[event]]) as query,patch.object(oc,'call',return_value=(400,{})):
            self.assertFalse(oc.ingest_one({'_owner_id':'owner0000000001'},event))
            self.assertIn("service='quote'' OR 1=1 --'",query.call_args.args[1])
        with self.assertRaises(oc.Fail):oc.canonical(dict(event,started_at='2026-09-27T12:00:00'))

class FollowTests(unittest.TestCase):
    class Stop(Exception):pass
    def event(self,index):
        return dict(version=1,request_id=f'{index:032x}',service='follow',method='GET',route='/api/context/schema',started_at='2026-09-27T12:00:00.123Z',duration_ms=1,status=200)
    def line(self,index):return (json.dumps(self.event(index))+'\n').encode()

    def test_partial_completion_rotation_restart_and_memory_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'traces';rotated=Path(str(path)+'.1')
            first=self.line(1);path.write_bytes(first[:30])
            seen=[];stored={};polls=0;retained=[]
            def ingest(cfg,event):
                key=event['request_id'];seen.append(key)
                inserted=key not in stored;stored[key]=event
                return inserted
            def tick(_):
                nonlocal polls
                # Inspect only the collector's retained cursor count; old inodes must be discarded.
                frame=sys._getframe(1)
                while frame is not None and frame.f_code is not oc.ingest.__code__:frame=frame.f_back
                self.assertIsNotNone(frame)
                self.assertLessEqual(len(frame.f_locals['positions']),2)
                tracked=list(frame.f_locals['handles'].values())
                self.assertLessEqual(len(tracked),2)
                self.assertTrue(all(not handle.closed for handle in tracked))
                retained.extend(tracked)
                polls+=1
                if polls==1:
                    self.assertEqual(seen,[])
                    with path.open('ab') as handle:handle.write(first[30:])
                elif polls==2:
                    self.assertEqual(seen,[self.event(1)['request_id']])
                    path.replace(rotated);path.write_bytes(self.line(2))
                elif polls==3:
                    self.assertEqual(seen,[self.event(n)['request_id'] for n in [1,2]])
                    with path.open('ab') as handle:handle.write(self.line(3))
                elif polls<18:
                    path.replace(rotated);path.write_bytes(self.line(polls))
                else:raise self.Stop()
            with patch.object(oc,'ingest_one',side_effect=ingest),patch.object(oc.time,'sleep',side_effect=tick):
                with self.assertRaises(self.Stop):oc.ingest({},path,follow=True)
            self.assertEqual(seen,[self.event(n)['request_id'] for n in range(1,18)])
            self.assertTrue(all(handle.closed for handle in retained))
            with patch.object(oc,'ingest_one',side_effect=ingest):summary=oc.ingest({},path)
            self.assertEqual(summary,{'inserted':0,'duplicates':2})

    def test_failure_stops_without_skipping_next_record(self):
        for reason,code in [('timeout',1),('rejected',1),('conflicting',4)]:
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'traces';path.write_bytes(b''.join(self.line(n) for n in [1,2,3]))
                calls=[];stored={}
                def ingest(cfg,event):
                    key=event['request_id'];calls.append(key)
                    if key==self.event(2)['request_id']:raise oc.Fail(code,reason)
                    stored[key]=event;return True
                with patch.object(oc,'ingest_one',side_effect=ingest),patch.object(oc.time,'sleep') as sleep:
                    with self.assertRaises(oc.Fail) as error:oc.ingest({},path,follow=True)
                    self.assertEqual(error.exception.code,code);sleep.assert_not_called()
                self.assertEqual(calls,[self.event(n)['request_id'] for n in [1,2]])
                def retry(cfg,event):
                    key=event['request_id'];inserted=key not in stored;stored[key]=event;return inserted
                with patch.object(oc,'ingest_one',side_effect=retry):summary=oc.ingest({},path)
                self.assertEqual(summary,{'inserted':2,'duplicates':1})
                self.assertEqual(len(stored),3)

    def test_malformed_oversized_and_unfinished_records_preserved(self):
        for invalid in [b'not JSON\n',b'x'*262145+b'\n',self.line(2)[:-1]]:
            with self.subTest(size=len(invalid)),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'traces';raw=self.line(1)+invalid;path.write_bytes(raw)
                with patch.object(oc,'ingest_one',return_value=True) as ingest:
                    with self.assertRaises(oc.Fail):oc.ingest({},path)
                    self.assertEqual(ingest.call_count,1)
                self.assertEqual(path.read_bytes(),raw)

if __name__=='__main__':unittest.main()
