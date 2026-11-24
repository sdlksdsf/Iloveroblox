#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Roblox Studio MCP Bridge  -  runs on YOUR PC, next to Roblox Studio.
=====================================================================

WHAT IT DOES (plain English)
  1. Starts Roblox's OWN MCP server, exactly the way Roblox's documentation does:

         cmd.exe /c cd /d %LOCALAPPDATA%\Roblox && .\\mcp.bat

  2. Looks up where the AI sandbox currently is (a small public "beacon" file).
  3. Long-polls that sandbox for work.
  4. Runs each request against your open Roblox Studio session and sends the
     result back.

WHAT IT DOES NOT DO
  - It does not download or run any other program.
  - It does not install anything, edit the registry, or need admin rights.
  - It does not open a listening port on your PC, so your firewall stays shut.
  - It only ever makes OUTBOUND requests to two places:
        api.github.com   (reads one tiny public JSON file)
        the sandbox relay (your game data, over HTTPS, bearer-token protected)
  - It never reads files outside its own folder, except Roblox's own mcp.bat.

One file, ~450 lines, no third-party packages. Read it before you run it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

VERSION = "1.0.0"
USER_AGENT = "roblox-studio-bridge/" + VERSION
MCP_PROTOCOL = "2024-11-05"
CLIENT_INFO = {"name": "arena-roblox-bridge", "version": VERSION}
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "bridge.config.json")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


LOG_FILE = os.path.join(HERE, "bridge.log")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class BridgeError(Exception):
    """Something went wrong that the human needs to read."""


# --------------------------------------------------------------------------
# Transport A: a local folder. Only used by the offline self-test.
# --------------------------------------------------------------------------
class DirTransport:
    kind = "dir"

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def _path(self, rel, name=None):
        p = os.path.join(self.root, *rel.split("/"))
        if name:
            p = os.path.join(p, name)
        return p

    def pull(self):
        d = self._path("to-studio")
        os.makedirs(d, exist_ok=True)
        out = []
        for n in sorted(os.listdir(d)):
            if n.startswith(".") or not n.endswith(".json"):
                continue
            with open(self._path("to-studio", n), "r", encoding="utf-8") as fh:
                req = json.load(fh)
            req["_name"] = n
            out.append(req)
        return out

    def reply(self, rid, payload):
        d = self._path("to-sandbox")
        os.makedirs(d, exist_ok=True)
        with open(self._path("to-sandbox", rid + ".json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def ack(self, rid, name=None):
        try:
            os.remove(self._path("to-studio", name or (rid + ".json")))
        except FileNotFoundError:
            pass

    def beat(self, state, extra=None):
        payload = {"state": state, "ts": now()}
        if extra:
            payload.update(extra)
        d = self._path(".")
        os.makedirs(d, exist_ok=True)
        with open(self._path(".", "status.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def close(self):
        pass


# --------------------------------------------------------------------------
# Transport B: the sandbox relay, over HTTPS.
# --------------------------------------------------------------------------
class HttpTransport:
    kind = "http"

    def __init__(self, base_url, token, timeout=40):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _req(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 401:
                raise BridgeError(
                    "The sandbox rejected this PC's token. Run: python bridge.py --setup"
                )
            raise BridgeError("relay said %s for %s: %s" % (exc.code, path, raw))
        except urllib.error.URLError as exc:
            raise BridgeError("cannot reach the sandbox relay (%s)" % exc.reason)
        except TimeoutError:
            raise BridgeError("the sandbox relay timed out")

    def health(self):
        return self._req("GET", "/health", timeout=10)

    def status(self):
        return self._req("GET", "/status", timeout=10)

    def pull(self):
        return self._req("GET", "/next?wait=20", timeout=32).get("requests", [])

    def reply(self, rid, payload):
        body = dict(payload)
        body["id"] = rid
        self._req("POST", "/reply", body)

    def ack(self, rid, name=None):
        try:
            self._req("POST", "/ack", {"id": rid}, timeout=10)
        except BridgeError:
            pass

    def beat(self, state, extra=None):
        body = {"state": state, "ts": now()}
        if extra:
            body.update(extra)
        try:
            self._req("POST", "/beat", body, timeout=10)
        except BridgeError:
            pass

    def close(self):
        pass


# --------------------------------------------------------------------------
# Roblox Studio MCP process (newline-delimited JSON-RPC over stdin/stdout).
# --------------------------------------------------------------------------
class McpProcess:
    def __init__(self, cmd, cwd=None):
        self.cmd = cmd
        self.cwd = cwd
        self.proc = None
        self._pending = {}
        self._lock = threading.Lock()
        self._next_id = 0
        self._log = []

    def start(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        log("starting Roblox's MCP server: %s" % " ".join(self.cmd))
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        time.sleep(0.7)
        if self.proc.poll() is not None:
            raise BridgeError(
                "Roblox's MCP server exited immediately (code %s). %s"
                % (self.proc.returncode, self.tail() or "It printed no error.")
            )

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self._fail_all("MCP server stopped")

    def _pump_stdout(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    self._log.append("stdout: " + line[:300])  # a banner, not JSON-RPC
                    del self._log[:-30]
                    continue
                mid = msg.get("id")
                if mid is not None:
                    with self._lock:
                        slot = self._pending.get(mid)
                    if slot is not None:
                        slot["msg"] = msg
                        slot["event"].set()
        finally:
            self._fail_all("MCP server closed its output")

    def _pump_stderr(self):
        try:
            for line in self.proc.stderr:
                self._log.append(line.rstrip()[:300])
                del self._log[:-30]
        except Exception:
            pass

    def tail(self):
        return " | ".join(self._log[-5:])

    def _fail_all(self, reason):
        with self._lock:
            slots = list(self._pending.values())
            self._pending.clear()
        for slot in slots:
            slot["error"] = reason
            slot["event"].set()

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=180):
        with self._lock:
            self._next_id += 1
            mid = self._next_id
            slot = {"event": threading.Event(), "msg": None, "error": None}
            self._pending[mid] = slot
        payload = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except Exception as exc:
            with self._lock:
                self._pending.pop(mid, None)
            raise BridgeError("cannot talk to Roblox's MCP server: %r" % exc)

        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(mid, None)
            raise BridgeError(
                "Roblox Studio did not answer within %ss. Is a place open, and is "
                "'Enable Studio as MCP server' switched on?" % int(timeout))
        if slot["error"]:
            raise BridgeError(slot["error"])
        return slot["msg"]

    def initialize(self):
        result = self.request(
            "initialize",
            {"protocolVersion": MCP_PROTOCOL, "capabilities": {}, "clientInfo": CLIENT_INFO},
            timeout=30,
        )
        if "error" in result:
            raise BridgeError("Roblox's MCP server refused the handshake: %s" % result["error"])
        self.notify("notifications/initialized", {})
        return result.get("result", {})

    def tool_names(self):
        result = self.request("tools/list", timeout=30)
        if "error" in result:
            raise BridgeError("tools/list failed: %s" % result["error"])
        return [t.get("name") for t in result.get("result", {}).get("tools", [])]


# --------------------------------------------------------------------------
# Config + beacon
# --------------------------------------------------------------------------
def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        raise BridgeError(
            "No config yet. Run this first:\n\n    python bridge.py --setup\n"
        )
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not cfg.get("token"):
        raise BridgeError("Config has no token. Run: python bridge.py --setup")
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def fetch_beacon(url, timeout=20):
    """Read the tiny public file that says where the sandbox is right now."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BridgeError("beacon returned HTTP %s" % exc.code)
    except Exception as exc:
        raise BridgeError("cannot read the beacon: %r" % exc)
    # If the beacon is served through the GitHub contents API, it is base64.
    if isinstance(data, dict) and data.get("content") and data.get("encoding") == "base64":
        data = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
    if not isinstance(data, dict) or not data.get("relay"):
        raise BridgeError("beacon has no 'relay' field yet - is the sandbox online?")
    return data


def default_mcp_cmd():
    """The exact command Roblox's own documentation publishes for Windows."""
    override = os.environ.get("BRIDGE_MCP_CMD")
    if override:  # used by the offline self-test
        return shlex.split(override)
    local = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.environ.get("USERPROFILE", ""), "AppData", "Local")
    bat = os.path.join(local, "Roblox", "mcp.bat")
    if not os.path.exists(bat):
        raise BridgeError(
            "Roblox's MCP launcher was not found at:\n    %s\n\n"
            "Update Roblox Studio, open it once, then retry. If Studio lives "
            "somewhere unusual, pass --mcp-bat." % bat)
    return ["cmd.exe", "/c", "cd /d %LOCALAPPDATA%\\Roblox && .\\mcp.bat"]


def build_transport(cfg, args):
    if args.transport:
        if args.transport.startswith("dir:"):
            return DirTransport(args.transport[4:])
        raise BridgeError("Only dir:<folder> is supported for --transport.")
    url = getattr(args, "relay_url", None) or cfg.get("relay_url")
    if not url:
        raise BridgeError("No relay URL. Run: python bridge.py --setup")
    return HttpTransport(url, cfg["token"])


# --------------------------------------------------------------------------
def handle_request(mcp, req):
    method = req.get("method", "tools/call")
    params = req.get("params") or {}
    timeout = float(req.get("timeout", 180))

    if method == "ping":
        return {"ok": True, "result": {"pong": True, "ts": now(),
                                       "pid": os.getpid(), "version": VERSION}}

    try:
        reply = mcp.request(method, params, timeout=timeout)
    except BridgeError as exc:
        return {"ok": False, "error": {"code": -32000, "message": str(exc),
                                       "data": {"mcp_log": mcp.tail()}}}
    except Exception as exc:
        return {"ok": False, "error": {"code": -32000, "message": "bridge: %r" % exc}}

    if "error" in reply:
        return {"ok": False, "error": reply["error"]}
    return {"ok": True, "result": reply.get("result")}


# --------------------------------------------------------------------------
def cmd_setup(args):
    print("\n=== Roblox Studio MCP Bridge - one-time setup ===\n")
    cfg = {}
    if os.path.exists(args.config):
        try:
            cfg = json.load(open(args.config, encoding="utf-8"))
        except Exception:
            cfg = {}

    cfg["token"] = cfg.get("token") or "rlx_" + secrets.token_urlsafe(32)
    cfg["beacon_url"] = args.beacon or cfg.get("beacon_url") or DEFAULT_BEACON
    save_config(cfg, args.config)

    print("This PC's secret token (already saved to bridge.config.json):\n")
    print("    " + cfg["token"] + "\n")
    print("Paste that one line to your Arena agent, once. It lets the sandbox")
    print("talk to this PC and nothing else. You can revoke it any time by")
    print("deleting bridge.config.json and running --setup again.\n")
    print("Saved to: %s" % args.config)
    print("Beacon  : %s\n" % cfg["beacon_url"])
    print("Next:    python bridge.py --doctor\n")
    return 0


def cmd_doctor(args):
    print("\n=== Bridge doctor ===\n")
    print("Python      : %s" % sys.version.split()[0])

    try:
        cmd = default_mcp_cmd()
        print("mcp.bat     : found")
    except BridgeError as exc:
        print("mcp.bat     : MISSING\n%s" % exc)
        return 1

    try:
        cfg = load_config(args.config)
        print("Token       : %s..." % cfg["token"][:10])
    except BridgeError as exc:
        print("Token       : not ready -> %s" % exc)
        return 1

    # ---- local half: Roblox Studio itself (works while the sandbox sleeps)
    mcp = McpProcess(cmd)
    names = []
    try:
        mcp.start()
        info = mcp.initialize()
        names = mcp.tool_names()
        print("MCP         : handshake OK (%s)" % (info.get("serverInfo") or "unknown"))
        print("Tools       : %d" % len(names))
        if names:
            print("              " + ", ".join(names[:8]) + ("..." if len(names) > 8 else ""))
    except BridgeError as exc:
        print("\nFAILED -> %s\n" % exc)
        print("Checklist:\n"
              "  1. Roblox Studio is open on the latest version.\n"
              "  2. Assistant > ... > Manage MCP Servers > 'Enable Studio as MCP server' is ON.\n"
              "  3. A place is open in Studio.\n"
              "  4. Claude Desktop / Cursor / VS Code are closed - they can hold the server.\n")
        return 1
    finally:
        mcp.stop()

    # ---- remote half: the sandbox relay --------------------------------
    if args.transport:
        tr = build_transport(cfg, args)
    elif args.relay_url:
        tr = HttpTransport(args.relay_url, cfg["token"])
    else:
        try:
            beacon = fetch_beacon(cfg.get("beacon_url") or DEFAULT_BEACON)
            print("Beacon      : %s" % beacon.get("relay"))
            tr = HttpTransport(beacon["relay"], cfg["token"])
        except BridgeError as exc:
            print("Beacon      : unreachable -> %s" % exc)
            return 1

    print("Relay       : waiting up to 30s for the sandbox ", end="", flush=True)
    up = False
    for _ in range(10):
        try:
            tr.health()
            up = True
            break
        except BridgeError:
            print(".", end="", flush=True)
            time.sleep(3)
    print()

    if not up:
        print("Relay       : ASLEEP  (this is normal, not a fault)")
        print()
        print("  The sandbox only runs while the agent is working on a message.")
        print("  To connect:")
        print("    1. Double-click START_BRIDGE.bat and leave the window open.")
        print("    2. Send any message in Arena.")
        print("    3. The bridge connects within seconds and starts working.")
        print()
        return 0

    try:
        tr.status()
        print("Auth        : OK - the sandbox accepts this PC's token")
    except BridgeError as exc:
        print("Auth        : REJECTED -> %s" % exc)
        print("\nThe sandbox is up but has not been given this PC's token.")
        print("Paste the full token printed by SETUP.bat into the Arena chat.\n")
        return 1

    tr.beat("ready", {"tools": len(names)})
    print("\nAll good. Double-click START_BRIDGE.bat to begin.\n")
    return 0


def cmd_show_token(args):
    cfg = load_config(args.config)
    print("\nYour bridge token:\n")
    print("    " + cfg["token"] + "\n")
    print("Paste that whole line into the Arena chat. It is already saved in")
    print("bridge.config.json - you do not need to type it again.\n")
    return 0


def cmd_run(args):
    cmd = args.mcp_cmd or default_mcp_cmd()

    if args.transport:
        tr = build_transport({}, args)
        print("Relay: local folder %s" % args.transport[4:])
    elif args.relay_url:
        cfg = load_config(args.config)
        tr = HttpTransport(args.relay_url, cfg["token"])
        print("Relay: %s" % args.relay_url)
    else:
        cfg = load_config(args.config)
        cfg.setdefault("beacon_url", args.beacon or DEFAULT_BEACON)
        tr = HttpTransport("http://127.0.0.1:1/", cfg["token"])  # replaced on first beacon

    print("=" * 64)
    print(" Roblox Studio MCP Bridge %s" % VERSION)
    print(" Keep this window open. Ctrl+C stops it.")
    print("=" * 64)

    mcp = McpProcess(cmd)
    answered = set()
    last_note = [""]

    def say(msg):
        if msg != last_note[0]:
            log(msg)
            last_note[0] = msg

    mcp_ready = False
    last_beacon = 0.0
    last_beat = 0.0
    relay_ok = bool(args.transport or args.relay_url)  # folder or fixed URL is always "online"

    try:
        while True:
            # ---- keep the MCP server alive --------------------------------
            if mcp.proc is None or mcp.proc.poll() is not None:
                try:
                    mcp.start()
                    info = mcp.initialize()
                    names = mcp.tool_names()
                    mcp_ready = True
                    log("Roblox MCP ready: %s (%d tools)" % (
                        info.get("serverInfo") or "unknown", len(names)))
                except BridgeError as exc:
                    mcp_ready = False
                    log("Roblox MCP not available: %s" % exc)
                    log("Check: Studio open, 'Enable Studio as MCP server' ON.")
                    time.sleep(5)
                    continue

            # ---- find the sandbox -----------------------------------------
            if not args.transport and not args.relay_url:
                if time.time() - last_beacon > 120 or not relay_ok:
                    last_beacon = time.time()
                    try:
                        cfg = load_config(args.config)
                        beacon = fetch_beacon(cfg.get("beacon_url") or DEFAULT_BEACON)
                        url = beacon["relay"]
                        if tr.base.rstrip("/") != url.rstrip("/") or not relay_ok:
                            tr = HttpTransport(url, cfg["token"])
                            log("sandbox relay: %s" % url)
                            relay_ok = True
                    except BridgeError as exc:
                        if relay_ok:
                            log("lost the sandbox: %s" % exc)
                        relay_ok = False
                        time.sleep(5)   # do not hammer the beacon API

            # ---- pull work -------------------------------------------------
            batch = []
            if relay_ok:
                try:
                    batch = tr.pull()
                except BridgeError as exc:
                    relay_ok = False
                    say("sandbox relay is asleep - waiting for a message in Arena "
                        "(this is normal between messages)")
                    time.sleep(4)
                    continue

            if not batch:
                if relay_ok and time.time() - last_beat > 30:
                    last_beat = time.time()
                    tr.beat("ready" if mcp_ready else "no-studio")
                time.sleep(0.2)
                continue

            for req in batch:
                rid = req.get("id")
                name = req.get("_name")
                if rid in answered:
                    tr.ack(rid, name)
                    continue
                label = (req.get("params") or {}).get("name") or req.get("method")
                log(">> %s" % label)
                reply = handle_request(mcp, req)
                reply["id"] = rid
                reply["ts"] = now()
                try:
                    tr.reply(rid, reply)   # answer first...
                    tr.ack(rid, name)       # ...then forget the request
                except BridgeError as exc:
                    log("could not deliver the answer: %s" % exc)
                    relay_ok = False
                    break
                answered.add(rid)
                if len(answered) > 4000:
                    answered = set(list(answered)[-2000:])
                log("<< %s %s" % (label, "ok" if reply.get("ok") else "ERROR"))
    except KeyboardInterrupt:
        log("stopped (Ctrl+C)")
    except BridgeError as exc:
        log("FATAL: %s" % exc)
        return 1
    finally:
        try:
            mcp.stop()
        except Exception:
            pass
    return 0


DEFAULT_BEACON = (
    "https://api.github.com/repos/sdlksdsf/Iloveroblox/contents/_relay/session.json"
    "?ref=arena/79418886-iloveroblox"
)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Relay Roblox Studio's MCP server to the AI sandbox.")
    p.add_argument("--setup", action="store_true", help="one-time setup: create a token")
    p.add_argument("--show-token", action="store_true", help="print the saved token")
    p.add_argument("--doctor", action="store_true", help="check everything, list tools")
    p.add_argument("--run", action="store_true", help="start relaying (default)")
    p.add_argument("--config", default=CONFIG_PATH)
    p.add_argument("--transport", help="dir:<folder> (offline self-test only)")
    p.add_argument("--mcp-bat", dest="mcp_cmd", help="path to mcp.bat if not default")
    p.add_argument("--beacon", help="override the beacon URL")
    p.add_argument("--relay-url", dest="relay_url", help="skip the beacon, use this relay")
    args = p.parse_args(argv)

    if args.mcp_cmd:
        args.mcp_cmd = ["cmd.exe", "/c", args.mcp_cmd]

    try:
        if args.setup:
            return cmd_setup(args)
        if args.show_token:
            return cmd_show_token(args)
        if args.doctor:
            return cmd_doctor(args)
        return cmd_run(args)
    except BridgeError as exc:
        print("\nERROR: %s\n" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
