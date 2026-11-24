#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Relay - runs inside the AI sandbox. Nothing here touches your PC.

The bridge on your PC connects to this relay over HTTPS (through the sandbox's
public preview URL) and long-polls for work. The agent talks to it on
127.0.0.1, which never leaves the sandbox.

Endpoints
  GET  /health     no auth   liveness
  GET  /status     bearer    queue depth, last bridge contact
  GET  /next       bearer    long-poll, returns pending requests
  POST /reply      bearer    bridge hands back one result
  POST /ack        bearer    bridge confirms it stored the result
  POST /call       loopback  agent submits one JSON-RPC request and waits

Run:  python3 relay.py --token <secret> [--port 8787]
"""
from __future__ import annotations

import argparse
import collections
import hmac
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY = 64 * 1024 * 1024
REDELIVER_AFTER = 240.0


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


class State:
    def __init__(self):
        self.cv = threading.Condition()
        self.queue = collections.deque()
        self.inflight = {}
        self.replies = {}
        self.served = 0
        self.bridge_seen = 0.0
        self.started = time.time()

    def sweep(self):
        """Put back anything the bridge pulled but never acknowledged."""
        while True:
            time.sleep(15)
            now = time.time()
            with self.cv:
                stale = [k for k, (req, ts) in self.inflight.items()
                         if now - ts > REDELIVER_AFTER]
                for k in stale:
                    req, _ts = self.inflight.pop(k)
                    self.queue.append(req)
                if stale:
                    self.cv.notify_all()


STATE = State()
TOKENS = set()
TOKEN_FILE = None


def load_tokens():
    """Supports several tokens, one per line, so a new PC can be authorised
    without restarting the relay."""
    global TOKENS
    if TOKEN_FILE and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            found = {ln.strip() for ln in fh
                     if ln.strip() and not ln.strip().startswith("#")}
        if found:
            TOKENS = found


def authorized(header):
    if not header:
        return False
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    presented = parts[1].strip()
    for token in TOKENS:
        try:
            if hmac.compare_digest(presented, token):
                return True
        except TypeError:
            continue
    return False


def token_reloader():
    while True:
        time.sleep(10)
        load_tokens()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "roblox-relay/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---- helpers ---------------------------------------------------------
    def _send(self, code, obj=None, raw=None):
        body = raw if raw is not None else json.dumps(obj or {}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _loopback(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "localhost")

    # ---- routes ----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            return self._send(200, {"ok": True, "ts": time.time(),
                                    "uptime": round(time.time() - STATE.started, 1)})

        if not authorized(self.headers.get("Authorization")):
            return self._send(401, {"error": "bad token"})

        if path == "/status":
            with STATE.cv:
                return self._send(200, {
                    "pending": len(STATE.queue),
                    "inflight": len(STATE.inflight),
                    "served": STATE.served,
                    "bridge_seen": STATE.bridge_seen,
                    "bridge_age_s": round(time.time() - STATE.bridge_seen, 1) if STATE.bridge_seen else None,
                    "uptime": round(time.time() - STATE.started, 1),
                })

        if path == "/next":
            qs = parse_qs(parsed.query)
            try:
                wait = max(0.0, min(float(qs.get("wait", ["20"])[0]), 25.0))
            except ValueError:
                wait = 20.0
            deadline = time.time() + wait
            batch = []
            with STATE.cv:
                while not STATE.queue and time.time() < deadline:
                    STATE.cv.wait(timeout=max(0.05, deadline - time.time()))
                now = time.time()
                while STATE.queue:
                    req = STATE.queue.popleft()
                    STATE.inflight[req["id"]] = (req, now)
                    batch.append(req)
                STATE.bridge_seen = now
            return self._send(200, {"requests": batch})

        return self._send(404, {"error": "unknown path"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            body = self._body()
        except Exception as exc:
            return self._send(400, {"error": "bad body: %s" % exc})

        if path == "/call":
            if not self._loopback():
                return self._send(403, {"error": "loopback only"})
            return self._call(body)

        if not authorized(self.headers.get("Authorization")):
            return self._send(401, {"error": "bad token"})

        if path == "/reply":
            rid = body.get("id")
            if not rid:
                return self._send(400, {"error": "missing id"})
            with STATE.cv:
                STATE.replies[rid] = body
                STATE.inflight.pop(rid, None)
                STATE.served += 1
                STATE.bridge_seen = time.time()
                STATE.cv.notify_all()
            return self._send(200, {"ok": True})

        if path == "/ack":
            rid = body.get("id")
            with STATE.cv:
                STATE.inflight.pop(rid, None)
            return self._send(200, {"ok": True})

        if path == "/beat":
            with STATE.cv:
                STATE.bridge_seen = time.time()
            return self._send(200, {"ok": True})

        return self._send(404, {"error": "unknown path"})

    def _call(self, body):
        rid = body.get("id") or uuid.uuid4().hex[:16]
        req = {
            "id": rid,
            "method": body.get("method", "tools/call"),
            "params": body.get("params") or {},
            "timeout": float(body.get("timeout", 180)),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        wait_for = float(body.get("timeout", 180)) + 30
        with STATE.cv:
            STATE.queue.append(req)
            STATE.cv.notify_all()

        deadline = time.time() + wait_for
        with STATE.cv:
            while rid not in STATE.replies:
                left = deadline - time.time()
                if left <= 0:
                    return self._send(504, {"error": "bridge did not answer in time",
                                            "id": rid})
                STATE.cv.wait(timeout=min(1.0, left))
            reply = STATE.replies.pop(rid)
        return self._send(200, reply)


def main():
    global TOKEN_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", help="shared secret (default: relay.token file)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RELAY_PORT", "8787")))
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    TOKEN_FILE = os.environ.get("RELAY_TOKEN_FILE") or os.path.join(here, "relay.token")
    if args.token:
        TOKENS.add(args.token)
    load_tokens()
    if os.environ.get("RELAY_TOKEN"):
        TOKENS.add(os.environ["RELAY_TOKEN"])
    if not TOKENS:
        print("No token. Create one with:\n"
              "  python3 -c \"import secrets;print('rlx_'+secrets.token_urlsafe(24))\" > relay.token",
              file=sys.stderr)
        return 1

    threading.Thread(target=STATE.sweep, daemon=True).start()
    threading.Thread(target=token_reloader, daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    log("relay listening on %s:%d  (%d token(s) authorised)" % (args.host, args.port, len(TOKENS)))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("relay stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
