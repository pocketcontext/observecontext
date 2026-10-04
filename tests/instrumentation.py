#!/usr/bin/env python3
"""Packaged subprocess opt-in, transport privacy and result preservation."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from observecontext_client.instrumentation import CONFIG_ENV, instrument_cli


class InstrumentationTests(unittest.TestCase):
    def test_no_opt_in_and_nested_activation(self):
        import urllib.request
        original = urllib.request.OpenerDirector.open
        with patch.dict(os.environ, {}, clear=True), instrument_cli(service='test.client'):
            self.assertIs(urllib.request.OpenerDirector.open, original)
        with tempfile.TemporaryDirectory() as tmp:
            payload = dict(version=1, url='http://127.0.0.1:1', origin=[], service='test.client',
                           output=str(Path(tmp)/'events'), upload=False, spool=None,
                           flush_timeout=1, capture_sql=False, status_file=None)
            with patch.dict(os.environ, {CONFIG_ENV:json.dumps(payload)}):
                with self.assertRaisesRegex(RuntimeError, 'source error'):
                    with instrument_cli(service='test.client'):
                        current = urllib.request.OpenerDirector.open
                        self.assertIsNot(current, original)
                        with instrument_cli(service='test.client'):
                            self.assertIs(urllib.request.OpenerDirector.open, current)
                        raise RuntimeError('source error')
                self.assertIs(urllib.request.OpenerDirector.open, original)
                with instrument_cli(service='observecontext.client'):
                    self.assertIs(urllib.request.OpenerDirector.open, original)

    def test_subprocess_binary_stdout_exit_status_and_activation(self):
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                seen.append(dict(self.headers))
                self.send_response(200); self.end_headers(); self.wfile.write(b'private result')
        with tempfile.TemporaryDirectory() as tmp, http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                url=f'http://127.0.0.1:{server.server_port}'
                output=Path(tmp)/'events'
                code="""import sys,urllib.request
from observecontext_client.instrumentation import instrument_cli
with instrument_cli(service='test.client'):
 with urllib.request.urlopen(sys.argv[1]+'/api/context/schema?secret=value') as response:response.read()
 sys.stdout.buffer.write(b'\\x00\\xffoutput')
 raise SystemExit(7)
"""
                command=[sys.executable,'-m','observecontext_client','capture','--url',url,'--service','test.client','--output',str(output),'--',sys.executable,'-c',code,url]
                env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
                result=subprocess.run(command,env=env,cwd=tmp,capture_output=True)
                self.assertEqual(result.returncode,7,result.stderr)
                self.assertEqual(result.stdout,b'\x00\xffoutput')
                self.assertNotIn(b'no capture was confirmed',result.stderr)
                event=json.loads(output.read_text())
                self.assertEqual(event['route'],'/api/context/schema')
                self.assertNotIn('private result',output.read_text())
                self.assertNotIn('secret',output.read_text())
                self.assertIn('X-Context-Correlation-Id',seen[0])
                command[-2]="import sys;sys.stdout.write('plain')"
                result=subprocess.run(command,env=env,cwd=tmp,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,b'plain')
                self.assertIn(b'no capture was confirmed',result.stderr)
            finally:
                server.shutdown();worker.join()

    def check_termination(self, ignore=False):
        import signal
        import time
        with tempfile.TemporaryDirectory() as tmp:
            ready=Path(tmp)/'ready'
            code="import pathlib,sys,time,signal;"+("signal.signal(signal.SIGTERM,signal.SIG_IGN);" if ignore else "")+"pathlib.Path(sys.argv[1]).touch();time.sleep(60)"
            command=[sys.executable,'-m','observecontext_client','capture','--url','http://127.0.0.1:1',
                     '--service','test.client','--output',str(Path(tmp)/'events'),'--',sys.executable,'-c',code,str(ready)]
            process=subprocess.Popen(command,env={**os.environ,'PYTHONPATH':str(ROOT/'src')},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+10
                while not ready.exists() and time.monotonic()<deadline:
                    time.sleep(.05)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=8)
                self.assertEqual(process.returncode,137 if ignore else 143)
            finally:
                if process.poll() is None:
                    process.kill();process.communicate()

    def test_termination_is_forwarded(self):
        self.check_termination()

    def test_ignored_termination_is_bounded(self):
        self.check_termination(ignore=True)

    def test_bad_configuration_does_not_change_source_result(self):
        with patch.dict(os.environ,{CONFIG_ENV:'invalid'}),contextlib.redirect_stderr(__import__('io').StringIO()):
            with instrument_cli(service='test.client'):
                value=42
        self.assertEqual(value,42)


if __name__=='__main__':unittest.main()
