#!/usr/bin/env python3
"""Actual server -> private JSONL -> REST collector -> SQL end-to-end trace test."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from integration import ROOT, server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);args=parser.parse_args()
    binary=str(Path(args.binary).resolve())
    cli=ROOT/'skills/observecontext/scripts/oc.py'
    with tempfile.TemporaryDirectory(prefix='observecontext-tracing-') as tmp, server(binary) as destination:
        temp=Path(tmp)
        config=json.loads((ROOT/'pocketcontext.json').read_text())
        spool=temp/'server.jsonl'
        config['tracing']={'enabled':True,'service':'synthetic-server','path':str(spool),'captureSql':True,'maxBytes':1048576}
        config_path=temp/'source.json';config_path.write_text(json.dumps(config))
        common=[binary,'--dir',str(temp/'source-data'),'--migrationsDir',str(ROOT/'pb_migrations'),'--hooksDir',str(ROOT/'pb_hooks'),'--contextConfig',str(config_path)]
        setup=subprocess.run(common+['superuser','upsert','admin@example.com','SyntheticAdminPassword123!'],cwd=ROOT,capture_output=True,text=True)
        assert setup.returncode==0,setup.stdout+setup.stderr
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        with (temp/'source.log').open('w+') as log:
            proc=subprocess.Popen(common+['serve','--http',f'127.0.0.1:{port}'],cwd=ROOT,stdout=log,stderr=log)
            def source(method,path,body=None,token=None):
                headers={'Content-Type':'application/json'}
                if token:headers['Authorization']=token
                request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
                with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)
            try:
                for _ in range(100):
                    try:source('GET','/api/health');break
                    except OSError:
                        if proc.poll() is not None:log.seek(0);raise AssertionError(log.read())
                        time.sleep(.1)
                else:raise AssertionError('source startup timed out')
                for request in [source,destination]:
                    admin=request('POST','/api/collections/_superusers/auth-with-password',{'identity':'admin@example.com','password':'SyntheticAdminPassword123!'})['token']
                    request('POST','/api/collections/users/records',{'email':'skill@example.com','name':'Synthetic tracing','password':'SyntheticSkillPassword123!','passwordConfirm':'SyntheticSkillPassword123!'},admin)
                env={**os.environ,'XDG_CACHE_HOME':str(temp/'cache'),'OBSERVECONTEXT_URL':base,'OBSERVECONTEXT_USER_EMAIL':'skill@example.com','OBSERVECONTEXT_USER_PASSWORD':'SyntheticSkillPassword123!'}
                captured=temp/'client.jsonl'
                # Capture an unmodified standard-library skill client. Authentication is excluded.
                result=subprocess.run(['python3',str(cli),'capture','--url',base,'--service','synthetic-client','--output',str(captured),'--capture-sql',str(cli),'query','SELECT count(id) FROM traces'],env=env,capture_output=True,text=True)
                assert result.returncode==0,result.stdout+result.stderr
                clients=[json.loads(line) for line in captured.read_text().splitlines()]
                assert len(clients)==1 and clients[0]['route']=='/api/context/query'
                for _ in range(100):
                    events=[json.loads(line) for line in spool.read_text().splitlines()]
                    queries=[event for event in events if event['route']=='/api/context/query']
                    if queries:break
                    time.sleep(.05)
                assert queries,events
                event=queries[0]
                assert event['correlation_id']==clients[0]['correlation_id']
                assert event['sql']=='SELECT count(id) FROM traces'
                assert event['status']==200 and event['user_id']
                assert any(span['name'].startswith('sql.') for span in event['spans'])
                assert 'SyntheticSkillPassword' not in spool.read_text()+captured.read_text()
                env['OBSERVECONTEXT_URL']=destination.base_url
                def command(*parts):
                    result=subprocess.run(['python3',str(cli),*parts],env=env,capture_output=True,text=True)
                    assert result.returncode==0,result.stdout+result.stderr
                    return json.loads(result.stdout)
                assert command('ingest',str(spool))['inserted']>=1
                assert command('ingest',str(captured))['inserted']==1
                assert command('ingest',str(spool))['duplicates']>=1
                rows=command('query',"SELECT service,correlation_id FROM traces WHERE correlation_id='"+event['correlation_id']+"' ORDER BY service")['rows']
                assert len(rows)==2 and rows[0][1]==rows[1][1]
                print('Server, skill capture, collector and correlated SQL checks passed.')
            finally:
                proc.terminate();proc.wait(timeout=20)

if __name__=='__main__':main()
