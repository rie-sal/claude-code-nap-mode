#!/usr/bin/env python3
"""
claude-code-nap-mode: pick a song from your own Spotify playlists and play it,
driven by whatever's calling this (e.g. an AI agent choosing by "vibes").

Stdlib only, no third-party deps. macOS-first (uses `open -a Spotify` to
wake a local Spotify Connect device), but the Web API calls are portable.

Config / state lives in ~/.config/claude-code-nap-mode/ (XDG-ish), separate from
wherever this repo is cloned, so multiple checkouts / machines share nothing
by accident and nothing mutable ends up inside the git working tree.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

CONFIG_DIR = os.path.expanduser(
    os.environ.get("NAP_MODE_CONFIG_DIR", "~/.config/claude-code-nap-mode")
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
TOKEN_PATH = os.path.join(CONFIG_DIR, "token.json")
CACHE_PATH = os.path.join(CONFIG_DIR, "library_cache.json")
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = (
    "playlist-read-private "
    "playlist-read-collaborative "
    "user-read-playback-state "
    "user-modify-playback-state"
)


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    _ensure_config_dir()
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_config():
    cfg = _load_json(CONFIG_PATH)
    if not cfg or not cfg.get("client_id"):
        sys.exit(
            f"No config found at {CONFIG_PATH}.\n"
            f"Copy config.example.json there and fill in your Spotify Client ID.\n"
            f"See README.md for how to create a Spotify app."
        )
    cfg.setdefault("redirect_uri", "http://127.0.0.1:8888/callback")
    return cfg


# ---------- OAuth (Authorization Code + PKCE) ----------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_pkce_pair():
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _run_callback_server(redirect_uri: str) -> str:
    parsed = urllib.parse.urlparse(redirect_uri)
    host, port = parsed.hostname, parsed.port or 80
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = b"<html><body>Spotify auth complete, close this tab and go back to your terminal.</body></html>"
            else:
                result["error"] = qs.get("error", ["unknown_error"])[0]
                body = b"<html><body>Auth failed, check your terminal.</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # keep terminal quiet

    server = http.server.HTTPServer((host, port), Handler)
    server.timeout = 120
    server.handle_request()
    if "code" not in result:
        sys.exit(f"OAuth failed: {result.get('error', 'no code returned')}")
    return result["code"]


def cmd_auth(args):
    cfg = _load_config()
    verifier, challenge = _make_pkce_pair()
    params = {
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": cfg["redirect_uri"],
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": SCOPES,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print("Opening browser for Spotify login/consent...")
    print(f"If it doesn't open automatically, visit:\n{url}\n")
    webbrowser.open(url)
    code = _run_callback_server(cfg["redirect_uri"])

    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        token = json.load(resp)
    token["obtained_at"] = time.time()
    _save_json(TOKEN_PATH, token)
    print("Auth complete. Token saved.")


def _refresh_token(cfg, token):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id": cfg["client_id"],
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        new_token = json.load(resp)
    new_token.setdefault("refresh_token", token["refresh_token"])
    new_token["obtained_at"] = time.time()
    _save_json(TOKEN_PATH, new_token)
    return new_token


def _get_access_token():
    cfg = _load_config()
    token = _load_json(TOKEN_PATH)
    if not token:
        sys.exit("Not authenticated yet. Run: nap.py auth")
    age = time.time() - token.get("obtained_at", 0)
    if age > token.get("expires_in", 3600) - 60:
        token = _refresh_token(cfg, token)
    return token["access_token"]


def _api(method, path, params=None, body=None):
    token = _get_access_token()
    url = f"{API_BASE}{path}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return None
        raise RuntimeError(f"Spotify API {method} {path} -> {e.code}: {e.read().decode()}")


# ---------- Library cache (owned playlists only) ----------

def _collect_playlist_tracks(pl, params, tracks):
    while True:
        page = _api("GET", f"/playlists/{pl['id']}/items", params=params)
        for item in page["items"]:
            t = item.get("item")
            if not t or t.get("is_local") or not t.get("uri", "").startswith("spotify:track:"):
                continue
            tracks[t["uri"]] = {
                "uri": t["uri"],
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t.get("artists", [])),
                "playlist": pl["name"],
            }
        if not page.get("next"):
            break
        params["offset"] += params["limit"]


def cmd_refresh_cache(args):
    me = _api("GET", "/me")
    my_id = me["id"]

    playlists = []
    params = {"limit": 50, "offset": 0}
    while True:
        page = _api("GET", "/me/playlists", params=params)
        playlists.extend(page["items"])
        if not page.get("next"):
            break
        params["offset"] += params["limit"]

    owned = [p for p in playlists if p and p.get("owner", {}).get("id") == my_id]
    print(f"Found {len(owned)} playlists owned by you (of {len(playlists)} total).")

    tracks = {}
    skipped = []
    for pl in owned:
        params = {"limit": 100, "offset": 0,
                   "fields": "items(item(uri,name,artists(name),is_local)),next"}
        try:
            _collect_playlist_tracks(pl, params, tracks)
        except RuntimeError as e:
            skipped.append(pl["name"])
            print(f"  skipping '{pl['name']}' ({e})")

    if skipped:
        print(f"Skipped {len(skipped)} playlist(s) Spotify wouldn't let us read: {', '.join(skipped)}")

    cache = {"updated_at": time.time(), "tracks": list(tracks.values())}
    _save_json(CACHE_PATH, cache)
    print(f"Cached {len(cache['tracks'])} unique tracks -> {CACHE_PATH}")


def cmd_list(args):
    cache = _load_json(CACHE_PATH)
    if not cache:
        sys.exit("No cache yet. Run: nap.py refresh-cache")
    for t in cache["tracks"]:
        print(f"{t['uri']}\t{t['artist']} - {t['name']}\t[{t['playlist']}]")


# ---------- Playback ----------

def _find_device():
    data = _api("GET", "/me/player/devices")
    devices = data.get("devices", []) if data else []
    if not devices:
        return None
    active = [d for d in devices if d.get("is_active")]
    return (active or devices)[0]


def cmd_play(args):
    uri = args.uri
    if not uri.startswith("spotify:track:"):
        sys.exit(f"Not a track URI: {uri}")

    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Spotify"], check=False)

    device = None
    for _ in range(10):
        device = _find_device()
        if device:
            break
        time.sleep(1)
    if not device:
        sys.exit("No Spotify Connect device found (is Spotify installed/logged in?)")

    _api("PUT", "/me/player/play", params={"device_id": device["id"]}, body={"uris": [uri]})
    print(f"Playing {uri} on {device['name']}")


# ---------- Nap mode on/off state ----------

def cmd_on(args):
    _save_json(STATE_PATH, {"enabled": True})
    print("nap mode: on")


def cmd_off(args):
    _save_json(STATE_PATH, {"enabled": False})
    print("nap mode: off")


def cmd_status(args):
    state = _load_json(STATE_PATH, {"enabled": False})
    print("on" if state.get("enabled") else "off")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="one-time browser OAuth login").set_defaults(func=cmd_auth)
    sub.add_parser("refresh-cache", help="pull all your owned playlists' tracks into a local cache").set_defaults(func=cmd_refresh_cache)
    sub.add_parser("list", help="print cached tracks (uri, artist - name, playlist)").set_defaults(func=cmd_list)

    p_play = sub.add_parser("play", help="play a specific track uri")
    p_play.add_argument("uri")
    p_play.set_defaults(func=cmd_play)

    sub.add_parser("on", help="turn nap mode on").set_defaults(func=cmd_on)
    sub.add_parser("off", help="turn nap mode off").set_defaults(func=cmd_off)
    sub.add_parser("status", help="print on/off").set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
