#!/usr/bin/env python3
"""Exercise ObserveContext through HTTP against an isolated temporary database."""
import argparse
import concurrent.futures
import contextlib
import json
from pathlib import Path
import socket
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

@contextlib.contextmanager
def server(binary, root=ROOT, data_dir=None):
    with tempfile.TemporaryDirectory(prefix='observecontext-test-') as tmp:
        hooks=Path(tmp)/'pb_hooks'
        shutil.copytree(root/'pb_hooks',hooks)
        (hooks/'failure_fixture.pb.js').write_text('''
onRecordCreateExecute((e) => {
  if(e.record.getString('name') === 'synthetic_failure') throw new Error('Synthetic audit failure');
  e.next();
}, 'spans');
onRecordCreateExecute((e) => {
  if(e.record.id === 'dirfailure00001') throw new Error('Synthetic directory failure');
  e.next();
}, 'user_directory');
onRecordUpdateExecute((e)=>{
  if(e.record.id==='authfail0000001' && e.record.getBool('can_view_all_traces'))throw new Error('Synthetic authority mirror failure');
  e.next();
},'trace_authority');
''')
        common = [str(Path(binary).resolve()), '--dir', str(data_dir or Path(tmp)/'pb_data'), '--migrationsDir', str(root/'pb_migrations'), '--hooksDir', str(hooks)]
        result = subprocess.run(common+['superuser','upsert','admin@example.com','SyntheticAdminPassword123!'],cwd=root,capture_output=True,text=True)
        assert result.returncode == 0, result.stdout+result.stderr
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        with open(Path(tmp)/'server.log','w+') as log:
            proc=subprocess.Popen(common+['serve','--http',f'127.0.0.1:{port}'],cwd=root,stdout=log,stderr=log)
            def request(method,path,body=None,token=None,expected=200):
                headers={'Content-Type':'application/json'}
                if token: headers['Authorization']=token
                req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
                try:
                    with urllib.request.urlopen(req,timeout=20) as r: status,raw=r.status,r.read()
                except urllib.error.HTTPError as e: status,raw=e.code,e.read()
                assert status in (expected if isinstance(expected,tuple) else (expected,)), (method,path,status,raw.decode())
                return json.loads(raw) if raw else None
            try:
                for _ in range(150):
                    try: request('GET','/api/health'); break
                    except (OSError,AssertionError):
                        if proc.poll() is not None: log.seek(0); raise AssertionError(log.read())
                        time.sleep(.1)
                else: raise AssertionError('Server startup timed out')
                request.base_url = f'http://127.0.0.1:{port}'
                yield request
            finally:
                proc.terminate();proc.wait(timeout=15)

def fixture(**overrides):
    return dict(version=1,request_id='a'*32,correlation_id='synthetic',service='test',method='POST',
                route='/api/context/query',started_at='2026-09-27T12:00:00.000Z',duration_ms=12.5,
                status=200,user_id='reported-not-identity',sql='SELECT 1',rows=1,truncated=False,
                spans=[{'name':'sql.execute','offset_ms':1,'duration_ms':10}],**overrides)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);args=parser.parse_args()
    with server(args.binary) as request:
        path=lambda table:'/api/collections/'+table+'/records'
        admin=request('POST','/api/collections/_superusers/auth-with-password',{'identity':'admin@example.com','password':'SyntheticAdminPassword123!'})['token']
        def member(email):
            user=request('POST',path('users'),{'email':email,'name':'Synthetic user','password':'SyntheticUserPassword123!','passwordConfirm':'SyntheticUserPassword123!'},admin)
            token=request('POST','/api/collections/users/auth-with-password',{'identity':email,'password':'SyntheticUserPassword123!'})['token']
            return user,token
        user,token=member('one@example.test');other,other_token=member('two@example.test')
        authority_user=request('POST',path('users'),{'id':'authfail0000001','email':'authority@example.test','name':'Authority','password':'SyntheticUserPassword123!','passwordConfirm':'SyntheticUserPassword123!'},admin)
        authority_token=request('POST','/api/collections/users/auth-with-password',{'identity':'authority@example.test','password':'SyntheticUserPassword123!'})['token']
        request('PATCH',path('users')+'/'+authority_user['id'],{'can_view_all_traces':True},admin,(400,500))
        assert not request('GET',path('users')+'/'+authority_user['id'],token=authority_token)['can_view_all_traces']
        request('GET','/api/context/schema',token=authority_token)
        for actor in [token,admin]:
            request('PATCH',path('trace_authority')+'/'+user['id'],{'can_view_all_traces':True},actor,(400,403,404))
        request('POST','/api/context/query',{'sql':'SELECT * FROM traces'},admin,(400,401,403))
        operation=request('POST',path('operations'),{'source':'synthetic','client_key':'a'*32},token)
        assert operation['owner']==user['id']
        request('POST',path('operations'),{'source':'synthetic','owner':other['id']},token,400)
        request('POST',path('operations'),{'source':'synthetic','client_key':'a'*32},token,400)
        other_operation=request('POST',path('operations'),{'source':'synthetic','client_key':'a'*32},other_token)
        payload=fixture(operation=operation['id'])
        request('POST',path('traces'),fixture(),token,400)
        request('POST',path('traces'),dict(payload,operation=other_operation['id']),token,403)
        request('POST',path('traces'),payload,expected=(400,403))
        request('POST',path('traces'),payload,admin,403)
        row=request('POST',path('traces'),payload,token)
        assert row['created_by']==user['id'] and row['user_id']=='reported-not-identity'
        query=lambda sql,t=token:request('POST','/api/context/query',{'sql':sql},t)
        result=query('SELECT t.request_id,s.name,s.duration_ms FROM traces t JOIN spans s ON s.trace=t.id')
        assert result['rows']==[['a'*32,'sql.execute',10]]
        span_id=query('SELECT id FROM spans')['rows'][0][0]
        request('GET',path('spans')+'/'+span_id,expected=(403,404))
        request('GET','/api/context/schema',expected=(401,403))
        request('GET',path('traces')+'/'+row['id'],token=other_token,expected=404)
        request('GET',path('spans')+'/'+span_id,token=other_token,expected=404)
        request('GET',path('operations')+'/'+operation['id'],token=other_token,expected=404)
        assert request('GET',path('traces'),token=other_token)['totalItems']==0
        assert query('SELECT count(*) FROM traces',other_token)['rows']==[[0]]
        assert query('SELECT t.id,s.name FROM traces t JOIN spans s ON s.trace=t.id',other_token)['rows']==[]
        assert query('SELECT count(*) FROM operations',other_token)['rows']==[[1]]
        # Same producer IDs do not collide across authenticated owners.
        other_row=request('POST',path('traces'),dict(payload,operation=other_operation['id']),other_token)
        assert other_row['created_by']==other['id']
        assert query('SELECT count(*) FROM traces',other_token)['rows']==[[1]]
        # An operator grants read-all, revoking stale sessions atomically.
        request('PATCH',path('users')+'/'+other['id'],{'can_view_all_traces':True},admin)
        query_rejected=lambda t:request('POST','/api/context/query',{'sql':'SELECT * FROM traces'},t,(401,403))
        query_rejected(other_token)
        other_token=request('POST','/api/collections/users/auth-with-password',{'identity':'two@example.test','password':'SyntheticUserPassword123!'})['token']
        assert query('SELECT count(*) FROM traces',other_token)['rows']==[[2]]
        assert len(query('SELECT t.id,s.name FROM traces t JOIN spans s ON s.trace=t.id',other_token)['rows'])==2
        request('GET',path('traces')+'/'+row['id'],token=other_token)
        request('POST',path('traces'),dict(payload,request_id='9'*32),other_token,403)
        for value in [True,False]:
            request('PATCH',path('users')+'/'+other['id'],{'can_view_all_traces':value},other_token,(400,403,404))
            request('POST','/api/batch',{'requests':[{'method':'PATCH','url':path('users')+'/'+other['id'],'body':{'can_view_all_traces':value}}]},other_token,(400,403))
        request('PATCH',path('users')+'/'+other['id'],{'can_view_all_traces':False},admin)
        query_rejected(other_token)
        other_token=request('POST','/api/collections/users/auth-with-password',{'identity':'two@example.test','password':'SyntheticUserPassword123!'})['token']
        assert query('SELECT count(*) FROM traces',other_token)['rows']==[[1]]
        request('GET',path('traces')+'/'+row['id'],token=other_token,expected=404)
        request('GET',path('traces')+'/'+row['id'],expected=(403,404))
        request('POST',path('traces'),payload,token,400)
        for actor in [token,admin]:
            request('PATCH',path('traces')+'/'+row['id'],{'duration_ms':1},actor,403)
            request('DELETE',path('traces')+'/'+row['id'],token=actor,expected=403)
            request('PATCH',path('spans')+'/'+span_id,{'duration_ms':1},actor,403)
            request('DELETE',path('spans')+'/'+span_id,token=actor,expected=403)
            request('POST',path('spans'),{'trace':row['id'],'ordinal':1,'name':'forged'},actor,(400,403))
            request('PATCH',path('operations')+'/'+operation['id'],{'owner':other['id']},actor,403)
            request('DELETE',path('operations')+'/'+operation['id'],token=actor,expected=403)
        for change in [{'created_by':other['id']},{'version':2},{'status':99},{'status':600},{'duration_ms':-1},
                       {'route':'/api/context/query?secret=bad'},{'rows':.1},{'rows':9007199254740992},{'sql':'x'*16385},
                       {'spans':{}},{'spans':None},{'spans':[{'name':'bad name','offset_ms':0,'duration_ms':1}]},
                       {'spans':[{'name':'late','offset_ms':10,'duration_ms':10}]},
                       {'spans':[{'name':'x','offset_ms':0,'duration_ms':1}]*129}]:
            request('POST',path('traces'),dict(payload,request_id='b'*32,**change),token,400)
        # Failed child creation must roll back the parent as well.
        request('POST',path('traces'),dict(payload,request_id='c'*32,spans=[{'name':'synthetic_failure','offset_ms':0,'duration_ms':1}]),token,(400,500))
        assert query('SELECT count(*) FROM traces')['rows']==[[1]]
        # Batch failure must roll back valid preceding ingestion.
        request('POST','/api/batch',{'requests':[
            {'method':'POST','url':path('traces'),'body':dict(payload,request_id='d'*32)},
            {'method':'POST','url':path('traces'),'body':dict(payload,request_id='e'*32,duration_ms=-1)},
        ]},token,400)
        assert query('SELECT count(*) FROM traces')['rows']==[[1]]
        def race(_):return request('POST',path('traces'),dict(payload,request_id='f'*32),token,(200,400))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(race,range(2)))
        assert sum('id' in r for r in results)==1
        for sql in ['SELECT * FROM users','SELECT * FROM _superusers','SELECT * FROM sqlite_master','SELECT * FROM trace_authority','DELETE FROM traces']:
            request('POST','/api/context/query',{'sql':sql},token,(400,403))
        request('POST',path('traces'),dict(payload,request_id='0'*32,status=0),token)
    print('PASS: private ownership/read-all revocation, append-only attributed ingestion, relational spans, validation, duplicate race, transaction rollback and SQL boundaries')

if __name__=='__main__':main()
