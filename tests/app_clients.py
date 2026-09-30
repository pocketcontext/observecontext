#!/usr/bin/env python3
"""Acceptance check for installed application clients against real isolated servers."""
import argparse,contextlib,json,os,socket,subprocess,tempfile,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; BIN=ROOT/'pocketcontext/bin/pocketcontext'
APPS={'dealcontext':'dc','taskcontext':'tc','raisecontext':'rc','wikicontext':'wc','accountcontext':'ac','chatcontext':'cc','peoplecontext':'pc'}
@contextlib.contextmanager
def server(app,tmp):
 root=ROOT/app;folder=Path(tempfile.mkdtemp(prefix=app+'-',dir=tmp));cfg=json.loads((root/'pocketcontext.json').read_text())
 if app!='observecontext':assert cfg.get('tracing',{}).get('delivery')=='buffer',app
 service_env={k:v for k,v in os.environ.items() if not k.startswith(tuple(a.upper()+'_' for a in [*APPS,'observecontext','metacontext'])+('SMTP_','MAILER_','BASE_URL','SOURCE_TOKEN'))}
 service_env['HOME']=str(tmp/'home')
 common=[str(BIN),'--dir',str(folder/'data'),'--migrationsDir',str(root/'pb_migrations'),'--hooksDir',str(root/'pb_hooks')]
 result=subprocess.run(common+['superuser','upsert','test@example.test','SyntheticAdminPassword123!'],cwd=root,env=service_env,capture_output=True)
 assert result.returncode==0,(app,'provision')
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}'
 def req(method,path,body=None,token=None):
  headers={'Content-Type':'application/json'}
  if token:headers['Authorization']=token
  with urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method),timeout=20) as r:return json.load(r)
 with (folder/'server.log').open('w') as log:
  proc=subprocess.Popen(common+['serve','--http',f'127.0.0.1:{port}'],cwd=root,env=service_env,stdout=log,stderr=log)
  try:
   for _ in range(100):
    try:req('GET','/api/health');break
    except OSError:time.sleep(.1)
   else:raise AssertionError((app,'startup'))
   admin=req('POST','/api/collections/_superusers/auth-with-password',{'identity':'test@example.test','password':'SyntheticAdminPassword123!'})['token']
   auth=cfg['authCollection'];user=req('POST',f'/api/collections/{auth}/records',{'email':'client@example.test','name':'Synthetic client','verified':True,'password':'SyntheticClientPassword123!','passwordConfirm':'SyntheticClientPassword123!'},admin)
   if app=='chatcontext':req('POST','/api/collections/team_members/records',{'account':user['id'],'is_admin':False},admin)
   token=req('POST',f'/api/collections/{auth}/auth-with-password',{'identity':'client@example.test','password':'SyntheticClientPassword123!'})['token']
   yield base,req,token,cfg
  finally:proc.terminate();proc.wait(timeout=20)
def main():
 global ROOT,BIN
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--workspace',type=Path,default=ROOT,help='sibling PocketContext application checkouts')
 parser.add_argument('--binary',type=Path,required=True,help='buffer-enabled PocketContext binary')
 parser.add_argument('--apps',nargs='+',choices=list(APPS),default=list(APPS))
 args=parser.parse_args();ROOT=args.workspace.resolve();BIN=args.binary.resolve()
 results=[]
 with tempfile.TemporaryDirectory(prefix='pocketcontext-client-rollout-') as path:
  tmp=Path(path)
  with server('observecontext',tmp) as (dest,query,token,_):
   env={k:v for k,v in os.environ.items() if not k.startswith(tuple(a.upper()+'_' for a in [*APPS,'observecontext','metacontext']))}
   env.update(HOME=str(tmp/'home'),XDG_CACHE_HOME=str(tmp/'cache'),OBSERVECONTEXT_URL=dest,OBSERVECONTEXT_USER_EMAIL='client@example.test',OBSERVECONTEXT_USER_PASSWORD='SyntheticClientPassword123!')
   oc=ROOT/'observecontext/skills/observecontext/scripts/oc.py'
   def command(argv,expected=0):
    result=subprocess.run(['python3',*map(str,argv)],env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==expected,(argv,result.returncode,result.stderr)
    assert 'SyntheticClientPassword' not in result.stdout+result.stderr
    return result
   command([oc,'whoami'])
   for app,short in APPS.items():
    if app not in args.apps:continue
    with server(app,tmp) as (base,request,source_token,cfg):
     prefix=app.upper();identity='AGENT' if cfg['authCollection']=='agents' else 'USER'
     env.update({prefix+'_URL':base,prefix+'_'+identity+'_EMAIL':'client@example.test',prefix+'_'+identity+'_PASSWORD':'SyntheticClientPassword123!'})
     client=ROOT/app/'skills'/app/'scripts'/f'{short}.py'
     command([client,'whoami']);command([client,'sql','SELECT 42 AS synthetic'])
     for disclose in [False,True]:
      argv=[oc,'capture','--url',base,'--service',app+'.client','--upload']
      if disclose:argv+=['--capture-sql']
      result=command(argv+[client,'sql','SELECT 42 AS synthetic'])
      assert json.loads(result.stdout)['rows']==[[42]],(app,result.stdout)
     rows=query('POST','/api/context/query',{'sql':f"SELECT service,sql,operation,status FROM traces WHERE service IN ('{app}', '{app}.client') ORDER BY created,id"},token)['rows']
     assert len(rows)==4,(app,rows)
     assert len({r[2] for r in rows})==2,(app,rows)
     assert sum(r[1]=='SELECT 42 AS synthetic' for r in rows)==2,(app,rows)
     assert all(r[3]==200 for r in rows)
     if 'snapshot' in cfg:
      spans=query('POST','/api/context/query',{'sql':f"SELECT s.name FROM spans s JOIN traces t ON t.id=s.trace WHERE t.service='{app}'"},token)['rows']
      assert {'snapshot.wait','snapshot.build','snapshot.reader_init'}<={r[0] for r in spans},(app,spans)
     if app=='taskcontext':
      wrapped=[oc,'capture','--url',base,'--service',app+'.client','--upload',client]
      command(wrapped+['create','projects',json.dumps({'key':'TRACE','name':'Synthetic private payload'})])
      command(wrapped+['batch',json.dumps([{'method':'POST','url':'/api/collections/projects/records','body':{'key':'BATCH','name':'Synthetic private payload'}}])])
      writes=query('POST','/api/context/query',{'sql':"SELECT service,route,sql,status FROM traces WHERE service IN ('taskcontext','taskcontext.client') AND route != '/api/context/query'"},token)['rows']
      assert len(writes)==4,writes
      assert all(r[2]=='' and r[3]==200 for r in writes),writes
      assert {r[1] for r in writes}=={'/api/collections/projects/records','/api/collections/{collection}/records','/api/batch'},writes
      assert 'Synthetic private payload' not in json.dumps(writes)
     results.append({'app':app,'paired':True,'sql_opt_in':True,'snapshot':'snapshot' in cfg})
     print(json.dumps(results[-1]),flush=True)
 print(json.dumps({'passed':results}))
if __name__=='__main__':main()
