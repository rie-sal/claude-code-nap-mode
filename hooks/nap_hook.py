#!/usr/bin/env python3
"""
Claude Code "Stop" hook for claude-code-nap-mode.

Fires after every response Claude gives. If nap mode is on, instead of doing
the picking itself, it blocks the stop and hands Claude a reason: pick a
song from the cached library that matches what was just done, play it, and
say why in one line. That makes the pick + justification show up as a real
part of the visible conversation, using Claude's actual context of the task
(not a re-summarized message fed to an isolated subprocess).

Register this as a Stop hook in ~/.claude/settings.json (see README.md).

Recursion guard: blocking a Stop hook makes Claude Code mark the resulting
continuation with stop_hook_active=true on its own Stop event. We check that
and just let it stop instead of blocking again, so this fires once per real
response, not forever.
"""
import json
import os
import random
import sys

NAP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAP_PY = os.path.join(NAP_DIR, "nap.py")

CONFIG_DIR = os.path.expanduser(
    os.environ.get("NAP_MODE_CONFIG_DIR", "~/.config/claude-code-nap-mode")
)
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")
CACHE_PATH = os.path.join(CONFIG_DIR, "library_cache.json")
LOG_PATH = os.path.join(CONFIG_DIR, "nap_hook.log")

MAX_TRACKS_IN_PROMPT = int(os.environ.get("NAP_MAX_TRACKS", "50"))


def log(msg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            import time
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
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        log(f"failed to parse stdin: {e}")
        return

    if payload.get("stop_hook_active"):
        return  # this Stop event is our own forced continuation finishing; don't re-block

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

    reason = f"""nap mode is on. Before doing anything else, pick ONE song from the list below \
that matches the mood/energy of what you just finished doing in your last response \
(use vibes, not genre rules — a hairy bug fix resolved might call for something tense-then-\
triumphant, routine cleanup might call for something chill, shipping a feature might call for \
something upbeat). Then:

1. Run `python3 {NAP_PY} play <uri>` via Bash to play it.
2. Tell the user in one short line which song you picked and why, e.g. \
"\U0001F3B5 Now playing: <artist> - <title> - <one-sentence reason>".

Then stop, don't do anything else.

{track_lines}"""

    print(json.dumps({"decision": "block", "reason": reason, "suppressOutput": True}))


if __name__ == "__main__":
    main()
