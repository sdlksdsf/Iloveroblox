# Security notes — read before you run anything

You are right to be careful: an agent that can edit your game can also break it. Here is
exactly what this bridge is, what it can and cannot do, and how to check every claim yourself.

## The one-paragraph version

`bridge.py` is a single, readable Python script with **no third-party dependencies**. It starts
Roblox's *own* MCP server using the *exact* command Roblox publishes in its documentation, then
relays JSON between that server and the AI sandbox over HTTPS. It installs nothing, needs no
administrator rights, opens no incoming ports, and never downloads or executes any other file.

## Exactly what touches your PC

| Action | Detail |
|---|---|
| **Files written** | Only `bridge.config.json` (your token) and `bridge.log`-style console output, inside this folder |
| **Files read** | Its own config, Roblox's `mcp.bat`, and the Python standard library |
| **Programs run** | Exactly one: `cmd.exe /c cd /d %LOCALAPPDATA%\Roblox && .\mcp.bat` — Roblox's own launcher |
| **Network, outbound** | `api.github.com` (one small public JSON file, read-only) and the sandbox relay (HTTPS + bearer token) |
| **Network, inbound** | **None.** It never calls `bind()`. Your firewall stays closed. |
| **Registry / services / drivers** | Untouched |
| **Administrator rights** | Not required. Do **not** "Run as administrator" |

## What it can do to your game

Whatever Roblox's MCP server can do — because that is the whole point:

- read and write scripts anywhere in the place
- run Luau in Edit, Client and Server contexts
- start/stop playtests, read the Output window
- save the place (**not** publish — players never see changes until you publish)
- insert assets, generate meshes/animations

So the real question is not "is the script safe" but "do I trust the agent I am talking to".
Treat the bridge like giving someone your Studio keyboard. Only run it in a session you control.

## How to verify the file is what it claims to be

1. **Read it.** It is one file. Search for `subprocess`, `urllib`, `open(`, `os.system`,
   `eval`, `exec`, `base64`, `socket`. Every hit is accounted for below.
2. **Check the hash** and compare it with the one the agent quotes:

   ```
   python -c "import hashlib;print(hashlib.sha256(open('bridge.py','rb').read()).hexdigest())"
   ```

3. **Watch it.** Windows Resource Monitor (`resmon`) → Network → filter `python.exe`. You will
   see only two remote addresses: GitHub and the sandbox host.
4. **Block it.** If you want a hard stop, create an outbound firewall rule blocking `python.exe`.
   The bridge will fail loudly and nothing else on your PC is affected.

## Every sensitive-looking line, explained

| Code | Why it exists |
|---|---|
| `subprocess.Popen(...)` | Starts Roblox's `mcp.bat`. No shell string is built from user input; the command is a fixed list. |
| `urllib.request.urlopen(...)` | The only network code. Two hosts, both HTTPS. |
| `open(...)` / `os.replace(...)` | Reads/writes `bridge.config.json` and `status.json` in this folder only. |
| `base64.b64encode(...)` | Encoding JSON for the relay. Not obfuscation — the data is visible to you in the logs. |
| `threading` | One thread reads Roblox's stdout, one reads stderr, one is the main loop. |
| `secrets.token_urlsafe(32)` | Generates your token with a cryptographically secure RNG. |
| `hmac.compare_digest` | Constant-time token comparison, so timing cannot leak the secret. |

There is no `eval`, no `exec`, no `pickle`, no `socket`, no `ctypes`, no `winreg`, no
`os.system`, no auto-update, no telemetry, no hidden download.

## The token

- Generated **on your PC**, stored **on your PC** (`bridge.config.json`, chmod 600 where the OS
  supports it).
- Sent only to the sandbox relay, over HTTPS, as `Authorization: Bearer …`.
- Never written to GitHub, never committed (`.gitignore` excludes it).
- **Revoking:** delete `bridge.config.json`, run `SETUP.bat` again, paste the new token to the
  agent. The old token is dead immediately.

If you ever suspect the token leaked, do that. It costs ten seconds.

## Threat model — honest assessment

| Threat | Status |
|---|---|
| Random internet user drives your Studio | **Blocked.** They would need the 43-character token *and* the sandbox URL. |
| Someone reads your game code in transit | **Blocked.** HTTPS to the sandbox host. |
| Malicious code hidden in the bridge | **Not possible without your help** — you can read all ~450 lines. |
| A bad AI action damages your place | **Possible.** Use **File → Save to file** before big changes, and keep version history on. |
| Bridge keeps running after you forget it | Close the window. It is a normal process, shown in Task Manager as `python.exe`. |
| Token leaks through chat history | Low risk, but real. Revoke as above if the chat is shared. |

## Uninstall

1. Close the bridge window.
2. Delete this folder.
3. (Optional) Delete `%LOCALAPPDATA%\Roblox\StudioMCP\auth.token` only if you want Roblox's MCP
   server fully reset — you do not need to.

There is no installer, so there is nothing to uninstall. Nothing is left behind outside the
folder, apart from Roblox's own MCP files which Studio manages.
