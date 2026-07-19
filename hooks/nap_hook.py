#!/usr/bin/env python3
"""
Claude Code "Stop" hook for claude-code-nap-mode.

Fires after every response Claude gives. If nap mode is on, it asks a
throwaway headless Claude instance to pick one song (by vibes) from your
cached Spotify library that matches the mood of what was just done, then
plays it via nap.py.

Register this as a Stop hook in ~/.claude/settings.json (see README.md).
Runs `claude -p ...` for the mood judgment. NAP_HOOK_ACTIVE=1 is set on that
subprocess's environment and checked at the top of this file, so if the
headless call's own Stop hook fires this script again, it no-ops immediately
instead of recursing. (--bare would also skip hook loading, but it turns out
to skip loading stored credentials too, breaking auth, so it's not used here.)
"""
import json
import os
import random
import re
import subprocess
import sys
import time

NAP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAP_PY = os.path.join(NAP_DIR, "nap.py")

CONFIG_DIR = os.path.expanduser(
    os.environ.get("NAP_MODE_CONFIG_DIR", "~/.config/claude-code-nap-mode")
)
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")
CACHE_PATH = os.path.join(CONFIG_DIR, "library_cache.json")
LOG_PATH = os.path.join(CONFIG_DIR, "nap_hook.log")

MAX_TRACKS_IN_PROMPT = int(os.environ.get("NAP_MAX_TRACKS", "200"))
MAX_MESSAGE_CHARS = 4000


def log(msg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def main():
    # Belt-and-suspenders: if we're somehow already inside a nap-hook-spawned
    # invocation, bail immediately instead of risking recursion.
    if os.environ.get("NAP_HOOK_ACTIVE") == "1":
        return

    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        log(f"failed to parse stdin: {e}")
        return

    state = load_json(STATE_PATH, {"enabled": False})
    if not state.get("enabled"):
        return

    cache = load_json(CACHE_PATH)
    if not cache or not cache.get("tracks"):
        log("nap mode on but no library cache yet (run: nap.py refresh-cache)")
        return

    tracks = cache["tracks"]
    if len(tracks) > MAX_TRACKS_IN_PROMPT:
        tracks = random.sample(tracks, MAX_TRACKS_IN_PROMPT)

    track_lines = "\n".join(
        f"{t['uri']}\t{t['artist']} - {t['name']} [{t['playlist']}]" for t in tracks
    )
    valid_uris = {t["uri"] for t in tracks}

    last_message = (payload.get("last_assistant_message") or "")[:MAX_MESSAGE_CHARS]
    if not last_message.strip():
        last_message = "(no text response, likely a quick tool-only action)"

    prompt = f"""You are picking background music for a coder who just woke up (or is about to nap)
while an AI assistant worked. Here is what the assistant just finished doing:
---
{last_message}
---
Pick exactly ONE song from the list below that matches the mood/energy of that work
(e.g. a gnarly bug fix might call for something tense or triumphant once solved;
routine cleanup might call for something chill; a big shipped feature might call for
something upbeat). Use vibes, not genre rules.

Reply with ONLY the Spotify track URI on a single line. No other text.

{track_lines}"""

    try:
        env = dict(os.environ)
        env["NAP_HOOK_ACTIVE"] = "1"
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        output = result.stdout.strip()
    except Exception as e:
        log(f"headless claude call failed: {e}")
        return

    match = re.search(r"spotify:track:[A-Za-z0-9]+", output)
    if not match or match.group(0) not in valid_uris:
        log(f"no valid track uri in model output: {output!r}")
        return

    uri = match.group(0)
    track = next((t for t in cache["tracks"] if t["uri"] == uri), None)
    label = f"{track['artist']} - {track['name']}" if track else uri
    log(f"picked: {label}")

    try:
        subprocess.run(
            ["python3", NAP_PY, "play", uri],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        log(f"play failed: {e}")


if __name__ == "__main__":
    main()
