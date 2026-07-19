# claude-code-nap-mode

Go to sleep while [Claude Code](https://claude.com/claude-code) works. Wake up
to a song Claude picked for you, based on the vibe of what it just did.

> Unofficial community project — not affiliated with, endorsed by, or
> supported by Anthropic or Spotify. "Claude Code" and "Spotify" are
> trademarks of their respective owners.

How it works:

1. You turn "nap mode" on (just tell Claude in chat, e.g. "turn nap mode on").
2. From then on, every time Claude finishes a response, a
   [Stop hook](hooks/nap_hook.py) fires.
3. If nap mode is on, the hook spins up a tiny throwaway headless Claude call,
   hands it a list of songs pulled from **your own Spotify playlists**, and
   asks it to pick one track that matches the mood/energy of what was just
   done — a hairy bug fix might get something tense-then-triumphant, routine
   cleanup might get something chill, shipping a feature might get something
   upbeat.
4. That track gets played on your computer via Spotify Connect.

Nothing here is specific to one person's account — this repo only ever reads
your *own* playlists and plays to *your* own Spotify, using your own API
credentials that you create in the next section.

## Setup

### 1. Create a Spotify app (free, ~2 minutes)

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   and log in with your normal Spotify account.
2. Click **Create app**. Name/description can be anything.
3. Under **Redirect URIs**, add exactly: `http://127.0.0.1:8888/callback`
4. Save. Open the app's **Settings** and copy the **Client ID** (you do *not*
   need the Client Secret — this uses the PKCE flow, which doesn't need one).

### 2. Configure this repo

```bash
cp config.example.json config.json
```

This `config.json` is only a *template location* — actual runtime config
lives outside the repo, in `~/.config/claude-code-nap-mode/`, so there's nothing
personal to accidentally commit. Instead, run:

```bash
mkdir -p ~/.config/claude-code-nap-mode
cp config.example.json ~/.config/claude-code-nap-mode/config.json
```

Edit `~/.config/claude-code-nap-mode/config.json` and paste in your Client ID.

### 3. Log in once

```bash
python3 nap.py auth
```

This opens your browser for a one-time Spotify login/consent, then saves a
token locally (auto-refreshed after that — you won't need to do this again
unless you revoke access).

### 4. Cache your playlists

```bash
python3 nap.py refresh-cache
```

Pulls every track from every playlist **you personally created** (not ones
you just follow) into a local cache. Re-run this whenever your playlists
change meaningfully. Takes a few seconds to a minute depending on library size.

### 5. Wire up the Stop hook

Add this to your `~/.claude/settings.json` (merge with whatever's already
there — don't overwrite the file):

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/claude-code-nap-mode/hooks/nap_hook.py"
          }
        ]
      }
    ]
  }
}
```

Replace `/absolute/path/to/claude-code-nap-mode` with wherever you cloned this
repo. This hook is a no-op (exits instantly) whenever nap mode is off, so it's
safe to leave registered permanently.

### 6. Install the slash command

```bash
mkdir -p ~/.claude/commands
cp commands/nap-mode.md ~/.claude/commands/nap-mode.md
```

This makes `/nap-mode` available in every Claude Code project. If you cloned
this repo somewhere other than `~/claude-code-nap-mode`, edit the path inside
`~/.claude/commands/nap-mode.md` to match.

### 7. Use it

```
/nap-mode on
/nap-mode off
/nap-mode status
```

Or just ask in chat — "turn nap mode on" works too, Claude will run the same
`nap.py on` / `nap.py off` underneath.

While it's on, every response Claude finishes may queue up a song. Go to sleep.

## Notes / limitations

- **macOS only** for actual playback (`open -a Spotify` wakes a local Spotify
  Connect device). The Web API calls themselves are portable if you want to
  adapt device-handling for another OS.
- Only considers playlists **you own**, not ones you follow — this was a
  deliberate choice, edit the `owner.id == my_id` filter in `nap.py` if you
  want to include followed playlists too.
- The mood judgment costs one small extra headless Claude call per response
  while nap mode is on. It runs with `claude --bare`, which skips loading
  hooks/project context for that sub-call, both to keep it cheap and to avoid
  the hook recursively triggering itself.
- If Spotify isn't open/logged in on your machine, or no cache exists yet, the
  hook just silently no-ops (check `~/.config/claude-code-nap-mode/nap_hook.log`
  for what happened).
- Tokens and your cached library live in `~/.config/claude-code-nap-mode/`, never
  inside the repo — safe to `git clone` this publicly and it stays that way.

## Files

- `nap.py` — CLI: `auth`, `refresh-cache`, `list`, `play <uri>`, `on`, `off`, `status`
- `hooks/nap_hook.py` — the Stop hook that does the mood-picking + playback
- `commands/nap-mode.md` — the `/nap-mode` slash command definition
- `config.example.json` — template for your local (gitignored) config
