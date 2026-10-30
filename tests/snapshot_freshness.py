#!/usr/bin/env python3
"""Synthetic read-after-write checks for reused filtered SQL snapshots.

The pinned server retains each requester's private snapshot while the source
database is unchanged. These checks prove reuse with X-Context-Snapshot-At
(unchanged on a cache hit), then require new operations, traces and spans and
operator-managed read-all grants and revocations to be visible on the very next
read, with owners confined to their own telemetry and no rows leaking between
requesters. Telemetry here is synthetic; the collector itself is not traced.
"""
import argparse
import concurrent.futures
import json
import urllib.error
import urllib.request
from integration import server, fixture

PASSWORD = 'SyntheticUserPassword123!'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    with server(args.binary) as request:
        path = lambda table: '/api/collections/' + table + '/records'
        admin = request('POST', '/api/collections/_superusers/auth-with-password',
                        {'identity': 'admin@example.com', 'password': 'SyntheticAdminPassword123!'})['token']

        def login(email):
            return request('POST', '/api/collections/users/auth-with-password', {'identity': email, 'password': PASSWORD})['token']

        users, tokens = {}, {}
        for name in ('alice', 'bob', 'viewer'):
            users[name] = request('POST', path('users'), {'email': name + '@example.test', 'name': 'Synthetic ' + name,
                                                          'password': PASSWORD, 'passwordConfirm': PASSWORD}, admin)
            tokens[name] = login(name + '@example.test')

        def sql(query, name, expected=200):
            body = json.dumps({'sql': query}).encode()
            req = urllib.request.Request(request.base_url + '/api/context/query', data=body, method='POST',
                                         headers={'Content-Type': 'application/json', 'Authorization': tokens[name]})
            try:
                with urllib.request.urlopen(req, timeout=25) as response:
                    status, raw, headers = response.status, response.read(), response.headers
            except urllib.error.HTTPError as error:
                status, raw, headers = error.code, error.read(), error.headers
            assert status in (expected if isinstance(expected, tuple) else (expected,)), (query, name, status, raw.decode())
            if status != 200:
                return None, None
            assert headers['X-Context-Scope'] == 'authorized-snapshot'
            result = json.loads(raw)
            assert not result['truncated']
            return result['rows'], headers['X-Context-Snapshot-At']

        everything = ("SELECT 'operations', owner FROM operations UNION ALL SELECT 'traces', created_by FROM traces UNION ALL "
                      "SELECT 'spans', t.created_by FROM spans s JOIN traces t ON t.id = s.trace UNION ALL "
                      "SELECT 'user_directory', id FROM user_directory")

        def view(name):
            rows, at = sql(everything, name)
            counts = {}
            for table, owner in rows:
                counts.setdefault(table, {}).setdefault(owner, 0)
                counts[table][owner] += 1
            return counts, at

        def warm(name):
            first, at = view(name)
            again, at2 = view(name)
            assert first == again and at == at2, ('expected a reused snapshot for an unchanged source', name)
            return first, at

        def fresh(name, before):
            result, at = view(name)
            assert at != before, ('a committed write must invalidate the reused snapshot', name)
            return result

        def stamps():
            return {name: warm(name)[1] for name in tokens}

        def own(name, operations, traces, spans):
            uid = users[name]['id']
            result = {'user_directory': {uid: 1}}
            for table, count in (('operations', operations), ('traces', traces), ('spans', spans)):
                if count:
                    result[table] = {uid: count}
            return result

        def ingest(name, request_id):
            operation = request('POST', path('operations'), {'source': 'synthetic'}, tokens[name])
            request('POST', path('traces'), dict(fixture(operation=operation['id']), request_id=request_id), tokens[name])

        schema = request('GET', '/api/context/schema', token=tokens['alice'])
        assert schema['permissionModel'] == 'filtered-snapshot' and 'snapshot' not in schema, 'background snapshots must stay disabled'
        assert schema['snapshotLimits']['maxConcurrentPerRequester'] == 0, 'per-requester admission must stay disabled'

        # Everyone starts with only their own directory row, cached.
        for name in tokens:
            assert warm(name)[0] == own(name, 0, 0, 0), name

        # A new operation and trace are visible to their owner on the next read, and to nobody else.
        before = stamps()
        ingest('alice', 'a' * 32)
        assert fresh('alice', before['alice']) == own('alice', 1, 1, 1)
        assert fresh('bob', before['bob']) == own('bob', 0, 0, 0)
        assert fresh('viewer', before['viewer']) == own('viewer', 0, 0, 0)
        before = stamps()
        ingest('bob', 'b' * 32)
        assert fresh('bob', before['bob']) == own('bob', 1, 1, 1)
        assert fresh('alice', before['alice']) == own('alice', 1, 1, 1)

        # An operator read-all grant revokes the old session; the fresh session sees all owners immediately.
        before = stamps()
        request('PATCH', path('users') + '/' + users['viewer']['id'], {'can_view_all_traces': True}, admin)
        sql(everything, 'viewer', expected=(401, 403))
        tokens['viewer'] = login('viewer@example.test')
        ids = {name: users[name]['id'] for name in users}
        all_rows = {'operations': {ids['alice']: 1, ids['bob']: 1}, 'traces': {ids['alice']: 1, ids['bob']: 1},
                    'spans': {ids['alice']: 1, ids['bob']: 1}, 'user_directory': {uid: 1 for uid in ids.values()}}
        assert fresh('viewer', before['viewer']) == all_rows
        assert fresh('alice', before['alice']) == own('alice', 1, 1, 1), 'a grant to one user widens nobody else'
        assert fresh('bob', before['bob']) == own('bob', 1, 1, 1)

        # Later ingestion reaches the read-all viewer's next read.
        before = stamps()
        ingest('alice', 'c' * 32)
        all_rows['operations'][ids['alice']] = all_rows['traces'][ids['alice']] = all_rows['spans'][ids['alice']] = 2
        assert fresh('viewer', before['viewer']) == all_rows
        assert fresh('alice', before['alice']) == own('alice', 2, 2, 2)
        assert fresh('bob', before['bob']) == own('bob', 1, 1, 1)

        # Concurrent reads from several sessions of one account are admitted (no per-requester 429).
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: view('viewer')[0], range(8)))
        assert all(result == all_rows for result in results)

        # Revocation: the old session is rejected and the cached read-all copy is never served again.
        before = stamps()
        request('PATCH', path('users') + '/' + users['viewer']['id'], {'can_view_all_traces': False}, admin)
        sql(everything, 'viewer', expected=(401, 403))
        tokens['viewer'] = login('viewer@example.test')
        assert fresh('viewer', before['viewer']) == own('viewer', 0, 0, 0)
        assert fresh('alice', before['alice']) == own('alice', 2, 2, 2)

        # Disablement is enforced before any retained snapshot can be served.
        warm('bob')
        request('PATCH', path('users') + '/' + users['bob']['id'], {'disabled': True}, admin)
        sql(everything, 'bob', expected=(401, 403))
    print('Snapshot reuse read-after-write, read-all grant/revocation, privacy and disablement checks passed')


if __name__ == '__main__':
    main()
