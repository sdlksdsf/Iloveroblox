# Iloveroblox

Game development workspace. Two parts:

## `roblox-studio-bridge/` — AI ↔ Roblox Studio connection

Lets the Arena agent work inside your open Roblox Studio session: read the game tree, write
Luau, run code, press Play, read Output, take screenshots, save the place.

- **Start here:** [`roblox-studio-bridge/README.md`](roblox-studio-bridge/README.md)
- **Safety:** [`roblox-studio-bridge/SECURITY.md`](roblox-studio-bridge/SECURITY.md)
- One-time setup: double-click `SETUP.bat`. Every session after that: double-click
  `START_BRIDGE.bat`.

## `_relay/session.json`

Tiny public pointer that tells the bridge where the sandbox is this session. Updated
automatically; do not edit by hand.

## Games

Game projects land here as they are built.
