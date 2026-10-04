"""Bounded, account-bound local delivery queue. No credentials enter queue files."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

MAX_PENDING_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 512 * 1024


def cached_identity(session):
    """Unverified claims bind pending data; live auth verifies them before replay."""
    try:
        encoded = session['token'].split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['id']
        return value if isinstance(value, str) and value else None
    except (KeyError, ValueError, TypeError, IndexError):
        return None


class Delivery:
    def __init__(self, oc, cfg, spool=None, timeout=10):
        self.oc, self.cfg = oc, cfg
        self.session = oc.load_session(cfg)
        self.owner = cached_identity(self.session)
        self.deadline = time.monotonic() + timeout
        if not self.owner:
            self.authenticate()
        self.account = {'url': cfg['url'], 'email': cfg['email'].casefold(), 'id': self.owner}
        key = hashlib.sha256(json.dumps(self.account, sort_keys=True).encode()).hexdigest()[:32]
        base = Path(spool) if spool else oc.cache_file(cfg).parent / 'pending'
        self.directory = base / key
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise ValueError('pending directory must not be a symlink')
        info = self.directory.stat()
        if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
            raise ValueError('pending directory must be owned by this user')
        os.chmod(self.directory, 0o700)

    def send(self, method, path, body=None, token=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('telemetry delivery deadline exceeded')
        return self.oc.send(self.cfg, method, path, body, token, timeout=min(2, remaining))

    def authenticate(self):
        data = None
        if self.session:
            status, data = self.send('POST', '/api/collections/users/auth-refresh', token=self.session['token'])
            if status != 200:
                data = None
        if data is None and self.cfg.get('password'):
            status, data = self.send('POST', '/api/collections/users/auth-with-password',
                                     {'identity': self.cfg['email'], 'password': self.cfg['password']})
            if status != 200:
                data = None
        record = data.get('record', {}) if isinstance(data, dict) else {}
        if (record.get('collectionName') != 'users' or not record.get('id')
                or record.get('email', '').casefold() != self.cfg['email'].casefold()
                or not isinstance(data.get('token'), str)):
            raise ValueError('ordinary ObserveContext authentication required')
        if self.owner and self.owner != record['id']:
            raise ValueError('pending data belongs to a different ObserveContext account')
        self.owner = record['id']
        self.session = self.oc.auth_session(self.cfg, data, 'password' if self.cfg.get('password') else 'google')

    def lock(self, name):
        fd = os.open(self.directory / name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(fd)
            raise ValueError('unsafe queue lock')
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def enqueue(self, operation_key, source, events):
        entry = {'version': 1, 'account': self.account, 'operation_key': operation_key,
                 'source': source, 'events': events}
        raw = json.dumps(entry, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError('pending trace pair exceeds size limit')
        fd = self.lock('.append.lock')
        temporary = None
        try:
            size = sum(p.lstat().st_size for p in self.directory.glob('*.json'))
            if size + len(raw) > MAX_PENDING_BYTES:
                raise ValueError('pending trace queue is full')
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.queue-', delete=False) as file:
                temporary = Path(file.name)
                os.fchmod(file.fileno(), 0o600)
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.directory / (uuid.uuid4().hex + '.json'))
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            os.close(fd)

    def query(self, sql):
        status, result = self.send('POST', '/api/context/query', {'sql': sql}, self.session['token'])
        if status != 200 or not isinstance(result, dict) or result.get('truncated'):
            raise ValueError('pending trace lookup failed')
        return [dict(zip(result['columns'], row)) for row in result['rows']]

    def operation(self, key, source):
        literal = self.oc.literal
        lookup = ('SELECT id,owner,source,client_key,correlation_id FROM operations WHERE owner=' + literal(self.owner)
                  + ' AND client_key=' + literal(key) + ' LIMIT 1')
        existing = self.query(lookup)
        if existing:
            if existing[0]['source'] != source or existing[0]['correlation_id'] != key:
                raise ValueError('operation key conflicts with existing content')
            return existing[0]['id']
        status, record = self.send('POST', self.oc.records('operations'),
                                   {'source': source, 'client_key': key, 'correlation_id': key}, self.session['token'])
        if status != 200 or not isinstance(record, dict) or not record.get('id'):
            # An uncertain response is resolved on the next replay through the unique owner/key.
            raise ValueError('operation creation failed; retained for retry')
        return record['id']

    def upload(self, event, operation):
        payload = dict(event, operation=operation)
        expected = self.oc.canonical(payload)
        literal = self.oc.literal
        lookup = ('SELECT ' + ','.join('"' + f + '"' for f in self.oc.TRACE_FIELDS)
                  + ' FROM traces WHERE created_by=' + literal(self.owner)
                  + ' AND service=' + literal(event['service'])
                  + ' AND request_id=' + literal(event['request_id']) + ' LIMIT 1')
        existing = self.query(lookup)
        if existing:
            if self.oc.canonical(existing[0]) != expected:
                raise ValueError('trace id conflicts with existing content')
            return
        status, _ = self.send('POST', self.oc.records('traces'), payload, self.session['token'])
        if status != 200:
            raise ValueError('trace upload failed; retained for retry')

    def flush(self, timeout=10):
        self.deadline = time.monotonic() + timeout
        fd = self.lock('.flush.lock')
        count = 0
        try:
            self.authenticate()  # Live identity must match queue binding, even for broad viewers.
            for path in sorted(self.directory.glob('*.json')):
                if time.monotonic() >= self.deadline:
                    raise TimeoutError('pending trace delivery deadline exceeded')
                handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(handle, 'rb') as file:
                    info = os.fstat(file.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > MAX_RECORD_BYTES:
                        raise ValueError('unsafe pending trace record')
                    entry = json.load(file)
                if entry.get('account') != self.account or entry.get('version') != 1:
                    raise ValueError('pending record account does not match authenticated uploader')
                operation = self.operation(entry['operation_key'], entry['source'])
                for event in entry['events']:
                    self.upload(event, operation)
                path.unlink()
                count += 1
            return count
        finally:
            os.close(fd)
