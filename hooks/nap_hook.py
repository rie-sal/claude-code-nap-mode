#!/usr/bin/env python3
"""
Claude Code "Stop" hook for claude-code-nap-mode.

Fires after every response Claude gives. If nap mode is on, it asks a
throwaway headless Claude instance to pick one song (by vibes) from your
cached Spotify library that matches the mood of what was just done, plays it,
then surfaces a one-line "now playing" message via the hook's systemMessage
field — which shows up to you in the transcript without forcing Claude to
generate a blocked continuation, and without dumping the full track list /
instructions into your screen (that part only ever goes to the hidden
headless subprocess, never printed to your terminal).

Register this as a Stop hook in ~/.claude/settings.json (see README.md).

Only fires when the session's permission_mode is "auto" — nap mode implies
you're away from the keyboard, so anything that isn't running fully
autonomously (i.e. would otherwise sit there waiting on a permission prompt)
shouldn't be picking songs either.

Recursion guard: the headless `claude -p` subprocess is itself a Claude Code
invocation, so its own Stop hook will fire this same script again when it
finishes. NAP_HOOK_ACTIVE=1 is set on that subprocess's environment and
checked at the very top of main(), so that inner invocation no-ops instead
of recursing.
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

MAX_TRACKS_IN_PROMPT = int(os.environ.get("NAP_MAX_TRACKS", "150"))
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
    if os.environ.get("NAP_HOOK_ACTIVE") == "1":
        return  # this is the headless subprocess's own Stop event; don't recurse

    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        log(f"failed to parse stdin: {e}")
        return

    state = load_json(STATE_PATH, {"enabled": False})
    if not state.get("enabled"):
        return

    if payload.get("permission_mode") != "auto":
        log(f"nap mode on but session isn't in auto mode "
            f"(permission_mode={payload.get('permission_mode')!r}) — skipping")
        return

    cache = load_json(CACHE_PATH)
    if not cache or not cache.get("tracks"):
        log("nap mode on but no library cache yet (run: nap.py refresh-cache)")
        return

    tracks = cache["tracks"]
    if len(tracks) > MAX_TRACKS_IN_PROMPT:
        tracks = random.sample(tracks, MAX_TRACKS_IN_PROMPT)

    # Index instead of full Spotify URI in the prompt: the URI (~36 chars) carries
    # no vibe signal, it's pure token overhead. Model picks by number, we map back.
    track_lines = "\n".join(
        f"{i}\t{t['artist']} - {t['name']} [{t['playlist']}]"
        for i, t in enumerate(tracks, start=1)
    )

    last_message = (payload.get("last_assistant_message") or "")[:MAX_MESSAGE_CHARS]
    if not last_message.strip():
        last_message = "(no text response, likely a quick tool-only action)"

    prompt = f"""You are picking background music for a coder who just finished a stretch of AI-assisted
work and is napping while it happens. Here is what the assistant just finished doing:
---
{last_message}
---
Pick exactly ONE song from the numbered list below that matches the mood/energy of that work
(use vibes, not genre rules — e.g. a hairy bug fix resolved might call for something
tense-then-triumphant, routine cleanup might call for something chill, shipping a feature
might call for something upbeat).

Commit to your first instinct — do not second-guess, hedge, or reconsider partway through.
Reply with EXACTLY two lines and absolutely nothing else (no reasoning, no preamble, no
"wait" or "let me reconsider", no markdown):
line 1: just the number from the list, nothing else
line 2: one sentence on why it fits the vibe

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

    # If the model second-guesses itself ("2... wait, let me reconsider... 19"), the
    # LAST standalone-number line is its real final answer, not the first one seen.
    lines = output.splitlines()
    chosen_line_no = None
    idx = None
    for i, line in enumerate(lines):
        m = re.fullmatch(r"\s*#?(\d{1,4})\.?\s*", line)
        if m and 1 <= int(m.group(1)) <= len(tracks):
            idx = int(m.group(1))
            chosen_line_no = i

    if idx is None:
        log(f"no valid track index in model output: {output!r}")
        return

    track = tracks[idx - 1]
    uri = track["uri"]
    justification = next(
        (l.strip().lstrip("-:").strip() for l in lines[chosen_line_no + 1:] if l.strip()),
        "",
    )
    if not justification:
        justification = "(no justification given)"

    label = f"{track['artist']} - {track['name']}"
    log(f"picked: {label} — {justification}")

    try:
        subprocess.run(
            ["python3", NAP_PY, "play", uri],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        log(f"play failed: {e}")

    print(json.dumps({
        "systemMessage": f"\U0001F3B5 Now playing: {label} — {justification}"
    }))


if __name__ == "__main__":
    main()
