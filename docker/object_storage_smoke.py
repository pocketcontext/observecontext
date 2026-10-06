#!/usr/bin/env python3
"""Synthetic primary S3 uploads, access checks and complete Litestream recovery."""
import argparse
import importlib.util
import json
import pathlib
import secrets
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT.name
PREFIX = APP.upper()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('smoke', ROOT / 'docker/smoke.py')
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)
    run_id = secrets.token_hex(5)
    network = APP + '-s3-' + run_id
    minio = network + '-minio'
    failed = True
    try:
        with tempfile.TemporaryDirectory(prefix=APP + '-s3-') as td:
            tmp = pathlib.Path(td)
            # Independent bucket policies and credentials match production separation.
            for role in ('files', 'replica'):
                policy = {'Version': '2012-10-17', 'Statement': [
                    {'Effect': 'Allow', 'Action': ['s3:*'],
                     'Resource': [f'arn:aws:s3:::{role}', f'arn:aws:s3:::{role}/*']}]}
                (tmp / (role + '.json')).write_text(json.dumps(policy))
            s.docker('build', '-f', str(ROOT / 'docker/minio.Dockerfile'), '-t', s.MINIO_IMAGE,
                     str(ROOT / 'docker'), timeout=1200)
            s.docker('network', 'create', network)
            s.networks.append(network)
            root_key, root_secret = s.secret('fixture-root'), s.secret(secrets.token_hex(24))
            mc = {'MC_HOST_test': s.secret(f'http://{root_key}:{root_secret}@127.0.0.1:9000')}
            s.containers.append(minio)
            s.docker('run', '-d', '--name', minio, '--network', network,
                     '-v', str(tmp) + ':/fixtures:ro', '-e', 'MINIO_ROOT_USER', '-e', 'MINIO_ROOT_PASSWORD',
                     s.MINIO_IMAGE, 'server', '/data',
                     env={'MINIO_ROOT_USER': root_key, 'MINIO_ROOT_PASSWORD': root_secret})
            for _ in range(45):
                status, _ = s.docker('exec', '-e', 'MC_HOST_test', minio, 'mc', 'mb',
                    '--ignore-existing', 'test/files', 'test/replica', env=mc, ok=False)
                if status == 0:
                    break
                time.sleep(1)
            else:
                raise s.Failure('Synthetic MinIO did not become ready')
            s.docker('exec', '-e', 'FIXTURE_ROOT_KEY', '-e', 'FIXTURE_ROOT_SECRET', minio,
                     'sh', '-c', 'mc alias set test http://127.0.0.1:9000 "$FIXTURE_ROOT_KEY" "$FIXTURE_ROOT_SECRET"',
                     env={'FIXTURE_ROOT_KEY': root_key, 'FIXTURE_ROOT_SECRET': root_secret})
            keys = {}
            for role in ('files', 'replica'):
                key, password = s.secret('fixture-' + role), s.secret(secrets.token_hex(24))
                keys[role] = (key, password)
                env = {**mc, 'FIXTURE_KEY': key, 'FIXTURE_SECRET': password}
                s.docker('exec', '-e', 'MC_HOST_test', '-e', 'FIXTURE_KEY', '-e', 'FIXTURE_SECRET',
                         minio, 'sh', '-c', 'mc admin user add test "$FIXTURE_KEY" "$FIXTURE_SECRET"', env=env)
                s.docker('exec', '-e', 'MC_HOST_test', minio, 'mc', 'admin', 'policy', 'create',
                         'test', role, '/fixtures/' + role + '.json', env=mc)
                s.docker('exec', '-e', 'MC_HOST_test', minio, 'mc', 'admin', 'policy', 'attach',
                         'test', role, '--user', key, env=mc)
            env = s.once_env({PREFIX + '_S3_BUCKET': 'files',
                PREFIX + '_S3_ENDPOINT': f'http://{minio}:9000', PREFIX + '_S3_REGION': 'us-east-1',
                PREFIX + '_S3_ACCESS_KEY_ID': keys['files'][0], PREFIX + '_S3_SECRET_ACCESS_KEY': keys['files'][1],
                'LITESTREAM_BUCKET': 'replica', 'LITESTREAM_PATH': 'synthetic/data',
                'LITESTREAM_ENDPOINT': f'http://{minio}:9000', 'LITESTREAM_REGION': 'us-east-1',
                'LITESTREAM_ACCESS_KEY_ID': keys['replica'][0], 'LITESTREAM_SECRET_ACCESS_KEY': keys['replica'][1],
                'LITESTREAM_SYNC_INTERVAL': '1h'})
            first, second = network + '-source', network + '-restored'
            s.run_app(args.image, first, first, env, network)
            base = s.wait_up(first)
            admin = s.superuser_token(base, env[PREFIX + '_SUPERUSER_EMAIL'], env[PREFIX + '_SUPERUSER_PASSWORD'])
            password = s.secret(secrets.token_urlsafe(24))
            user = s.provision_user(base, admin, 'owner@example.test', password)
            s.provision_user(base, admin, 'other@example.test', password)

            def api(method, path, body=None, token=admin, expected=200):
                status, _, value = s.http(method, base + path, body=body, token=token)
                s.check(status == expected, method + ' ' + path.split('?')[0] + ' returns expected status')
                return value

            owner_token = s.secret(api('POST', '/api/collections/users/auth-with-password',
                {'identity': 'owner@example.test', 'password': password}, token=None)['token'])
            other_token = s.secret(api('POST', '/api/collections/users/auth-with-password',
                {'identity': 'other@example.test', 'password': password}, token=None)['token'])
            client = s.Client(base, 'owner@example.test', password, tmp / 'home-source')
            write = getattr(s, 'write_record', getattr(s, 'write_batch', None))
            record, metadata = write(client, user)
            s.check_records(client, record, metadata)
            collection = api('POST', '/api/collections', {
                'name': 'synthetic_storage_files', 'type': 'base',
                'listRule': 'owner = @request.auth.id', 'viewRule': 'owner = @request.auth.id',
                'createRule': '@request.auth.id != "" && owner = @request.auth.id',
                'fields': [{'name': 'owner', 'type': 'relation', 'collectionId': '_pb_users_auth_',
                            'maxSelect': 1, 'required': True},
                           {'name': 'original', 'type': 'file', 'maxSelect': 1,
                            'maxSize': 1048576, 'protected': True, 'required': True}]})
            payload = b'Synthetic protected evidence\x00\xff\n' + secrets.token_bytes(2048)

            def upload():
                boundary = 'fixture-' + secrets.token_hex(12)
                raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="owner"\r\n\r\n{user}\r\n'
                       f'--{boundary}\r\nContent-Disposition: form-data; name="original"; filename="evidence.bin"\r\n'
                       'Content-Type: application/octet-stream\r\n\r\n').encode() + payload + f'\r\n--{boundary}--\r\n'.encode()
                req = urllib.request.Request(base + '/api/collections/synthetic_storage_files/records', data=raw,
                    headers={'Authorization': owner_token, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
                try:
                    with urllib.request.urlopen(req, timeout=15) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, None

            status, file_record = upload()
            s.check(status == 200, 'ordinary owner uploads a protected file through PocketBase')
            file_path = '/api/files/' + collection['id'] + '/' + file_record['id'] + '/' + file_record['original']

            def download(token, allowed, path=None):
                suffix = ''
                if token:
                    signed = s.secret(api('POST', '/api/files/token', token=token)['token'])
                    suffix = '?token=' + urllib.parse.quote(signed)
                try:
                    with urllib.request.urlopen(base + (path or file_path) + suffix, timeout=15) as response:
                        status, data = response.status, response.read()
                except urllib.error.HTTPError as error:
                    status, data = error.code, error.read()
                s.check(status == 200 and data == payload if allowed else status in (401, 403, 404),
                        'protected original bytes and independent authorization preserved')

            download(owner_token, True)
            download(other_token, False)
            download(None, False)
            s.check(s.docker('exec', first, 'sh', '-c',
                'find /storage/pb_data/storage -type f 2>/dev/null || true')[1].strip() == '', 'no local originals')
            state = api('GET', '/api/context/maintenance')
            frozen = api('PUT', '/api/context/maintenance', {'readOnly': True, 'expectedGeneration': state['generation']})
            s.check(frozen['state'] == 'read_only' and upload()[0] == 503, 'freeze rejects new file uploads')
            download(owner_token, True)
            s.stop(first)
            s.docker('start', first)
            base = s.wait_up(first)
            s.check(api('GET', '/api/context/maintenance')['state'] == 'read_only', 'frozen restart retains marker')
            download(owner_token, True)
            thaw = api('PUT', '/api/context/maintenance', {'readOnly': False, 'expectedGeneration': frozen['generation']})
            late_status, late_file = upload()
            s.check(late_status == 200, 'late upload succeeds after thaw before final shutdown')
            late_path = '/api/files/' + collection['id'] + '/' + late_file['id'] + '/' + late_file['original']
            frozen = api('PUT', '/api/context/maintenance', {'readOnly': True, 'expectedGeneration': thaw['generation']})
            s.stop(first)
            s.check_logs(first)
            s.docker('volume', 'create', second)
            s.volumes.append(second)
            restore = ['run', '--rm', '--network', network, '-v', second + ':/storage']
            for key in env:
                restore.extend(['-e', key])
            s.docker(*restore, '--entrypoint', 'sh', args.image, '-c',
                'mkdir -p /storage/pb_data; exec litestream restore -config /etc/litestream.yml /storage/pb_data/data.db', env=env)
            # A clean Litestream exit alone cannot prove the final commit reached S3.
            s.docker('run', '--rm', '-v', first + ':/source:ro', '-v', second + ':/restored',
                '-v', str(ROOT / 'docker') + ':/checks:ro', '--entrypoint', 'sh', args.image, '-c',
                'cp -a /source/pb_data /tmp/source; cp -a /restored/pb_data /tmp/restored; '
                'python3 /checks/verify_recovery.py /tmp/source/data.db /tmp/restored/data.db '
                '/tmp/source/auxiliary.db /restored/pb_data/auxiliary.db && '
                'cp /source/pb_data/maintenance.json /restored/pb_data/maintenance.json && '
                'chmod 600 /restored/pb_data/maintenance.json')
            s.check(True, 'entire restored database equals stopped source, including late upload')
            s.docker('rm', first)
            s.docker('volume', 'rm', first)
            s.volumes.remove(first)
            s.run_app(args.image, second, second, env, network)
            s.volumes.remove(second)  # run_app registers the already-created volume again.
            base = s.wait_up(second)
            s.check(api('GET', '/api/context/maintenance')['state'] == 'read_only', 'fresh restored host stays frozen')
            download(owner_token, True, late_path)
            download(other_token, False, late_path)
            download(None, False, late_path)
            download(owner_token, True)
            download(other_token, False)
            download(None, False)
            api('PUT', '/api/context/maintenance', {'readOnly': False, 'expectedGeneration': frozen['generation']})
            client = s.Client(base, 'owner@example.test', password, tmp / 'home-restored')
            s.check_records(client, record, metadata)
            automatic_status, automatic_file = upload()
            s.check(automatic_status == 200, 'last upload before automatic host recovery succeeds')
            automatic_path = '/api/files/' + collection['id'] + '/' + automatic_file['id'] + '/' + automatic_file['original']
            s.stop(second)
            s.check_logs(second)
            s.docker('rm', second)
            s.docker('volume', 'rm', second)
            s.volumes.remove(second)
            # Disaster recovery: normal entrypoint, truly empty volume, no manual
            # SQLite restoration, auxiliary database or maintenance marker copying.
            third = network + '-automatic'
            s.run_app(args.image, third, third, env, network)
            base = s.wait_up(third)
            restored_admin = s.superuser_token(base, env[PREFIX + '_SUPERUSER_EMAIL'], env[PREFIX + '_SUPERUSER_PASSWORD'])
            s.check(api('GET', '/api/context/maintenance', token=restored_admin)['state'] == 'writable',
                    'normal empty-volume recovery is writable without a transferred freeze marker')
            download(owner_token, True)
            download(other_token, False)
            download(None, False)
            download(owner_token, True, late_path)
            download(owner_token, True, automatic_path)
            download(other_token, False, automatic_path)
            download(None, False, automatic_path)
            client = s.Client(base, 'owner@example.test', password, tmp / 'home-automatic')
            s.check_records(client, record, metadata)
            s.docker('exec', third, 'python3', '-c',
                'from pathlib import Path; import sqlite3; '
                'p=Path("/storage/pb_data/auxiliary.db"); assert p.is_file(); '
                'db=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True); '
                'assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)')
            s.check('post-restore integrity check passed' in s.logs(third), 'normal entrypoint restored and verified SQLite')
            s.stop(third)
            s.check_logs(third)
            failed = False
            print('PASS: primary S3 protected uploads, freeze/restart, full final-sync equality, destroyed source, frozen recovery and automatic empty-volume recovery')
    finally:
        s.report_and_clean(failed)


if __name__ == '__main__':
    main()
