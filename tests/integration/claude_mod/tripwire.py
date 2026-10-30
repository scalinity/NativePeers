"""Local model-request tripwire for zero-API Claude Code harness runs.

Every request is answered HTTP 400 and counted. Only the method, the path,
the body length and the body's top-level `model` field are recorded; headers
(which carry credentials) never are.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BODY = json.dumps({'type': 'error', 'error': {'type': 'invalid_request_error',
                   'message': 'NativePeers tripwire: model requests are refused in this harness'}}).encode()


class Tripwire:
    def __init__(self, log: Path | None = None, port: int = 0):
        self.hits = []
        self.lock = threading.Lock()
        self.log = log
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                try:model = json.loads(body).get('model')
                except (ValueError, AttributeError):model = None
                with owner.lock:
                    owner.hits.append({'method': self.command, 'path': self.path.split('?')[0], 'bytes': length, 'model': model})
                    if owner.log:
                        with owner.log.open('a') as stream:stream.write(json.dumps(owner.hits[-1]) + '\n')
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(BODY)))
                self.end_headers()
                self.wfile.write(BODY)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = answer

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.port = self.server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def count(self):
        with self.lock:return len(self.hits)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    with Tripwire(args.log, args.port) as wire:
        print(json.dumps({'ready': True, 'url': wire.url}), flush=True)
        try:wire.thread.join()
        except KeyboardInterrupt:pass


if __name__ == '__main__':
    main()
