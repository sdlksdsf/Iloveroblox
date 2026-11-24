# Roblox Studio ↔ Arena Agent Bridge

Lets the AI agent work inside **your** Roblox Studio session: read the game tree, write Luau,
press Play, read the Output window, take screenshots, and save the place.

It talks through a **private GitHub repo** — the one channel both your PC and the sandbox can
reach with no tunnel, no port forwarding, no firewall change and no browser token.

```
  YOU                    YOUR PC (Windows)                    ARENA SANDBOX (Linux)
   |                            |                                    |
   | "make a lobby system"      |                                    |
   |--------------------------->|                                    |
   |                            |  bridge.py                         |
   |                            |    |-- starts Roblox's own server   |
   |                            |    |     %LOCALAPPDATA%\Roblox       |
   |                            |    |     \mcp.bat  (official)        |
   |                            |    |                                  |
   |                            |    |-- polls your private repo ----->|
   |                            |    |                                  |
   |                            |    |<-- "run this Luau" --------------|
   |                            |    |                                  |
   |                            |    '--> Roblox Studio                 |  agent writes
   |                            |         (your open place)             |  code, tests,
   |                            |<-- result / screenshot --------------|  iterates
   |<---------------------------|                                    |
   | "done, tested in Play mode" |                                    |
```

Nothing is installed on your PC. No firewall holes. No admin rights. One Python file you can
read in ten minutes.

---

## 1. What you need

| Requirement | How to check |
|---|---|
| Windows 10 or 11 | You are reading this on it |
| **Latest** Roblox Studio | Studio → File → "Check for updates" |
| Python 3.9+ | Command Prompt → `python --version` |
| A GitHub account | You already have one |

If `python --version` says *not recognized*, install from
<https://www.python.org/downloads/> and **tick "Add python.exe to PATH"** on the first screen.
That checkbox is the only part people get wrong.

---

## 2. One-time setup (about 6 minutes)

### Step 1 — Create a private relay repo (1 minute)

1. Go to <https://github.com/new>
2. **Repository name:** `roblox-studio-bridge`
3. **Visibility:** select **Private** ← important, this carries your game code
4. Tick **"Add a README file"**
5. Click **Create repository**

### Step 2 — Create a token that can only touch that repo (2 minutes)

1. Go to <https://github.com/settings/personal-access-tokens/new>
2. **Token name:** `roblox bridge`
3. **Expiration:** 90 days (or No expiration if you prefer)
4. **Repository access** → **Only select repositories** → pick `roblox-studio-bridge`
5. **Permissions** → **Repository permissions** → **Contents** → set to **Read and write**
   (leave everything else as *No access*)
6. **Generate token** → copy it. It starts with `github_pat_…`

This token can only read/write that one empty repo. It cannot touch your other repos, your
code, or your account.

### Step 3 — Get the bridge onto your PC

- **Git:** `git clone https://github.com/<you>/Iloveroblox.git`
- **No git:** **Code → Download ZIP**, unzip to somewhere permanent like `C:\RobloxBridge`

> The double-clickable files are at the **top level** and also inside `roblox-studio-bridge`.
> Either copy works.

### Step 4 — Turn on Roblox's MCP server (inside Studio)

1. Open Roblox Studio and open **any place** (a new baseplate is fine)
2. Click **Assistant** (top right, next to the avatar icon)
3. Click **⋯** → **Manage MCP Servers**
4. Switch on **Enable Studio as MCP server**
5. Keep that panel open — you will use it to confirm the connection

### Step 5 — Run SETUP.bat

Double-click **`SETUP.bat`**. It asks four questions:

```
GitHub username:                       your-username
Private relay repo name [...]:         roblox-studio-bridge
Branch [main]:                         main
Paste the token (typing is hidden):    github_pat_...
```

Then it verifies everything and prints:

```
  OK - token works, repo is private
  OK - read/write verified with a scratch file
Saved to: C:\RobloxBridge\...\bridge.config.json

Now paste this token to your Arena agent, once:

    github_pat_xxxxxxxxxxxxxxxxxxxx
```

### Step 6 — Paste that token into the Arena chat

One line, once. The agent stores it and both sides are paired permanently.

---

## 3. Every session (30 seconds, no setup)

1. Open **Roblox Studio** with the place you want to work on.
2. Double-click **`START_BRIDGE.bat`**. Leave that window open.
3. In Arena, say: *"Studio is open, go"*.

The bridge window shows:

```
[14:02:11] Roblox MCP ready: StudioMCP 1.x (30 tools)
[14:02:13] >> search_game_tree
[14:02:16] << search_game_tree ok
```

And Studio shows a **green dot** with "1 connected" under Manage MCP Servers.

The bridge keeps running while you are away. It only acts when the agent sends work.

---

## 4. What the agent can do

| Area | Tools |
|---|---|
| **Scripts** | `script_read`, `multi_edit`, `script_search`, `script_grep` |
| **Run code** | `execute_luau` (Edit / Client / Server) |
| **Explore** | `search_game_tree`, `inspect_instance`, `subagent` |
| **Playtest** | `start_stop_play`, `get_studio_state`, `get_console_output` |
| **See it** | `screen_capture` (stills or video during a playtest) |
| **Drive the player** | `character_navigation`, `user_keyboard_input`, `user_mouse_input` |
| **Assets** | `search_asset`, `insert_asset`, `generate_mesh`, `generate_animation`, `generate_avatar`, `generate_procedural_model`, `generate_material`, `upload_image`, `store_image` |
| **Place** | `save_place`, `list_roblox_studios` |
| **Docs** | `http_get`, `skill` |

Things to ask for:

- *"Look at my Workspace and tell me what's badly organised."*
- *"Write a server script for a coin pickup system with a leaderstats currency."*
- *"Press Play, walk the character to the coin, and tell me if the Output shows errors."*
- *"Take a screenshot of the spawn area."*
- *"Save the place with a note saying 'added coin system'."*

---

## 5. Troubleshooting

| What you see | Why | Fix |
|---|---|---|
| `Python is not recognized` | Python missing or not on PATH | Reinstall from python.org, **tick "Add to PATH"** |
| `mcp.bat: MISSING` | Studio outdated or installed elsewhere | Update Studio; or `--mcp-bat "C:\...\mcp.bat"` |
| `MCP server exited immediately` | Studio not running / MCP switch off | Open Studio, turn on **Enable Studio as MCP server**, retry |
| `GitHub rejected the token (401)` | Token wrong, expired, or revoked | Create a new token, run `SETUP.bat` again |
| `could not read repository` (404) | Token not granted access to that repo | Re-create the token and select **that exact repo** |
| `repo is PUBLIC` warning | You skipped Private | Repo → Settings → Danger Zone → Change visibility |
| `wrote a test file but could not list it` | Token is read-only | Set **Contents = Read and write** |
| `GitHub rate limit hit` | Too many requests | It resumes on its own after a minute |
| Studio shows no green dot | Bridge window closed | Keep `START_BRIDGE.bat` open |
| `Roblox Studio did not answer within 180s` | No place open, or another client holds the server | Open a place; close Claude Desktop / Cursor / VS Code |
| `401 Unauthorized` in Studio logs (port 13469) | Token file in another app's private folder | Copy it to `%LOCALAPPDATA%\Roblox\StudioMCP\auth.token` |

Still stuck? Run `python bridge.py --doctor` and paste the whole output to the agent.

### Expected speed

Each tool call takes roughly **2–5 seconds**: the request travels to GitHub and back several
times. That is slower than a local client (which is near-instant), but a single agent step
already takes several seconds of thinking, so it is not the bottleneck. The `ping` command
prints your real round-trip time.

---

## 6. Safety — read this once

Full detail in **[SECURITY.md](SECURITY.md)**. The short version:

- `bridge.py` is **one readable Python file**, no third-party packages. It downloads nothing,
  installs nothing, needs no admin, and opens **no** listening port.
- It makes outbound HTTPS requests to **one place only**: `api.github.com`.
- Your token lives in `bridge.config.json` (gitignored, never uploaded).
- The bridge only ever executes Roblox's own `mcp.bat`. It cannot run arbitrary commands.
- The relay repo must be **private** — it carries your game's scripts and screenshots.
- To stop: close the window. To remove: delete the folder and delete the token on GitHub.
- Verify the file you run is the file you read:
  `python -c "import hashlib;print(hashlib.sha256(open('bridge.py','rb').read()).hexdigest())"`

---

## 7. Updating

```
git pull
```

…then double-click `START_BRIDGE.bat`. Your `bridge.config.json` is untouched.

---

## Files

| File | Runs on | Purpose |
|---|---|---|
| `SETUP.bat` | Your PC | **Start here.** One-time repo + token check |
| `START_BRIDGE.bat` | Your PC | **Double-click every session** |
| `SHOW_TOKEN.bat` | Your PC | Prints your saved token if you need to re-paste it |
| `bridge.py` | Your PC | Starts Roblox's MCP server, relays requests |
| `client.py` | Sandbox | The agent's command-line client |
| `selftest.py` | Either | Offline end-to-end test (no Roblox, no GitHub) |
| `mock_mcp.py` | Either | Fake Studio server used by the self-test |
| `SECURITY.md` | — | Plain-English audit of every line |
