#!/usr/bin/env python3
"""Migrate populated legacy shared traces without inferring ownership from labels."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
from integration import ROOT,server,fixture

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='observecontext-migration-') as tmp:
        base=Path(tmp);old=base/'legacy';old.mkdir();data=base/'data'
        # Historical shared schema remains in the migration chain. Freeze its
        # ingestion/directory helpers as fixtures so shallow CI needs no Git history.
        (old/'pb_migrations').mkdir()
        for source in (ROOT/'pb_migrations').glob('*.js'):
            if source.name < '1790700000':shutil.copy2(source,old/'pb_migrations'/source.name)
        shutil.copytree(ROOT/'pb_hooks',old/'pb_hooks')
        shutil.copy2(ROOT/'tests/fixtures/legacy-traces.js',old/'pb_hooks/traces.js')
        shutil.copy2(ROOT/'tests/fixtures/legacy-user-directory.js',old/'pb_hooks/user_directory.js')
        config=json.loads((ROOT/'pocketcontext.json').read_text());config.pop('snapshot',None)
        config['tables'].pop('operations',None);config['tables']['traces'].remove('operation')
        (old/'pocketcontext.json').write_text(json.dumps(config))
        admin_creds={'identity':'admin@example.com','password':'SyntheticAdminPassword123!'}
        users=[];traces=[]
        with server(args.binary,old,data) as request:
            admin=request('POST','/api/collections/_superusers/auth-with-password',admin_creds)['token']
            for i in range(2):
                creds={'identity':f'owner{i}@example.test','password':'SyntheticMigration123!'}
                user=request('POST','/api/collections/users/records',{'email':creds['identity'],'name':'Legacy owner','password':creds['password'],'passwordConfirm':creds['password']},admin)
                token=request('POST','/api/collections/users/auth-with-password',creds)['token'];users.append((user,creds))
                for j in range(3):
                    payload=fixture();payload.update(request_id=f'{i*3+j:032x}',correlation_id='same-correlation' if j<2 else '',user_id='misleading-other-person')
                    trace=request('POST','/api/collections/traces/records',payload,token);traces.append(trace)
        with server(args.binary,ROOT,data) as request:
            admin=request('POST','/api/collections/_superusers/auth-with-password',admin_creds)['token']
            operation_ids=[]
            for user,creds in users:
                token=request('POST','/api/collections/users/auth-with-password',creds)['token']
                query=lambda sql:request('POST','/api/context/query',{'sql':sql},token)['rows']
                assert query('SELECT count(*) FROM operations')==[[2]]
                assert query('SELECT count(*) FROM traces')==[[3]]
                assert query('SELECT count(*) FROM spans')==[[3]]
                records=request('GET','/api/collections/traces/records',token=token)['items']
                expected=[t for t in traces if t['created_by']==user['id']]
                assert {t['id'] for t in records}=={t['id'] for t in expected}
                paired=[t for t in records if t['correlation_id']]
                assert paired[0]['operation']==paired[1]['operation']
                operation_ids.append({t['operation'] for t in records})
                assert all(t['user_id']=='misleading-other-person' for t in records)
                assert all(t['sql']=='SELECT 1' and t['spans']==expected[0]['spans'] for t in records)
                owner=request('GET','/api/collections/users/records/'+user['id'],token=token)
                assert not owner['can_view_all_traces']
            assert operation_ids[0].isdisjoint(operation_ids[1])
            assert request('GET','/api/collections/traces/records',token=admin)['totalItems']==6
    print('PASS: populated legacy migration preserves records/spans and groups by uploader only; cross-owner correlation remains isolated')
if __name__=='__main__':main()
