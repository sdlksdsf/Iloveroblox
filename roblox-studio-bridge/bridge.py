#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Roblox Studio MCP Bridge  -  runs on YOUR PC, next to Roblox Studio.
=====================================================================

WHAT IT DOES (plain English)
  1. Starts Roblox's OWN MCP server, exactly the way Roblox's documentation does:

         cmd.exe /c cd /d %LOCALAPPDATA%\Roblox && .\mcp.bat

  2. Watches a PRIVATE GitHub repo that you own for requests.
  3. Runs each request against your open Roblox Studio session.
  4. Writes the result back to that same private repo.

Why a GitHub repo? Because it is the one place both your PC and the AI sandbox
can reach without any tunnel, port forwarding, firewall change or browser
token. It also keeps working while the sandbox is asleep, so the bridge never
shows a scary error when the agent is idle.

WHAT IT DOES NOT DO
  - It does not download or run any other program.
  - It does not install anything, edit the registry, or need admin rights.
  - It does not open a listening port on your PC, so your firewall stays shut.
  - It only ever makes OUTBOUND HTTPS requests to api.github.com.
  - It never reads files outside its own folder, except Roblox's own mcp.bat.

One file, no third-party packages. Read it before you run it.
"""
from __future__ import annotations

import argparse
import base64
import getpass
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

VERSION = "2.0.0"
USER_AGENT = "roblox-studio-bridge/" + VERSION
API_VERSION = "2022-11-28"
MCP_PROTOCOL = "2024-11-05"
CLIENT_INFO = {"name": "arena-roblox-bridge", "version": VERSION}
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "bridge.config.json")
LOG_FILE = os.path.join(HERE, "bridge.log")

TO_STUDIO = "_queue/to-studio"
TO_SANDBOX = "_queue/to-sandbox"
STATUS = "_queue/status.json"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


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

    def list(self, rel):
        d = self._path(rel)
        os.makedirs(d, exist_ok=True)
        out = []
        for n in sorted(os.listdir(d)):
            if n.startswith(".") or not n.endswith(".json"):
                continue
            out.append({"name": n, "sha": "0" * 7})
        return out

    def read(self, rel, name):
        with open(self._path(rel, name), "r", encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, rel, name, obj):
        os.makedirs(self._path(rel), exist_ok=True)
        tmp = self._path(rel, "." + name + ".tmp")
        final = self._path(rel, name)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        os.replace(tmp, final)

    def read_optional(self, rel, name):
        try:
            return self.read(rel, name)
        except FileNotFoundError:
            return None

    def delete(self, rel, name, sha=None):
        try:
            os.remove(self._path(rel, name))
        except FileNotFoundError:
            pass

    def close(self):
        pass


# --------------------------------------------------------------------------
# Transport B: your private GitHub repo, through the REST API.
#
# One message = one small JSON file. The producer writes it, the consumer
# deletes it after it has safely stored the reply. Every message has a unique
# id, so a retry can never run the same Studio command twice.
# --------------------------------------------------------------------------
class GitHubTransport:
    kind = "github"

    def __init__(self, owner, repo, token, branch="main", api="https://api.github.com"):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.branch = branch or "main"
        self.api = api.rstrip("/")
        self._etags = {}
        self._cache = {}
        self._lock = threading.Lock()

    # ---- low level -------------------------------------------------------
    def _request(self, method, path, body=None, etag_key=None, ok_codes=(200, 201, 204)):
        url = self.api + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        req.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        key = etag_key or (method + " " + path)
        etag = self._etags.get(key)
        if etag and method == "GET":
            req.add_header("If-None-Match", etag)
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
                new_etag = resp.headers.get("ETag")
                if new_etag:
                    self._etags[key] = new_etag
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return 304, None
            raw = exc.read()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"message": raw.decode("utf-8", "replace")}
            message = payload.get("message", "")
            if exc.code == 401:
                raise BridgeError(
                    "GitHub rejected the token (401). Re-run SETUP.bat and paste a "
                    "fresh token.") from exc
            if exc.code == 403 and "rate limit" in message.lower():
                raise BridgeError(
                    "GitHub rate limit hit. Wait a minute and the bridge resumes "
                    "automatically.") from exc
            if exc.code == 404:
                return 404, payload
            if exc.code in ok_codes:
                return exc.code, payload
            raise BridgeError("GitHub said %s for %s %s: %s"
                              % (exc.code, method, path, message)) from exc
        except urllib.error.URLError as exc:
            raise BridgeError(
                "Could not reach api.github.com (%s). Check your internet "
                "connection, VPN or firewall." % exc.reason) from exc
        except TimeoutError:
            raise BridgeError("GitHub did not answer in time.")

    # ---- transport interface --------------------------------------------
    def list(self, rel):
        status, payload = self._request(
            "GET",
            "/repos/%s/%s/contents/%s?ref=%s"
            % (self.owner, self.repo, rel, self.branch),
            etag_key="list " + rel,
        )
        if status == 304:
            return self._cache.get("list " + rel, [])
        if status == 404:
            return []
        if isinstance(payload, dict):
            payload = [payload]
        out = []
        for item in payload or []:
            name = item.get("name", "")
            if name.startswith(".") or not name.endswith(".json"):
                continue
            out.append({"name": name, "sha": item.get("sha", "")})
        self._cache["list " + rel] = out
        return out

    def read(self, rel, name):
        key = "read " + rel + "/" + name
        status, payload = self._request(
            "GET",
            "/repos/%s/%s/contents/%s/%s?ref=%s"
            % (self.owner, self.repo, rel, name, self.branch),
            etag_key=key,
        )
        if status == 304 and key in self._cache:
            return self._cache[key]
        if status == 404:
            raise BridgeError("message %s disappeared before it could be read" % name)
        content = payload.get("content", "")
        if payload.get("encoding") == "base64":
            content = base64.b64decode(content).decode("utf-8")
        obj = json.loads(content)
        self._cache[key] = obj
        return obj

    def read_optional(self, rel, name):
        """Same as read(), but returns None instead of raising when absent."""
        try:
            return self.read(rel, name)
        except BridgeError as exc:
            if "disappeared" in str(exc):
                return None
            raise

    def write(self, rel, name, obj):
        with self._lock:
            path = "/repos/%s/%s/contents/%s/%s" % (self.owner, self.repo, rel, name)
            body = {
                "message": "bridge: " + name,
                "branch": self.branch,
                "content": base64.b64encode(
                    json.dumps(obj, ensure_ascii=False).encode("utf-8")
                ).decode("ascii"),
            }
            status, payload = self._request("PUT", path, body,
                                           ok_codes=(200, 201, 409, 422))
            # GitHub wants the file's current sha when it already exists, and
            # 409 means someone changed it underneath us. Retry with the
            # fresh sha a couple of times before giving up.
            for _attempt in range(3):
                if status in (200, 201):
                    break
                st, meta = self._request(
                    "GET", path + "?ref=" + self.branch, etag_key=None)
                if st != 200 or not isinstance(meta, dict) or not meta.get("sha"):
                    break
                body["sha"] = meta["sha"]
                status, payload = self._request("PUT", path, body,
                                                ok_codes=(200, 201, 409, 422))
            if status not in (200, 201):
                detail = payload.get("message") if isinstance(payload, dict) else payload
                raise BridgeError("could not write %s/%s: %s" % (rel, name, detail))
            self._etags.pop("list " + rel, None)
            self._etags.pop("read " + rel + "/" + name, None)
            self._cache.pop("list " + rel, None)
            self._cache.pop("read " + rel + "/" + name, None)

    def delete(self, rel, name, sha=None):
        if not sha:
            st, meta = self._request(
                "GET",
                "/repos/%s/%s/contents/%s/%s?ref=%s"
                % (self.owner, self.repo, rel, name, self.branch),
                etag_key=None,
            )
            if st != 200 or not isinstance(meta, dict):
                return
            sha = meta.get("sha")
        body = {"message": "bridge: ack " + name, "sha": sha, "branch": self.branch}
        st, _ = self._request(
            "DELETE",
            "/repos/%s/%s/contents/%s/%s" % (self.owner, self.repo, rel, name),
            body,
            ok_codes=(200, 204),
        )
        if st == 404:
            return
        self._etags.pop("list " + rel, None)
        self._etags.pop("read " + rel + "/" + name, None)
        self._cache.pop("list " + rel, None)
        self._cache.pop("read " + rel + "/" + name, None)

    def probe(self):
        """Return repo metadata, or raise a clear error."""
        st, meta = self._request(
            "GET", "/repos/%s/%s" % (self.owner, self.repo), etag_key=None)
        if st != 200 or not isinstance(meta, dict):
            raise BridgeError("could not read repository %s/%s" % (self.owner, self.repo))
        return meta

    def roundtrip(self):
        """Write, list, read and delete a scratch file. Proves read+write."""
        name = "probe-%s.json" % secrets.token_hex(4)
        payload = {"ok": True, "ts": now(), "note": "write test"}
        self.write("_queue", name, payload)
        names = [i["name"] for i in self.list("_queue")]
        if name not in names:
            raise BridgeError("wrote a test file but could not list it - the token "
                              "may be read-only")
        back = self.read("_queue", name)
        self.delete("_queue", name)
        if back.get("ok") is not True:
            raise BridgeError("test file came back corrupted")
        return True

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
                % (self.proc.returncode, self.tail() or "It printed no error."))

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
                    self._log.append("stdout: " + line[:300])
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
# Config
# --------------------------------------------------------------------------
def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        raise BridgeError("No config yet. Run this first:\n\n    python bridge.py --setup\n")
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    for key in ("owner", "repo", "token"):
        if not cfg.get(key):
            raise BridgeError("Config is missing '%s'. Run: python bridge.py --setup" % key)
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


def build_transport(cfg, args):
    if args.transport:
        if args.transport.startswith("dir:"):
            return DirTransport(args.transport[4:])
        raise BridgeError("Only dir:<folder> is supported for --transport.")
    return GitHubTransport(cfg["owner"], cfg["repo"], cfg["token"], cfg.get("branch", "main"))


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
    return ["cmd.exe", "/c", r"cd /d %LOCALAPPDATA%\Roblox && .\mcp.bat"]


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


def write_status(tr, state, extra=None):
    payload = {"state": state, "ts": now(), "pid": os.getpid(), "version": VERSION}
    if extra:
        payload.update(extra)
    try:
        tr.write("_queue", "status.json", payload)
    except Exception as exc:
        log("could not write status.json: %s" % exc)


# --------------------------------------------------------------------------
def cmd_setup(args):
    print("\n=== Roblox Studio MCP Bridge - one-time setup ===\n")
    cfg = {}
    if os.path.exists(args.config):
        try:
            cfg = json.load(open(args.config, encoding="utf-8"))
        except Exception:
            cfg = {}

    owner = input("GitHub username: ").strip() or cfg.get("owner", "")
    if not owner:
        raise BridgeError("A username is required.")
    repo = input("Private relay repo name [roblox-studio-bridge]: ").strip() or "roblox-studio-bridge"
    branch = input("Branch [main]: ").strip() or "main"

    print("\nThe token must be a FINE-GRAINED personal access token:\n"
          "  Repository access : Only select repositories -> %s\n"
          "  Permissions       : Contents = Read and write    (nothing else)\n"
          "Create it here: https://github.com/settings/personal-access-tokens/new\n" % repo)
    token = getpass.getpass("Paste the token (typing is hidden): ").strip()
    if not token:
        raise BridgeError("A token is required.")

    cfg.update({"owner": owner, "repo": repo, "branch": branch, "token": token})
    print("\nChecking GitHub access...")
    tr = GitHubTransport(owner, repo, token, branch)
    meta = tr.probe()
    if meta.get("private") is False:
        print("  WARNING: %s/%s is PUBLIC. Your game code will be readable by anyone."
              % (owner, repo))
        if input("  Type 'yes' to continue anyway: ").strip().lower() != "yes":
            raise BridgeError("Cancelled - make the repo private and run --setup again.")
    print("  OK - token works, repo is %s" % ("private" if meta.get("private") else "public"))

    tr.roundtrip()
    print("  OK - read/write verified with a scratch file")

    save_config(cfg, args.config)
    print("\nSaved to: %s\n" % args.config)
    print("Now paste this token to your Arena agent, once:\n")
    print("    " + token + "\n")
    print("Next:  python bridge.py --doctor\n")
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
        print("Config      : %s/%s (branch %s)" % (cfg["owner"], cfg["repo"],
                                                   cfg.get("branch", "main")))
    except BridgeError as exc:
        print("Config      : not ready -> %s" % exc)
        return 1

    if args.transport:
        tr = build_transport(cfg, args)
    else:
        tr = GitHubTransport(cfg["owner"], cfg["repo"], cfg["token"], cfg.get("branch", "main"))

    if tr.kind == "github":
        try:
            meta = tr.probe()
            print("GitHub      : OK (%s)" % ("private" if meta.get("private") else "PUBLIC"))
            tr.roundtrip()
            print("Queue       : read/write verified")
        except BridgeError as exc:
            print("GitHub      : FAILED -> %s" % exc)
            print("\nFix: create a fine-grained token with Contents = Read and write\n"
                  "on this exact repository, then re-run SETUP.bat.\n")
            return 1

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

    write_status(tr, "ready", {"tools": len(names)})
    print("\nAll good. Double-click START_BRIDGE.bat to begin.\n")
    return 0


def cmd_show_token(args):
    cfg = load_config(args.config)
    print("\nYour GitHub token:\n")
    print("    " + cfg["token"] + "\n")
    print("Paste that whole line into the Arena chat, once.\n")
    return 0


def cmd_run(args):
    if args.transport:
        tr = build_transport({}, args)
        print("Relay: local folder %s" % args.transport[4:])
    else:
        cfg = load_config(args.config)
        tr = build_transport(cfg, args)
        print("Relay: %s/%s (branch %s)" % (cfg["owner"], cfg["repo"], cfg.get("branch", "main")))

    print("=" * 64)
    print(" Roblox Studio MCP Bridge %s" % VERSION)
    print(" Keep this window open. Ctrl+C stops it.")
    print("=" * 64)

    mcp = McpProcess(args.mcp_cmd or default_mcp_cmd())
    answered = set()
    mcp_ready = False
    last_heartbeat = 0.0
    last_note = [""]

    def say(msg):
        if msg != last_note[0]:
            log(msg)
            last_note[0] = msg

    try:
        while True:
            if mcp.proc is None or mcp.proc.poll() is not None:
                try:
                    mcp.start()
                    info = mcp.initialize()
                    names = mcp.tool_names()
                    mcp_ready = True
                    log("Roblox MCP ready: %s (%d tools)"
                        % (info.get("serverInfo") or "unknown", len(names)))
                    write_status(tr, "ready", {"tools": len(names)})
                except BridgeError as exc:
                    mcp_ready = False
                    say("Roblox MCP not available: %s" % exc)
                    time.sleep(5)
                    continue

            try:
                items = tr.list(TO_STUDIO)
            except BridgeError as exc:
                say("GitHub problem: %s" % exc)
                time.sleep(10)
                continue
            last_note[0] = ""

            for item in items:
                name = item["name"]
                try:
                    req = tr.read(TO_STUDIO, name)
                except BridgeError as exc:
                    log("could not read %s: %s" % (name, exc))
                    continue

                rid = req.get("id", name[:-5])
                if rid in answered:
                    tr.delete(TO_STUDIO, name, item.get("sha"))
                    continue

                label = (req.get("params") or {}).get("name") or req.get("method")
                log(">> %s" % label)
                reply = handle_request(mcp, req)
                reply["id"] = rid
                reply["ts"] = now()

                # Answer first, then forget the request: a crash can never lose
                # a result Studio already produced.
                tr.write(TO_SANDBOX, name, reply)
                tr.delete(TO_STUDIO, name, item.get("sha"))
                answered.add(rid)
                if len(answered) > 4000:
                    answered = set(list(answered)[-2000:])
                log("<< %s %s" % (label, "ok" if reply.get("ok") else "ERROR"))

            if not items:
                if time.time() - last_heartbeat > 60:
                    last_heartbeat = time.time()
                    write_status(tr, "ready" if mcp_ready else "no-studio")
                time.sleep(args.poll)
    except KeyboardInterrupt:
        log("stopped (Ctrl+C)")
    except BridgeError as exc:
        log("FATAL: %s" % exc)
        return 1
    finally:
        try:
            write_status(tr, "offline")
        except Exception:
            pass
        try:
            mcp.stop()
        except Exception:
            pass
        tr.close()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Relay Roblox Studio's MCP server through your private GitHub repo.")
    p.add_argument("--setup", action="store_true", help="one-time setup: create a token")
    p.add_argument("--doctor", action="store_true", help="check everything, list tools")
    p.add_argument("--show-token", action="store_true", help="print the saved token")
    p.add_argument("--run", action="store_true", help="start relaying (default)")
    p.add_argument("--config", default=CONFIG_PATH)
    p.add_argument("--transport", help="dir:<folder> (offline self-test only)")
    p.add_argument("--mcp-bat", dest="mcp_cmd", help="path to mcp.bat if not default")
    p.add_argument("--poll", type=float, default=0.6, help="seconds between GitHub checks")
    args = p.parse_args(argv)

    if args.mcp_cmd:
        args.mcp_cmd = ["cmd.exe", "/c", args.mcp_cmd]

    try:
        if args.setup:
            return cmd_setup(args)
        if args.doctor:
            return cmd_doctor(args)
        if args.show_token:
            return cmd_show_token(args)
        return cmd_run(args)
    except BridgeError as exc:
        print("\nERROR: %s\n" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
