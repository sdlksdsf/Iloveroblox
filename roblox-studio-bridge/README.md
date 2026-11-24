# Roblox Studio ↔ Arena Agent Bridge

This folder is the missing link between your Roblox Studio and the AI agent in the Arena
sandbox. Once it is running, you type what you want in Arena, and the agent reads your game's
tree, writes Luau scripts, presses Play, reads the Output window, takes screenshots and fixes
what it sees — inside **your** Studio, on **your** PC.

```
   YOU                         YOUR PC (Windows)                    ARENA SANDBOX (Linux)
   |                                  |                                    |
   |  "make a lobby system"           |                                    |
   |-------------------------------->|                                    |
   |                                  |  bridge.py                         |
   |                                  |    |-- starts Roblox's own server   |
   |                                  |    |     %LOCALAPPDATA%\Roblox       |
   |                                  |    |     \mcp.bat  (official)        |
   |                                  |    |                                  |
   |                                  |    |-- long-polls over HTTPS ------> relay.py
   |                                  |    |                                  |   |
   |                                  |    |<-- "run this Luau" --------------|   |
   |                                  |    |                                  |   |
   |                                  |    '--> Roblox Studio                 |  agent
   |                                  |         (your open place)             |  writes code,
   |                                  |<-- result / screenshot --------------|  tests, iterates
   |<------------------------------- |                                    |
   |  "done, tested in Play mode"     |                                    |
```

Nothing is installed on your PC. No firewall holes. No admin rights. One Python file you can
read in ten minutes.

---

## 1. What you need (5 minutes, once)

| Requirement | How to check |
|---|---|
| Windows 10 or 11 | You are reading this on it |
| **Latest** Roblox Studio | Studio → File → "Check for updates" |
| Python 3.9+ | Open **Command Prompt**, type `python --version` |
| A GitHub account | You already have one |
| Internet | Obviously |

If `python --version` says *not recognized*, install Python from
<https://www.python.org/downloads/> and **tick "Add python.exe to PATH"** on the first screen.
That checkbox is the only part people get wrong.

---

## 2. One-time setup (about 5 minutes)

### Step 1 — Get this folder onto your PC

Pick **one**:

- **Git:** `git clone https://github.com/<you>/Iloveroblox.git` then open the
  `roblox-studio-bridge` folder.
- **No git:** on GitHub press **Code → Download ZIP**, unzip, open
  `roblox-studio-bridge`.

> Keep the folder somewhere permanent like `C:\RobloxBridge`. Do **not** run it from inside
> the ZIP preview or the Downloads temp folder.

### Step 2 — Turn on Roblox's MCP server (inside Studio)

1. Open Roblox Studio and open **any place** (a new baseplate is fine).
2. Click **Assistant** (top right, next to the avatar icon).
3. Click **⋯** → **Manage MCP Servers**.
4. Switch on **Enable Studio as MCP server**.
5. Leave that panel open — you will use it to confirm the connection later.

### Step 3 — Create your secret token

Double-click **`SETUP.bat`**.

It prints something like:

```
This PC's secret token (already saved to bridge.config.json):

    rlx_Kj8s2...long string...

Paste that one line to your Arena agent, once.
```

Copy that **whole line** and paste it into the Arena chat. That is the only secret in the
system: it lets the sandbox talk to *this PC* and nothing else. It never goes on GitHub.

Then the same window runs a health check and should end with:

```
mcp.bat     : found
Beacon      : https://8787-xxxxxxxx.e2b.app
Relay       : up (12.4)
MCP         : handshake OK (StudioMCP 1.x)
Tools       : 30
All good. Double-click START_BRIDGE.bat to begin.
```

If it says `Beacon: unreachable`, that is normal **if the sandbox is not running yet**.
Start the sandbox first, then re-run `SETUP.bat`.

### Step 4 — Tell the agent

Say: *"bridge is set up, here is my token: `rlx_...`"*. The agent stores it and starts the
relay. From then on the token never changes and you never repeat this step.

---

## 3. Every session (30 seconds, no setup)

1. Open **Roblox Studio** with the place you want to work on.
2. Double-click **`START_BRIDGE.bat`**. Leave that black window open — it is the bridge.
3. In Arena, say: *"Studio is open, go"*.

You should see in the bridge window:

```
[14:02:11] Roblox MCP ready: StudioMCP 1.x (30 tools)
[14:02:11] sandbox relay: https://8787-....e2b.app
[14:02:12] >> search_game_tree
[14:02:12] << search_game_tree ok
```

And in Studio, under **Manage MCP Servers**, a **green dot** with "1 connected".

That is the whole ritual. Open Studio, double-click, say go.

---

## 4. What the agent can do in your Studio

Roblox's server exposes these tools (the agent calls them through the bridge):

| Area | Tools |
|---|---|
| **Scripts** | `script_read`, `multi_edit`, `script_search`, `script_grep` |
| **Run code** | `execute_luau` (Edit / Client / Server datamodel) |
| **Explore** | `search_game_tree`, `inspect_instance`, `subagent` |
| **Playtest** | `start_stop_play`, `get_studio_state`, `get_console_output` |
| **See it** | `screen_capture` (stills or video during a playtest) |
| **Drive the player** | `character_navigation`, `user_keyboard_input`, `user_mouse_input` |
| **Assets** | `search_asset`, `insert_asset`, `generate_mesh`, `generate_animation`, `generate_avatar`, `generate_procedural_model`, `generate_material`, `upload_image`, `store_image` |
| **Place** | `save_place`, `list_roblox_studios` |
| **Docs** | `http_get`, `skill` |

Practical examples of what to ask for:

- *"Look at my Workspace and tell me what is badly organised."*
- *"Write a server script for a coin pickup system with a leaderstats currency."*
- *"Press Play, walk the character to the coin, and tell me if the Output shows errors."*
- *"Take a screenshot of the spawn area."*
- *"Save the place with a note saying 'added coin system'."*

---

## 5. Troubleshooting

| What you see | Why | Fix |
|---|---|---|
| `Python is not recognized` | Python missing or not on PATH | Reinstall from python.org, **tick "Add to PATH"** |
| `mcp.bat: MISSING` | Studio outdated, or installed elsewhere | Update Studio; or run `python bridge.py --doctor --mcp-bat "C:\...\mcp.bat"` |
| `MCP server exited immediately` | Studio not running / MCP switch off | Open Studio, turn on **Enable Studio as MCP server**, retry |
| `Roblox Studio did not answer within 180s` | No place open, or another client holds the server | Open a place; close Claude Desktop / Cursor / VS Code |
| `Beacon: unreachable` | Sandbox is not running | Start a session in Arena, then re-run `SETUP.bat` |
| `Relay: DOWN` | Sandbox relay not started | Ask the agent to start the relay |
| `401 bad token` | Token mismatch | Re-run `SETUP.bat`, paste the new token to the agent |
| Nothing happens, no green dot in Studio | Bridge window closed | Keep `START_BRIDGE.bat` open the whole session |
| Studio says "1 connected" but agent times out | Two Studio windows | Close the extra one; the bridge picks the first |
| `401 Unauthorized` in Studio logs (port 13469) | Token file in another app's private folder (Claude Desktop / MSIX) | Copy the token to `%LOCALAPPDATA%\Roblox\StudioMCP\auth.token` |

Still stuck? Run `python bridge.py --doctor` and paste the whole output to the agent.

---

## 6. Safety — read this once

Full detail is in **[SECURITY.md](SECURITY.md)**. The short version:

- `bridge.py` is **one readable Python file**, ~450 lines, **zero dependencies**. It downloads
  nothing, installs nothing, needs no admin, and opens **no** listening port.
- Outbound traffic goes to exactly two places: `api.github.com` (to read a tiny public file
  saying where the sandbox is) and the sandbox relay (your game data, over HTTPS, guarded by
  your token).
- Your token lives in `bridge.config.json`, which is gitignored and never uploaded.
- The bridge only ever executes Roblox's own `mcp.bat`. It cannot run arbitrary commands.
- To stop everything: close the bridge window. To remove it: delete the folder.
- Verify the file you run is the file you read:
  `python -c "import hashlib;print(hashlib.sha256(open('bridge.py','rb').read()).hexdigest())"`

**Do not** paste `bridge.config.json` into chat, Discord, or a public repo. Anyone holding that
token could send instructions to your Studio.

---

## 7. Updating

The scripts live in this repo. When the agent improves them it will say so; then:

```
git pull
```

…and double-click `START_BRIDGE.bat`. Your token in `bridge.config.json` is untouched.

---

## Files

| File | Runs on | Purpose |
|---|---|---|
| `bridge.py` | Your PC | Starts Roblox's MCP server, relays requests |
| `START_BRIDGE.bat` | Your PC | Double-click to start the bridge |
| `SETUP.bat` | Your PC | One-time token + health check |
| `relay.py` | Sandbox | Long-poll relay the bridge connects to |
| `client.py` | Sandbox | The agent's command-line client |
| `selftest.py` | Either | Offline end-to-end test (no Roblox needed) |
| `mock_mcp.py` | Either | Fake Studio server used by the self-test |
| `SECURITY.md` | — | Plain-English audit of every line |
