---
description: Turn nap mode on/off or check status — while on, Claude plays a mood-matched song from your Spotify playlists after each response it finishes
argument-hint: on | off | status
allowed-tools: Bash(python3 ~/claude-code-nap-mode/nap.py:*)
---

Run this and report the one-line result back, nothing else:

```
python3 ~/claude-code-nap-mode/nap.py $ARGUMENTS
```

If `$ARGUMENTS` is empty, use `status` instead of leaving it blank. Valid values are `on`, `off`, or `status` — if given anything else, just pass it through and let the script's own error message explain.
