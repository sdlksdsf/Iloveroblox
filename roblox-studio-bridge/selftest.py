#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
End-to-end self-test. No Roblox, no network, no Windows required.

It starts the real bridge.py against a fake Studio MCP server and a temporary
folder in place of the network, then drives it with the real client.py. If this
passes, the relay logic is sound and the only untested part left is the
Windows/Roblox half, which `bridge.py --doctor` checks on your PC.

    python3 selftest.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          ("  -> " + detail) if detail and not ok else ""))
    return ok


def run_client(tmp, *argv, timeout=90):
    env = dict(os.environ, RELAY_URL="http://127.0.0.1:1/", RELAY_TOKEN="x")
    return subprocess.run(
        [PY, os.path.join(HERE, "client.py"), "--transport", "dir:" + tmp] + list(argv),
        capture_output=True, text=True, timeout=timeout, env=env,
    )


def main():
    tmp = tempfile.mkdtemp(prefix="bridge-selftest-")
    env = dict(os.environ, BRIDGE_MCP_CMD="%s %s" % (PY, os.path.join(HERE, "mock_mcp.py")))
    bridge = subprocess.Popen(
        [PY, os.path.join(HERE, "bridge.py"), "--transport", "dir:" + tmp],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        print("\n[1/6] bridge starts and completes the MCP handshake")
        ready, boot = False, ""
        for _ in range(80):
            time.sleep(0.25)
            boot += (bridge.stdout.readline() or "")
            p = os.path.join(tmp, "status.json")
            if os.path.exists(p):
                try:
                    if json.load(open(p))["state"] == "ready":
                        ready = True
                        break
                except Exception:
                    pass
            if bridge.poll() is not None:
                break
        if not ready:
            try:
                boot += bridge.stdout.read()
            except Exception:
                pass
        check("handshake + status.json", ready, boot[-1200:])

        print("\n[2/6] tool discovery")
        r = run_client(tmp, "tools")
        check("tools/list returns tools", r.returncode == 0 and "echo" in r.stdout, r.stderr[-500:])

        print("\n[3/6] studio_id is injected automatically")
        r = run_client(tmp, "call", "echo", "--args", '{"text":"hi"}')
        ok = r.returncode == 0 and "studio-MOCK01" in r.stdout and '"text": "hi"' in r.stdout
        check("echo with injected studio_id", ok, (r.stdout + r.stderr)[-700:])

        print("\n[4/6] a second tool call round-trips")
        r = run_client(tmp, "call", "get_studio_state")
        check("get_studio_state", r.returncode == 0 and "play_state" in r.stdout, r.stderr[-500:])

        print("\n[5/6] binary payloads survive the relay")
        shot = os.path.join(tmp, "shot.png")
        r = run_client(tmp, "call", "screen_capture", "--save", shot)
        ok = r.returncode == 0 and os.path.exists(shot) and os.path.getsize(shot) > 20
        check("image decoded to PNG", ok, (r.stdout + r.stderr)[-500:])

        print("\n[6/6] errors are reported instead of hanging")
        r = run_client(tmp, "call", "does_not_exist", timeout=40)
        check("unknown tool errors cleanly",
              r.returncode == 1 and "ERROR" in r.stderr, (r.stdout + r.stderr)[-500:])
    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=10)
        except Exception:
            bridge.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 52)
    print(" %d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print(" failed: " + ", ".join(FAIL))
    print("=" * 52 + "\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
