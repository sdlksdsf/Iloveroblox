#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publish the sandbox relay URL to the beacon file, then push it.

Runs inside the sandbox. The beacon is the only thing the bridge on your PC
reads from GitHub: a tiny public JSON file that says where the sandbox is.

    python3 publish.py --port 8787
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BEACON = os.path.join(REPO, "_relay", "session.json")


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO, check=check,
                          capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("RELAY_PORT", "8787")))
    ap.add_argument("--url", help="override the relay URL")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    sandbox = os.environ.get("E2B_SANDBOX_ID", "")
    url = args.url or ("https://%d-%s.e2b.app" % (args.port, sandbox) if sandbox else "")
    if not url:
        print("Could not work out the public URL. Pass --url.", file=sys.stderr)
        return 1

    payload = {
        "relay": url,
        "port": args.port,
        "sandbox": sandbox,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    os.makedirs(os.path.dirname(BEACON), exist_ok=True)
    with open(BEACON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print("beacon: %s" % json.dumps(payload))

    if args.no_push:
        return 0

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git("add", "_relay/session.json")
    commit = git("commit", "-m", "relay: publish sandbox beacon", check=False)
    if commit.returncode != 0:
        print("nothing to commit (%s)" % commit.stderr.strip().splitlines()[-1:] or "")
        return 0
    push = git("push", "origin", branch, check=False)
    if push.returncode != 0:
        print("push failed: %s" % push.stderr.strip(), file=sys.stderr)
        return 1
    print("pushed %s to %s" % ("_relay/session.json", branch))
    return 0


if __name__ == "__main__":
    sys.exit(main())
