# claude-code-nap-mode

Go to sleep while [Claude Code](https://claude.com/claude-code) works. Wake up
to a song Claude picked for you, based on the vibe of what it just did.

> Unofficial community project — not affiliated with, endorsed by, or
> supported by Anthropic or Spotify. "Claude Code" and "Spotify" are
> trademarks of their respective owners.

How it works:

1. You turn "nap mode" on (just tell Claude in chat, e.g. "turn nap mode on",
   or `/nap-mode on`).
2. From then on, every time Claude finishes a response **while the session is
   in `auto` permission mode**, a [Stop hook](hooks/nap_hook.py) fires. (It's
   gated on `auto` deliberately — nap mode means you're away from the
   keyboard, so it only makes sense while Claude is actually running
   autonomously rather than sitting on a permission prompt waiting for you.)
3. The hook asks a throwaway headless Claude call to pick one track — by
   number, from a sampled list of your **own Spotify playlists** — that
   matches the mood/energy of what was just done (a hairy bug fix resolved
   might get something tense-then-triumphant, routine cleanup might get
   something chill, shipping a feature might get something upbeat), plus a
   one-sentence justification.
4. The hook plays the track itself via the Spotify Web API, then surfaces a
   clean one-line "now playing: X — reason" message to you through the hook's
   `systemMessage` field — visible in the transcript, without dumping the raw
   picking prompt or track list onto your screen.

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
- Only fires while the session's `permission_mode` is `auto` (checked from the
  hook's stdin payload) — nap mode assumes nobody's around to answer a
  permission prompt, so it stays quiet in every other mode.
- The picking happens via a throwaway headless `claude -p` subprocess, kept
  invisible to your terminal — the raw prompt (instructions + track list)
  never gets printed to your transcript, only the final one-line
  `systemMessage` does. That subprocess is itself a Claude Code invocation
  and would otherwise trigger this same Stop hook recursively when it
  finishes; `NAP_HOOK_ACTIVE=1` is set on its environment and checked at the
  top of the script to short-circuit that.
- The prompt shows the model a **numbered index per track instead of the full
  Spotify URI** (~36 chars of pure overhead with zero vibe signal) — the
  model answers with a number, which gets mapped back to a URI locally. Cuts
  roughly a third of the track-list tokens with no loss in how many tracks
  get considered.
- The hook only shows Claude a **random sample** of up to `NAP_MAX_TRACKS`
  tracks (default 150) out of your full cache, not your whole library — this
  is purely a size limit (a library of thousands of tracks would otherwise
  blow up the prompt on every single response), not a curated shortlist.
  Raise `NAP_MAX_TRACKS` if you want more variety at the cost of a bigger
  prompt.
- The parser takes the **last** standalone-number line in the model's output,
  not the first — small local models occasionally second-guess themselves
  mid-answer ("2... wait, let me reconsider... 19"), and the last number is
  reliably the actual final answer.
- If Spotify isn't open/logged in on your machine, or no cache exists yet, the
  hook just silently no-ops (check `~/.config/claude-code-nap-mode/nap_hook.log`
  for what happened).
- Tokens and your cached library live in `~/.config/claude-code-nap-mode/`, never
  inside the repo — safe to `git clone` this publicly and it stays that way.

## Files

- `nap.py` — CLI: `auth`, `refresh-cache`, `list`, `play <uri>`, `on`, `off`, `status`
- `hooks/nap_hook.py` — the Stop hook: picks (via a hidden headless call), plays, and reports the track
- `commands/nap-mode.md` — the `/nap-mode` slash command definition
- `config.example.json` — template for your local (gitignored) config
