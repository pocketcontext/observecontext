#!/usr/bin/env python3
"""Portable trace client against an isolated application, including safe retries."""
import argparse
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
from integration import ROOT, server, fixture


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',required=True)
    parser.add_argument('--write-schema',action='store_true')
    parser.add_argument('--client',type=Path,help='exercise an unchanged published launcher instead of a local test wheel')
    args=parser.parse_args()
    with server(args.binary) as request, tempfile.TemporaryDirectory(prefix='observecontext-skill-') as tmp:
        admin=request('POST','/api/collections/_superusers/auth-with-password',{'identity':'admin@example.com','password':'SyntheticAdminPassword123!'})['token']
        password='SyntheticSkillPassword123!'
        request('POST','/api/collections/users/records',{'email':'skill@example.com','name':'Synthetic skill','password':password,'passwordConfirm':password},admin)
        token=request('POST','/api/collections/users/auth-with-password',{'identity':'skill@example.com','password':password})['token']
        schema=request('GET','/api/context/schema',token=token)
        snapshot=ROOT/'skills/observecontext/references/schema.json'
        if args.write_schema:
            snapshot.write_text(json.dumps(schema,indent=2)+'\n')
            (ROOT/'src/observecontext_client/schema.json').write_text(snapshot.read_text())
        assert json.loads((ROOT/'src/observecontext_client/schema.json').read_text())==schema
        assert json.loads(snapshot.read_text())==schema,'Schema changed; review and regenerate snapshot'
        skill=Path(tmp)/'portable'
        skill.mkdir()
        launcher=skill/'observecontext'
        if args.client:
            source=args.client.resolve().read_text()
        else:
            subprocess.run(['uv','build','--wheel','--out-dir',str(Path(tmp)/'dist')],cwd=ROOT,check=True,capture_output=True)
            wheel=next((Path(tmp)/'dist').glob('*.whl'))
            source=(ROOT/'skills/observecontext/observecontext').read_text()
            source=re.sub(r'# observecontext-client = .*', '# observecontext-client = { path = '+json.dumps(str(wheel))+' }', source)
        launcher.write_text(source);launcher.chmod(0o755)
        env={**os.environ,'UV_NO_CONFIG':'1','UV_CACHE_DIR':str(Path(tmp)/'uv-cache'),'XDG_CACHE_HOME':str(Path(tmp)/'cache'),'OBSERVECONTEXT_URL':request.base_url,'OBSERVECONTEXT_USER_EMAIL':'skill@example.com','OBSERVECONTEXT_USER_PASSWORD':password}
        def cli(*argv,expected=0):
            result=subprocess.run(['uv','run','--script',str(launcher),*argv],env=env,cwd=tmp,capture_output=True,text=True)
            assert password not in result.stdout+result.stderr and token not in result.stdout+result.stderr
            assert result.returncode==expected,(argv,result.returncode,result.stdout,result.stderr)
            return result.stdout
        cli('whoami');cli('check')
        source=Path(tmp)/'events.jsonl'
        source.write_text(json.dumps(fixture())+'\n')
        assert json.loads(cli('ingest',str(source)))=={'inserted':1,'duplicates':0}
        assert json.loads(cli('ingest',str(source)))=={'inserted':0,'duplicates':1}
        source.write_text(json.dumps(dict(fixture(),duration_ms=14))+'\n')
        cli('ingest',str(source),expected=4)
        row=json.loads(cli('trace','a'*32))['traces'][0]
        assert row['duration_ms']==12.5 and row['created_by']
        assert len(json.loads(cli('recent')))==1
        cli('query','SELECT name,duration_ms FROM spans')
        cli('report')
        # Optional fields omitted on ingestion must still compare identically on retry.
        minimal=fixture();minimal['request_id']='b'*32
        for field in ['sql','user_id','rows','truncated','correlation_id','spans']:minimal.pop(field)
        source.write_text(json.dumps(minimal)+'\n')
        assert json.loads(cli('ingest',str(source)))['inserted']==1
        assert json.loads(cli('ingest',str(source)))['duplicates']==1
        # Rotated JSONL records and the active file are read in chronological file order.
        Path(str(source)+'.1').write_text(json.dumps(fixture())+'\n')
        assert json.loads(cli('ingest',str(source)))['duplicates']==2
        source.write_text('{')
        cli('ingest',str(source),expected=2)
        cli('logout')
    print('Portable skill, schema and trace retry checks passed.')

if __name__=='__main__':main()
