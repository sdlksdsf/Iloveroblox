#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Client side - runs inside the AI sandbox.

Sends one JSON-RPC request to the relay and prints the answer. Used by the
agent through the shell:

    python3 client.py tools
    python3 client.py status
    python3 client.py call search_game_tree --args '{"path":"Workspace"}'
    python3 client.py call screen_capture --save shot.png

Config comes from relay.json next to this file:
    {"url": "http://127.0.0.1:8787", "token": "rlx_..."}
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
TOOLS_CACHE = os.path.join(CACHE, "tools.json")
SHOTS = os.path.join(CACHE, "shots")


class BridgeError(Exception):
    pass


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------
class HttpCall:
    kind = "http"

    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def _post(self, path, body, timeout):
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise BridgeError("relay said %s: %s" % (exc.code, exc.read()[:200]))
        except Exception as exc:
            raise BridgeError("cannot reach the relay at %s (%r)" % (self.url, exc))

    def _get(self, path, timeout=15):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.url + path)
        req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise BridgeError("relay said %s: %s" % (exc.code, exc.read()[:200]))
        except Exception as exc:
            raise BridgeError("cannot reach the relay at %s (%r)" % (self.url, exc))

    def send(self, method, params, timeout):
        reply = self._post("/call", {"method": method, "params": params, "timeout": timeout},
                           timeout=timeout + 35)
        if not reply.get("ok"):
            err = reply.get("error") or {}
            raise BridgeError(err.get("message") or "bridge returned an error")
        return reply.get("result")

    def status(self):
        return self._get("/status", timeout=10)


class DirCall:
    """Same wire protocol, but through a folder. Offline self-test only."""
    kind = "dir"

    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(os.path.join(self.root, "to-studio"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "to-sandbox"), exist_ok=True)

    def send(self, method, params, timeout):
        rid = uuid.uuid4().hex[:16]
        with open(os.path.join(self.root, "to-studio", rid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"id": rid, "method": method, "params": params or {},
                       "timeout": timeout}, fh)
        deadline = time.time() + timeout + 25
        target = os.path.join(self.root, "to-sandbox", rid + ".json")
        while time.time() < deadline:
            if os.path.exists(target):
                time.sleep(0.05)
                with open(target, encoding="utf-8") as fh:
                    reply = json.load(fh)
                try:
                    os.remove(target)
                    os.remove(os.path.join(self.root, "to-studio", rid + ".json"))
                except OSError:
                    pass
                if not reply.get("ok"):
                    err = reply.get("error") or {}
                    raise BridgeError(err.get("message") or "bridge error")
                return reply.get("result")
            time.sleep(0.15)
        raise BridgeError("no answer from the bridge within %ss" % int(timeout + 25))

    def status(self):
        path = os.path.join(self.root, "status.json")
        if os.path.exists(path):
            return json.load(open(path, encoding="utf-8"))
        return {}


def load_config(path=None):
    for c in [path, os.path.join(HERE, "relay.json")]:
        if c and os.path.exists(c):
            with open(c, "r", encoding="utf-8") as fh:
                return json.load(fh)
    url = os.environ.get("RELAY_URL")
    token = os.environ.get("RELAY_TOKEN")
    if url and token:
        return {"url": url, "token": token}
    raise BridgeError("No relay.json next to client.py, and RELAY_URL/RELAY_TOKEN unset.")


def make_transport(cfg, override=None):
    if override:
        if override.startswith("dir:"):
            return DirCall(override[4:])
        raise BridgeError("Only dir:<folder> is supported for --transport.")
    if not cfg.get("url") or not cfg.get("token"):
        raise BridgeError("relay.json needs 'url' and 'token'.")
    return HttpCall(cfg["url"], cfg["token"])


# --------------------------------------------------------------------------
def load_tools(tr, refresh=False):
    if not refresh and os.path.exists(TOOLS_CACHE):
        try:
            return json.load(open(TOOLS_CACHE, encoding="utf-8"))
        except Exception:
            pass
    result = tr.send("tools/list", {}, 60)
    tools = result.get("tools", [])
    os.makedirs(CACHE, exist_ok=True)
    with open(TOOLS_CACHE, "w", encoding="utf-8") as fh:
        json.dump(tools, fh, indent=1)
    return tools


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj and obj[key]:
            return obj[key]
        for v in obj.values():
            hit = find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_key(v, key)
            if hit is not None:
                return hit
    return None


def result_payloads(result):
    """MCP tool results wrap JSON inside a text block. Unwrap it."""
    out = [result]
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    out.append(json.loads(item.get("text", "")))
                except Exception:
                    pass
    return out


def pick_studio(tr):
    result = tr.send("tools/call", {"name": "list_roblox_studios", "arguments": {}}, 45)
    for payload in result_payloads(result):
        sid = find_key(payload, "studio_id")
        if sid:
            return sid
    raise BridgeError("No Studio instance is connected. Open Roblox Studio with a place loaded.")


def call_tool(tr, tool, arguments=None, timeout=180, studio_id=None):
    arguments = dict(arguments or {})
    tools = load_tools(tr)
    schema = next((t for t in tools if t.get("name") == tool), None)
    if schema is None:
        raise BridgeError("Unknown tool '%s'. Run: python3 client.py tools" % tool)
    props = (schema.get("inputSchema") or {}).get("properties") or {}
    if "studio_id" in props and not arguments.get("studio_id"):
        arguments["studio_id"] = studio_id or pick_studio(tr)
    return tr.send("tools/call", {"name": tool, "arguments": arguments}, timeout)


# --------------------------------------------------------------------------
def render(result, save=None, raw=False):
    if raw:
        print(json.dumps(result, indent=1)[:20000])
        return
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        print(json.dumps(result, indent=1)[:20000])
        return

    os.makedirs(SHOTS, exist_ok=True)
    saved = []
    for item in result["content"]:
        if not isinstance(item, dict):
            print(str(item))
            continue
        if item.get("type") == "text":
            print(item.get("text", ""))
        elif item.get("type") == "image":
            try:
                blob = base64.b64decode(item.get("data") or "")
            except Exception:
                print("[image could not be decoded]")
                continue
            target = save or os.path.join(SHOTS, "shot-%s.png" % time.strftime("%H%M%S"))
            with open(target, "wb") as fh:
                fh.write(blob)
            saved.append(target)
            print("[image saved: %s]" % target)
        else:
            print(json.dumps(item)[:4000])
    if result.get("isError"):
        print("\n[isError=true]")
    for path in saved:
        print("IMAGE_PATH=%s" % path)


# --------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="Drive Roblox Studio through the relay.")
    p.add_argument("command", choices=["tools", "call", "raw", "status", "ping"])
    p.add_argument("target", nargs="?", help="tool name (for call) or method (for raw)")
    p.add_argument("--args", dest="args_json", help='JSON arguments, e.g. \'{"path":"Workspace"}\'')
    p.add_argument("--params", dest="params_json", help="JSON params for raw")
    p.add_argument("--config")
    p.add_argument("--transport", help="dir:<folder> for the offline self-test")
    p.add_argument("--timeout", type=float, default=180)
    p.add_argument("--save", help="where to write a captured image")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--studio", help="explicit studio_id")
    args = p.parse_args(argv)

    try:
        tr = make_transport(load_config(args.config), args.transport)

        if args.command == "ping":
            print(json.dumps(tr.send("ping", {}, 8), indent=1))
            return 0

        if args.command == "status":
            try:
                print(json.dumps(tr.status(), indent=1))
            except Exception as exc:
                print("status unavailable: %s" % exc)
            try:
                pong = tr.send("ping", {}, 8)
                print("BRIDGE ONLINE  %s" % json.dumps(pong))
                tools = load_tools(tr, refresh=True)
                print("TOOLS  %d" % len(tools))
            except BridgeError as exc:
                print("BRIDGE OFFLINE  %s" % exc)
                return 1
            return 0

        if args.command == "tools":
            tools = load_tools(tr, refresh=args.refresh)
            if args.raw:
                print(json.dumps(tools, indent=1))
            else:
                for t in tools:
                    schema = t.get("inputSchema") or {}
                    props = schema.get("properties") or {}
                    req = schema.get("required") or []
                    sig = ", ".join((k if k in req else "[%s]" % k) for k in props)
                    print("%-24s(%s)" % (t.get("name"), sig))
            print("\n%d tools" % len(tools))
            return 0

        if args.command == "call":
            if not args.target:
                raise BridgeError("call needs a tool name.")
            arguments = json.loads(args.args_json) if args.args_json else {}
            result = call_tool(tr, args.target, arguments, timeout=args.timeout,
                               studio_id=args.studio)
            render(result, save=args.save, raw=args.raw)
            return 0

        if args.command == "raw":
            if not args.target:
                raise BridgeError("raw needs a method name.")
            params = json.loads(args.params_json) if args.params_json else {}
            print(json.dumps(tr.send(args.target, params, args.timeout), indent=1)[:20000])
            return 0

    except BridgeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print("ERROR: --args is not valid JSON: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
