#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A stand-in for Roblox's StudioMCP, used only by selftest.py.

It speaks the same newline-delimited JSON-RPC over stdin/stdout, implements the
MCP handshake, and exposes four fake tools. Nothing here touches Roblox.
"""
import base64
import json
import sys
import time

# a valid 1x1 transparent PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

TOOLS = [
    {
        "name": "echo",
        "description": "Returns the arguments it was given.",
        "inputSchema": {
            "type": "object",
            "properties": {"studio_id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["studio_id"],
        },
    },
    {
        "name": "list_roblox_studios",
        "description": "Fake Studio list.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_studio_state",
        "description": "Fake state.",
        "inputSchema": {
            "type": "object",
            "properties": {"studio_id": {"type": "string"}},
            "required": ["studio_id"],
        },
    },
    {
        "name": "screen_capture",
        "description": "Fake screenshot.",
        "inputSchema": {
            "type": "object",
            "properties": {"studio_id": {"type": "string"}},
            "required": ["studio_id"],
        },
    },
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def fail(mid, code, message):
    send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def handle(mid, method, params):
    if method == "initialize":
        send({
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-studio-mcp", "version": "0.0.1"},
            },
        })
        return

    if method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps({"echoed": args})}],
                "isError": False}})
        elif name == "list_roblox_studios":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps({"studios": [
                    {"studio_id": "studio-MOCK01", "name": "Mock Place", "place_id": 1}]})}]}})
        elif name == "get_studio_state":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(
                    {"studio_id": args.get("studio_id"), "play_state": "Edit"})}]}})
        elif name == "screen_capture":
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [
                {"type": "text", "text": "viewport"},
                {"type": "image", "data": base64.b64encode(PNG).decode(), "mimeType": "image/png"},
            ]}})
        else:
            fail(mid, -32601, "unknown tool: %s" % name)
        return

    fail(mid, -32601, "unknown method: %s" % method)


def main():
    sys.stderr.write("mock-mcp: ready\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method", "")
        sys.stderr.write("mock-mcp: %s\n" % method)
        sys.stderr.flush()
        if mid is None:          # notification, e.g. notifications/initialized
            continue
        if method == "tools/call" and (msg.get("params") or {}).get("name") == "slow":
            time.sleep(5)
        handle(mid, method, msg.get("params") or {})
    sys.stderr.write("mock-mcp: stdin closed, exiting\n")


if __name__ == "__main__":
    main()
