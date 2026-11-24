#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Client side - runs inside the AI sandbox.

Writes one request into the relay repo, waits for the bridge on your PC to
answer, and prints the result.

    python3 client.py tools
    python3 client.py status
    python3 client.py call search_game_tree --args '{"path":"Workspace"}'
    python3 client.py call screen_capture --save shot.png

Config comes from relay.json next to this file:
    {"owner": "you", "repo": "roblox-studio-bridge", "branch": "main", "token": "ghp_..."}
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge import (  # noqa: E402
    BridgeError, DirTransport, GitHubTransport, TO_SANDBOX, TO_STUDIO,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
TOOLS_CACHE = os.path.join(CACHE, "tools.json")
SHOTS = os.path.join(CACHE, "shots")


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------
class RepoCall:
    """Queue over the private GitHub repo (the real transport)."""

    kind = "github"

    def __init__(self, cfg):
        self.tr = GitHubTransport(cfg["owner"], cfg["repo"], cfg["token"],
                                  cfg.get("branch", "main"))

    def send(self, method, params, timeout):
        rid = uuid.uuid4().hex[:16]
        name = rid + ".json"
        self.tr.write(TO_STUDIO, name, {
            "id": rid, "method": method, "params": params or {}, "timeout": timeout,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        deadline = time.time() + timeout + 25
        while time.time() < deadline:
            reply = self.tr.read_optional(TO_SANDBOX, name)
            if reply is not None:
                self.tr.delete(TO_SANDBOX, name)
                if not reply.get("ok"):
                    err = reply.get("error") or {}
                    raise BridgeError(err.get("message") or "bridge returned an error")
                return reply.get("result")
            time.sleep(0.35)
        raise BridgeError(
            "No answer from the bridge after %ss.\n"
            "Is START_BRIDGE.bat open in a window on the PC running Studio?"
            % int(timeout + 25))

    def status(self):
        try:
            return self.tr.read("_queue", "status.json")
        except BridgeError:
            return {}


class DirCall:
    """Same wire protocol, but through a folder. Offline self-test only."""

    kind = "dir"

    def __init__(self, root):
        self.tr = DirTransport(root)

    def send(self, method, params, timeout):
        rid = uuid.uuid4().hex[:16]
        name = rid + ".json"
        self.tr.write(TO_STUDIO, name, {
            "id": rid, "method": method, "params": params or {}, "timeout": timeout,
        })
        deadline = time.time() + timeout + 25
        while time.time() < deadline:
            for item in self.tr.list(TO_SANDBOX):
                if item["name"] != name:
                    continue
                reply = self.tr.read(TO_SANDBOX, name)
                self.tr.delete(TO_SANDBOX, name, item.get("sha"))
                if not reply.get("ok"):
                    err = reply.get("error") or {}
                    raise BridgeError(err.get("message") or "bridge error")
                return reply.get("result")
            time.sleep(0.15)
        raise BridgeError("no answer from the bridge within %ss" % int(timeout + 25))

    def status(self):
        try:
            return self.tr.read("_queue", "status.json")
        except BridgeError:
            return {}


def load_config(path=None):
    for c in [path, os.path.join(HERE, "relay.json")]:
        if c and os.path.exists(c):
            with open(c, "r", encoding="utf-8") as fh:
                return json.load(fh)
    return {}


def make_transport(cfg, override=None):
    if override:
        if override.startswith("dir:"):
            return DirCall(override[4:])
        raise BridgeError("Only dir:<folder> is supported for --transport.")
    for key in ("owner", "repo", "token"):
        if not cfg.get(key):
            raise BridgeError("relay.json is missing '%s'." % key)
    return RepoCall(cfg)


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
            print(json.dumps(tr.status(), indent=1))
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
