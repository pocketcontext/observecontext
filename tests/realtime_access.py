#!/usr/bin/env python3
"""An existing realtime subscription loses access on disable and stays revoked."""
import argparse
import json
import queue
import threading
import urllib.request

from integration import server, fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    with server(args.binary) as request:
        admin = request('POST', '/api/collections/_superusers/auth-with-password', {
            'identity': 'admin@example.com', 'password': 'SyntheticAdminPassword123!',
        })['token']
        credentials = {'identity': 'realtime@example.test', 'password': 'SyntheticRealtimePassword123!'}
        user = request('POST', '/api/collections/users/records', {
            'email': credentials['identity'], 'name': 'Realtime',
            'password': credentials['password'], 'passwordConfirm': credentials['password'],
        }, admin)
        user_path = '/api/collections/users/records/' + user['id']
        token = request('POST', '/api/collections/users/auth-with-password', credentials)['token']
        producer = request('POST', '/api/collections/users/records', {
            'email':'producer@example.test','name':'Producer','password':credentials['password'],'passwordConfirm':credentials['password'],
        }, admin)
        producer_token=request('POST','/api/collections/users/auth-with-password',{'identity':'producer@example.test','password':credentials['password']})['token']
        messages = queue.Queue()

        def listen():
            try:
                with urllib.request.urlopen(request.base_url + '/api/realtime', timeout=10) as response:
                    event, data = '', ''
                    for raw in response:
                        line = raw.decode().strip()
                        if line.startswith('event:'):
                            event = line[6:].strip()
                        elif line.startswith('data:'):
                            data = line[5:].strip()
                        elif not line and event:
                            messages.put((event, json.loads(data)))
                            event, data = '', ''
            except Exception as error:
                messages.put(('error', str(error)))

        threading.Thread(target=listen, daemon=True).start()
        event, data = messages.get(timeout=5)
        assert event == 'PB_CONNECT', (event, data)
        client_id = data['clientId']
        subscriptions = {'clientId': client_id, 'subscriptions': ['traces/*']}
        request('POST', '/api/realtime', subscriptions, token, expected=204)
        trace = request('POST', '/api/collections/traces/records', fixture(), producer_token)
        event, data = messages.get(timeout=5)
        assert event.startswith('traces/') and data['record']['id'] == trace['id'], (event, data)
        trace_path = '/api/collections/traces/records/' + trace['id']
        for disabled in (True, False):
            request('PATCH', user_path, {'disabled': disabled}, admin)
            trace = request('POST', '/api/collections/traces/records', dict(fixture(),request_id=('b' if disabled else 'c')*32), producer_token)
            try:
                unexpected = messages.get(timeout=0.5)
                raise AssertionError(('Revoked subscriber received data', unexpected))
            except queue.Empty:
                pass
            request('POST', '/api/realtime', subscriptions, token, expected=401)
        fresh = request('POST', '/api/collections/users/auth-with-password', credentials)['token']
        request('POST', '/api/realtime', subscriptions, fresh, expected=204)
        trace = request('POST', '/api/collections/traces/records', dict(fixture(),request_id='d'*32), producer_token)
        event, data = messages.get(timeout=5)
        assert data['record']['request_id'] == 'd'*32, (event, data)
    print('PASS: realtime subscriptions lose access on disable and require fresh login after re-enable')


if __name__ == '__main__':
    main()
