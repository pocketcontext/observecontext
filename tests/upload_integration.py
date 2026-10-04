#!/usr/bin/env python3
"""Real opt-in buffer retrieval, paired ingestion and offline replay with stable IDs."""
import argparse
import contextlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from integration import ROOT, server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='observecontext-upload-') as temporary:
        tmp=Path(temporary);source_root=tmp/'source';source_root.mkdir()
        for name in ['pb_hooks','pb_migrations']:(source_root/name).symlink_to(ROOT/name,target_is_directory=True)
        cfg=json.loads((ROOT/'pocketcontext.json').read_text())
        cfg['tracing']={'enabled':True,'delivery':'buffer','service':'source.server','captureSql':True,'maxBytes':1048576}
        (source_root/'pocketcontext.json').write_text(json.dumps(cfg))
        with server(args.binary,root=source_root) as source,server(args.binary) as destination:
            identities=[]
            for request in [source,destination]:
                admin=request('POST','/api/collections/_superusers/auth-with-password',{'identity':'admin@example.com','password':'SyntheticAdminPassword123!'})['token']
                request('POST','/api/collections/users/records',{'email':'one@example.test','name':'Synthetic upload user','password':'SyntheticUserPassword123!','passwordConfirm':'SyntheticUserPassword123!'},admin)
                identities.append(request('POST','/api/collections/users/auth-with-password',{'identity':'one@example.test','password':'SyntheticUserPassword123!'})['token'])
            class Proxy(http.server.BaseHTTPRequestHandler):
                offline=False
                def log_message(self,*args):pass
                def do_GET(self):self.forward()
                def do_POST(self):self.forward()
                def forward(self):
                    body=self.rfile.read(int(self.headers.get('Content-Length',0)))
                    if self.offline:status,raw=503,b'{}'
                    else:
                        req=urllib.request.Request(destination.base_url+self.path,data=body or None,method=self.command,headers={key:self.headers[key] for key in ['Authorization','Content-Type'] if key in self.headers})
                        try:
                            with urllib.request.urlopen(req,timeout=10) as reply:status,raw=reply.status,reply.read()
                        except urllib.error.HTTPError as error:status,raw=error.code,error.read()
                    self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            with http.server.ThreadingHTTPServer(('127.0.0.1',0),Proxy) as proxy:
                worker=threading.Thread(target=proxy.serve_forever,daemon=True);worker.start()
                try:
                    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'XDG_CACHE_HOME':str(tmp/'cache'),'OBSERVECONTEXT_URL':f'http://127.0.0.1:{proxy.server_port}',
                         'OBSERVECONTEXT_USER_EMAIL':'one@example.test','OBSERVECONTEXT_USER_PASSWORD':'SyntheticUserPassword123!',
                         'SOURCE_URL':source.base_url,'SOURCE_TOKEN':identities[0]}
                    cli=ROOT/'src/observecontext_client/cli.py'
                    def command(*argv,expected=0):
                        result=subprocess.run(['python3','-m','observecontext_client',*argv],env=env,text=True,capture_output=True)
                        assert result.returncode==expected,(argv,result.stdout,result.stderr)
                        for secret in [identities[0],identities[1],env['OBSERVECONTEXT_USER_PASSWORD']]:assert secret not in result.stdout+result.stderr
                        return result
                    command('whoami')
                    script=tmp/'client.py';script.write_text('''import json,os,sys,urllib.request,urllib.error
from observecontext_client.instrumentation import instrument_cli
with instrument_cli(service='skill.client'):
 exec(''' + repr('''import json,os,sys,urllib.request,urllib.error
for path,body in [('/api/context/query',{'sql':'SELECT 42 AS synthetic'}),('/api/collections/operations/records',{'source':'synthetic-write'})]:
 request=urllib.request.Request(os.environ['SOURCE_URL']+path,data=json.dumps(body).encode(),headers={'Authorization':os.environ['SOURCE_TOKEN'],'Content-Type':'application/json'})
 with urllib.request.urlopen(request) as response:response.read()
sys.exit(int(os.environ.get('SOURCE_EXIT','0')))
''') + ')\n')
                    capture=['capture','--url',source.base_url,'--service','skill.client','--upload','--capture-sql','--','python3',str(script)]
                    result=command(*capture)
                    assert 'delivered 2' in result.stderr,result.stderr
                    token=identities[1]
                    query=lambda sql:destination('POST','/api/context/query',{'sql':sql},token)['rows']
                    rows=query('SELECT operation,correlation_id,service,sql FROM traces ORDER BY correlation_id,service')
                    assert len(rows)==4,rows
                    assert len({row[0] for row in rows})==1,rows
                    assert len({row[1] for row in rows})==2,rows
                    assert {row[2] for row in rows}=={'skill.client','source.server'}
                    assert sum(row[3]=='SELECT 42 AS synthetic' for row in rows)==2,rows
                    Proxy.offline=True;env['SOURCE_EXIT']='7'
                    result=command(*capture,expected=7)
                    assert 'private queue' in result.stderr,result.stderr
                    pending=list((tmp/'cache/observecontext/pending').glob('*/*.json'));assert len(pending)==2
                    for file in pending:
                        text=file.read_text()
                        assert identities[0] not in text and identities[1] not in text and env['OBSERVECONTEXT_USER_PASSWORD'] not in text
                    Proxy.offline=False
                    assert json.loads(command('flush').stdout)['delivered_pairs']==2
                    assert json.loads(command('flush').stdout)['delivered_pairs']==0
                    assert query('SELECT count(*) FROM traces')==[[8]]
                    assert query('SELECT count(*) FROM operations')==[[2]]
                    assert not list((tmp/'cache/observecontext/pending').glob('*/*.json'))
                    print('Opt-in HTTP capture, paired operation upload, nonfatal outage and account-bound replay passed.')
                finally:proxy.shutdown();worker.join()

if __name__=='__main__':main()
