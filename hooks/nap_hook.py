#!/usr/bin/env python3
"""
Claude Code "Stop" hook for claude-code-nap-mode.

Fires after every response Claude gives. If nap mode is on, it picks a song
from your cached Spotify library in two small hidden headless `claude -p`
calls instead of one big one:

  1. Pick a PLAYLIST (by name) that matches the vibe of what was just done.
     Your playlist names are themselves vibe/genre buckets (e.g. "berlin-
     derived hypnodub techno", "softcore narcotic dreamgaze") — richer signal
     than Spotify's own audio-features (danceability/tempo/energy/valence),
     which aren't usable here anyway: Spotify killed that endpoint for any
     app not grandfathered in before Nov 27 2024, no exceptions, no waitlist.
  2. Pick a TRACK from just that one playlist.

This considers your whole library (every playlist is a stage-1 candidate)
instead of a random flat sample, while keeping each individual prompt small
(playlist names are short; a single playlist's tracks are usually a much
smaller list than the whole cache).

Plays the track directly, then surfaces a one-line "now playing" message via
the hook's systemMessage field — visible in the transcript without forcing
Claude to generate a blocked continuation, and without dumping any of the
raw picking prompts onto your screen (those only ever go to the hidden
headless subprocesses, never printed to your terminal).

Register this as a Stop hook in ~/.claude/settings.json (see README.md).

Only fires when the session's permission_mode is "auto" — nap mode implies
you're away from the keyboard, so anything that isn't running fully
autonomously (i.e. would otherwise sit there waiting on a permission prompt)
shouldn't be picking songs either.

Recursion guard: each headless `claude -p` subprocess is itself a Claude Code
invocation, so its own Stop hook would fire this same script again when it
finishes. NAP_HOOK_ACTIVE=1 is set on that subprocess's environment and
checked at the very top of main(), so those inner invocations no-op instead
of recursing.
"""
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import defaultdict

NAP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAP_PY = os.path.join(NAP_DIR, "nap.py")

CONFIG_DIR = os.path.expanduser(
    os.environ.get("NAP_MODE_CONFIG_DIR", "~/.config/claude-code-nap-mode")
)
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")
CACHE_PATH = os.path.join(CONFIG_DIR, "library_cache.json")
LOG_PATH = os.path.join(CONFIG_DIR, "nap_hook.log")

MAX_TRACKS_PER_PLAYLIST = int(os.environ.get("NAP_MAX_TRACKS_PER_PLAYLIST", "300"))
MAX_MESSAGE_CHARS = 4000

COMMIT_INSTRUCTION = (
    'Commit to your first instinct — do not second-guess, hedge, or reconsider '
    'partway through. Reply with EXACTLY two lines and absolutely nothing else '
    '(no reasoning, no preamble, no "wait" or "let me reconsider", no markdown):\n'
    "line 1: the number from the list, a space, then the exact text from that line "
    "(so it's checkable against what you actually meant — e.g. \"5 bl00dwave - nights\")\n"
    "line 2: one sentence on why it fits the vibe"
)


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


def ask_headless(prompt):
    env = dict(os.environ)
    env["NAP_HOOK_ACTIVE"] = "1"
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    return result.stdout.strip()


def parse_index_and_justification(output, labels):
    """Takes the LAST number+text line as the real final answer — small models
    occasionally second-guess mid-output ("2... wait... 19"), and the last one
    is reliably the actual final pick.

    Self-correction: models are much better at recalling a track's actual name
    than at accurately counting its position in a numbered list, so if the
    stated text doesn't match the label at the claimed index, trust the text
    and look up which index it actually corresponds to.
    """
    lines = output.splitlines()
    idx, stated_text, chosen_line_no = None, None, None
    for i, line in enumerate(lines):
        m = re.match(r"\s*#?(\d{1,4})\s*[-:.\)]?\s*(.*)$", line)
        if m and 1 <= int(m.group(1)) <= len(labels):
            idx = int(m.group(1))
            stated_text = m.group(2).strip()
            chosen_line_no = i
    if idx is None:
        return None, None

    if stated_text:
        claimed_label = labels[idx - 1].lower()
        stated_lower = stated_text.lower()
        if stated_lower not in claimed_label and claimed_label not in stated_lower:
            for j, label in enumerate(labels, start=1):
                if stated_lower in label.lower() or label.lower() in stated_lower:
                    idx = j
                    break

    justification = next(
        (l.strip().lstrip("-:").strip() for l in lines[chosen_line_no + 1:] if l.strip()),
        "",
    )
    return idx, justification


def main():
    if os.environ.get("NAP_HOOK_ACTIVE") == "1":
        return  # this is a headless subprocess's own Stop event; don't recurse

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

    by_playlist = defaultdict(list)
    for t in cache["tracks"]:
        by_playlist[t["playlist"]].append(t)
    playlist_names = list(by_playlist.keys())

    last_message = (payload.get("last_assistant_message") or "")[:MAX_MESSAGE_CHARS]
    if not last_message.strip():
        last_message = "(no text response, likely a quick tool-only action)"

    playlist_lines = "\n".join(
        f"{i}\t{name} ({len(by_playlist[name])} tracks)"
        for i, name in enumerate(playlist_names, start=1)
    )
    stage1_prompt = f"""You are picking background music for a coder who just finished a stretch of
AI-assisted work and is napping while it happens. Here is what the assistant just finished doing:
---
{last_message}
---
Below is a numbered list of the coder's own Spotify playlists (their names hint at genre/mood —
e.g. a moody ambient playlist vs. a high-energy dance one). Pick the ONE playlist whose overall
vibe best matches the mood/energy of what was just done (use vibes, not genre rules — a hairy bug
fix resolved might call for something tense-then-triumphant, routine cleanup might call for
something chill, shipping a feature might call for something upbeat).

{COMMIT_INSTRUCTION}
(line 2 here should explain why that playlist, not a specific track yet)

{playlist_lines}"""

    try:
        stage1_output = ask_headless(stage1_prompt)
    except Exception as e:
        log(f"stage 1 (playlist pick) headless call failed: {e}")
        return

    playlist_idx, _ = parse_index_and_justification(stage1_output, playlist_names)
    if playlist_idx is None:
        log(f"no valid playlist index in stage 1 output: {stage1_output!r}")
        return

    playlist_name = playlist_names[playlist_idx - 1]
    tracks = by_playlist[playlist_name]
    if len(tracks) > MAX_TRACKS_PER_PLAYLIST:
        tracks = random.sample(tracks, MAX_TRACKS_PER_PLAYLIST)

    track_labels = [f"{t['artist']} - {t['name']}" for t in tracks]
    track_lines = "\n".join(f"{i}\t{label}" for i, label in enumerate(track_labels, start=1))
    stage2_prompt = f"""You are picking background music for a coder who just finished a stretch of
AI-assisted work and is napping while it happens. Here is what the assistant just finished doing:
---
{last_message}
---
You already chose the playlist "{playlist_name}" as the best vibe match. Now pick the ONE track
from it below that best fits that same mood/energy.

{COMMIT_INSTRUCTION}

{track_lines}"""

    try:
        stage2_output = ask_headless(stage2_prompt)
    except Exception as e:
        log(f"stage 2 (track pick) headless call failed: {e}")
        return

    track_idx, justification = parse_index_and_justification(stage2_output, track_labels)
    if track_idx is None:
        log(f"no valid track index in stage 2 output: {stage2_output!r}")
        return
    if not justification:
        justification = "(no justification given)"

    track = tracks[track_idx - 1]
    uri = track["uri"]
    label = f"{track['artist']} - {track['name']}"
    log(f"picked: {label} [{playlist_name}] — {justification}")

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
