#!/usr/bin/env python3
import os, sys, json, subprocess, shutil, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import scan

PORT = 8077
HOME = os.path.expanduser("~")
CONFIG_FILE = os.path.join(scan.CACHE_DIR, "config.json")

def app_base_dir():
    # Where to find bundled static files (bang3d.html, etc). Under a normal
    # `python app.py` run, that's just this file's own directory. Under a
    # PyInstaller-frozen .exe, __file__ doesn't point at the real bundle --
    # PyInstaller unpacks bundled "datas" into a temp dir at runtime and
    # exposes its path as sys._MEIPASS instead. Checking for that attribute
    # is the standard, documented way to tell the two cases apart.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

EXEC_EXT = {"sh","bash","py","pl","rb","bin","run","appimage","desktop","out","exe","bat","cmd","ps1","msi"}

def _drive_roots():
    if scan.IS_WINDOWS:
        import string
        return {f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")}
    return {"/"}

# paths fsop will never touch, regardless of what the frontend asks for —
# mirrors scan.VOID_PATHS, the home dir, and the drive root(s) (never delete/rename
# a whole drive or home outright)
PROTECTED = set(scan.VOID_PATHS) | {HOME} | _drive_roots()

def safe_open(path):
    if not os.path.exists(path):
        return False, "not found"
    if os.path.isdir(path):
        return False, "is a directory"
    ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
    try:
        if scan.IS_WINDOWS:
            os.startfile(path)  # always opens with the registered default app — never executes scripts as code
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, ("opened (view mode)" if (ext in EXEC_EXT or os.access(path, os.X_OK)) else "opened")
    except Exception as e:
        return False, str(e)

# ---- persisted root-folder config (the chosen "Bang" origin on this machine) ----
def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}

def save_config(cfg):
    try:
        os.makedirs(scan.CACHE_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f)
        os.replace(tmp, CONFIG_FILE)
        return True
    except OSError:
        return False

def get_root():
    cfg = load_config()
    root = cfg.get("root")
    if root and os.path.isdir(root):
        return root
    return None  # no root chosen yet, or it no longer exists

def set_root(path):
    if not os.path.isdir(path):
        return False, "not a valid directory"
    cfg = load_config()
    cfg["root"] = os.path.abspath(path)
    if save_config(cfg):
        return True, "root set"
    return False, "could not save config"

def is_protected(path):
    # guards the exact system roots themselves (can't rename/delete /etc,
    # /usr, the home dir, or / outright) — does NOT block normal files/folders
    # that merely live underneath one of those roots, since users legitimately
    # keep real data in places like /mnt, /media, /srv, /var/www, etc.
    # Also guards the active root's graveyard folder itself (can't rename,
    # move, or re-delete the graveyard -- it's where deleted things already
    # live, deleting it would defeat the whole point).
    path = os.path.abspath(path)
    if path in PROTECTED:
        return True
    root = get_root()
    if root and path == os.path.abspath(os.path.join(root, scan.GRAVEYARD_DIRNAME)):
        return True
    return False

def is_within_root(path):
    # Confinement check for WRITE operations only (create/rename/delete/move/
    # copy) — reads (/api/scan, /api/open) deliberately stay unrestricted so
    # Void mode can still browse past the origin. A write target must be the
    # active root itself, or somewhere underneath it (which includes the
    # graveyard, since that always lives inside the root).
    #
    # If no root has been chosen yet, nothing is considered "within root" --
    # fail closed rather than silently allowing writes anywhere.
    root = get_root()
    if not root:
        return False
    root_norm = scan._normcase_path(os.path.abspath(root))
    path_norm = scan._normcase_path(os.path.abspath(path))
    if path_norm == root_norm:
        return True
    # os.path.commonpath raises if the paths are on different drives (Windows)
    # or otherwise unrelated -- that's exactly the "not contained" case.
    try:
        return os.path.commonpath([root_norm, path_norm]) == root_norm
    except ValueError:
        return False

def validate_style_section(section, data):
    # Minimal shape-check before writing arbitrary POSTed JSON into
    # style.json — not a full schema validator, just enough to reject
    # obviously malformed payloads (wrong type, missing required keys)
    # rather than silently writing them to disk where later code might
    # not handle them gracefully.
    if section == "category_colors":
        if not isinstance(data, dict):
            return False, "category_colors must be an object"
        if not all(isinstance(v, str) for v in data.values()):
            return False, "category_colors values must be strings"
        return True, None
    if section in ("age_stages", "use_stages"):
        if not isinstance(data, list) or not data:
            return False, f"{section} must be a non-empty list"
        for stage in data:
            if not isinstance(stage, dict):
                return False, f"{section} entries must be objects"
            if "name" not in stage or "pattern" not in stage:
                return False, f"{section} entries need 'name' and 'pattern'"
            if "max_days" in stage and stage["max_days"] is not None and not isinstance(stage["max_days"], (int, float)):
                return False, "max_days must be a number or null"
        return True, None
    if section == "lastused_glow":
        if not isinstance(data, dict):
            return False, "lastused_glow must be an object"
        for key in ("window_days", "max_glow", "min_glow"):
            if key in data and not isinstance(data[key], (int, float)):
                return False, f"{key} must be a number"
        return True, None
    if section == "rendering":
        if not isinstance(data, dict):
            return False, "rendering must be an object"
        if not all(isinstance(v, (int, float)) for v in data.values()):
            return False, "rendering values must be numbers"
        return True, None
    return False, "unknown section"

def fs_create(parent, name, kind):
    if not name or "/" in name or "\\" in name or "\x00" in name:
        return False, "invalid name"
    parent = os.path.abspath(parent)
    if is_protected(parent) or not is_within_root(parent):
        return False, "outside the active origin"
    if not os.path.isdir(parent):
        return False, "destination directory does not exist"
    target = os.path.join(parent, name)
    if os.path.exists(target):
        return False, "already exists"
    try:
        if kind == "folder":
            os.makedirs(target)
        else:
            with open(target, "x"):
                pass
        return True, "created"
    except OSError as e:
        return False, str(e)

def fs_rename(path, new_name):
    path = os.path.abspath(path)
    if is_protected(path) or not is_within_root(path):
        return False, "outside the active origin"
    if not new_name or "/" in new_name or "\\" in new_name or "\x00" in new_name:
        return False, "invalid name"
    if not os.path.exists(path):
        return False, "not found"
    new_path = os.path.join(os.path.dirname(path), new_name)
    if os.path.exists(new_path):
        return False, "a file with that name already exists"
    try:
        os.rename(path, new_path)
        return True, "renamed"
    except OSError as e:
        return False, str(e)

def fs_delete(path):
    # "Delete" never actually deletes — it moves the item into a graveyard
    # folder inside the active root, so a misclick is always recoverable.
    # Permanently clearing the graveyard out is a deliberate, separate action
    # the person takes in Explorer (or a future "empty graveyard" feature),
    # not something this button can trigger.
    path = os.path.abspath(path)
    if is_protected(path) or not is_within_root(path):
        return False, "outside the active origin"
    if not os.path.exists(path):
        return False, "not found"
    root = get_root()  # is_within_root() already guarantees this isn't None
    grave = scan.graveyard_path(root)
    if os.path.abspath(os.path.dirname(path)) == os.path.abspath(grave):
        return False, "already in the graveyard"
    name = os.path.basename(path)
    target = os.path.join(grave, name)
    if os.path.exists(target):
        # collision: two different items with the same name sent to the
        # graveyard -- never silently overwrite, append a timestamp instead.
        stem, ext = os.path.splitext(name)
        target = os.path.join(grave, f"{stem}_{int(time.time())}{ext}")
    try:
        shutil.move(path, target)
        return True, "moved to graveyard"
    except OSError as e:
        return False, str(e)

def fs_move(path, dest_dir):
    path = os.path.abspath(path)
    dest_dir = os.path.abspath(dest_dir)
    if is_protected(path) or not is_within_root(path):
        return False, "outside the active origin"
    if not is_within_root(dest_dir):
        return False, "destination is outside the active origin"
    if not os.path.exists(path):
        return False, "not found"
    if not os.path.isdir(dest_dir):
        return False, "destination directory does not exist"
    target = os.path.join(dest_dir, os.path.basename(path))
    if os.path.exists(target):
        return False, "a file with that name already exists at destination"
    try:
        shutil.move(path, target)
        return True, "moved"
    except OSError as e:
        return False, str(e)

def fs_copy(path, dest_dir):
    path = os.path.abspath(path)
    dest_dir = os.path.abspath(dest_dir)
    # Source is a READ, same as /api/scan — copying FROM outside the root
    # doesn't write anything there, so it's not confined. The destination IS
    # a write, so it must stay inside the active root.
    if not is_within_root(dest_dir):
        return False, "destination is outside the active origin"
    if not os.path.exists(path):
        return False, "not found"
    if not os.path.isdir(dest_dir):
        return False, "destination directory does not exist"
    target = os.path.join(dest_dir, os.path.basename(path))
    if os.path.exists(target):
        return False, "a file with that name already exists at destination"
    try:
        if os.path.isdir(path):
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)
        return True, "copied"
    except (OSError, shutil.Error) as e:
        return False, str(e)

class BangHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # No Access-Control-Allow-Origin header: the frontend is served from
        # this exact same origin (127.0.0.1:PORT), so no cross-origin access
        # is needed. A wildcard CORS header here would let ANY website open
        # in the same browser/webview read this API's responses — including
        # file contents and directory listings — which is unacceptable for
        # an API with this much filesystem access.
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/style":
            # full style document — category colors, age-pattern stages,
            # last-used glow curve, and rendering (Tune-panel) values. This
            # is the single source of truth the frontend's settings menu
            # reads from and writes back to via /api/style/update.
            self._send(200, json.dumps(scan.load_style()))
            return

        if parsed.path == "/api/home":
            # tells the frontend what to use as the universe origin: a previously
            # chosen root if one is saved, otherwise null so the frontend shows
            # a first-launch folder picker instead of assuming a path that may
            # not exist on this machine.
            root = get_root()
            self._send(200, json.dumps({"root": root, "home": HOME, "platform": "windows" if scan.IS_WINDOWS else ("mac" if sys.platform=="darwin" else "linux")}))
            return

        if parsed.path == "/api/setroot":
            qs = parse_qs(parsed.query)
            path = unquote(qs.get("path", [""])[0])
            ok, msg = set_root(path)
            self._send(200 if ok else 400, json.dumps({"ok": ok, "msg": msg, "root": path if ok else None}))
            return

        if parsed.path == "/api/scan":
            qs = parse_qs(parsed.query)
            target = unquote(qs.get("path", [HOME])[0])
            data = scan.scan(target)
            self._send(200, json.dumps(data))
            return

        if parsed.path == "/api/open":
            qs = parse_qs(parsed.query)
            target = unquote(qs.get("path", [""])[0])
            ok, msg = safe_open(target)
            self._send(200 if ok else 400, json.dumps({"ok": ok, "msg": msg}))
            return

        if parsed.path == "/api/fsop":
            qs = parse_qs(parsed.query)
            op = unquote(qs.get("op", [""])[0])
            try:
                if op == "create":
                    parent = unquote(qs.get("path", [""])[0])
                    name = unquote(qs.get("name", [""])[0])
                    kind = unquote(qs.get("kind", ["file"])[0])
                    ok, msg = fs_create(parent, name, "folder" if kind == "folder" else "file")
                elif op == "rename":
                    path = unquote(qs.get("path", [""])[0])
                    new_name = unquote(qs.get("newName", [""])[0])
                    ok, msg = fs_rename(path, new_name)
                elif op == "delete":
                    path = unquote(qs.get("path", [""])[0])
                    ok, msg = fs_delete(path)
                elif op == "move":
                    path = unquote(qs.get("path", [""])[0])
                    dest = unquote(qs.get("dest", [""])[0])
                    ok, msg = fs_move(path, dest)
                elif op == "copy":
                    path = unquote(qs.get("path", [""])[0])
                    dest = unquote(qs.get("dest", [""])[0])
                    ok, msg = fs_copy(path, dest)
                else:
                    ok, msg = False, "unknown operation"
            except Exception as e:
                ok, msg = False, str(e)
            self._send(200 if ok else 400, json.dumps({"ok": ok, "msg": msg}))
            return

        if parsed.path == "/" or parsed.path == "/index.html":
            try:
                with open(os.path.join(app_base_dir(), "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html")
            except FileNotFoundError:
                self._send(200, "<h1>Bang server running. index.html not built yet.</h1>", "text/html")
            return

        if parsed.path.endswith(".html") and "/" not in parsed.path[1:] and ".." not in parsed.path:
            fname = parsed.path.lstrip("/")
            fpath = os.path.join(app_base_dir(), fname)
            if os.path.isfile(fpath):
                with open(fpath, "rb") as f:
                    self._send(200, f.read(), "text/html")
                return
            self._send(404, json.dumps({"error": "page not found"}))
            return

        self._send(404, json.dumps({"error": "not found"}))

    MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB — generous for the largest real payload (a full style.json section), nowhere near enough to be a memory-exhaustion vector

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length < 0 or length > self.MAX_BODY_BYTES:
            self._send(413, json.dumps({"ok": False, "msg": "request body too large"}))
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            self._send(400, json.dumps({"ok": False, "msg": "invalid JSON body"}))
            return

        if parsed.path == "/api/style/update":
            # payload: {"section": "category_colors", "data": {...whole section...}}
            # Replaces one entire top-level section of style.json at once
            # (the frontend sends the complete edited section, not a partial
            # patch) — simpler and harder to corrupt than deep-merging.
            section = payload.get("section")
            data = payload.get("data")
            valid_sections = {"category_colors", "age_stages", "use_stages", "lastused_glow", "rendering"}
            if section not in valid_sections:
                self._send(400, json.dumps({"ok": False, "msg": "unknown section"}))
                return
            if data is None:
                self._send(400, json.dumps({"ok": False, "msg": "missing data"}))
                return
            valid, err = validate_style_section(section, data)
            if not valid:
                self._send(400, json.dumps({"ok": False, "msg": err}))
                return
            style = scan.load_style()
            style[section] = data
            ok = scan.save_style(style)
            self._send(200 if ok else 500, json.dumps({"ok": ok, "msg": "saved" if ok else "write failed"}))
            return

        if parsed.path == "/api/style/reset":
            # payload: {"section": "category_colors"} or {} / {"section": "all"} for everything
            section = payload.get("section", "all")
            if section == "all":
                ok = scan.save_style(scan._default_style())
                self._send(200 if ok else 500, json.dumps({"ok": ok, "msg": "reset all" if ok else "write failed"}))
                return
            ok, msg = scan.reset_style_section(section)
            self._send(200 if ok else 400, json.dumps({"ok": ok, "msg": msg}))
            return

        self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    # 127.0.0.1 only — never 0.0.0.0. This server exposes full file
    # read/write/delete/move on the local filesystem with no
    # authentication; binding to all interfaces would put that on the
    # network for any other device to reach. app.py (the normal entry
    # point) already starts its own instance this same way — this
    # block only matters if someone runs `python server.py` directly.
    print(f"Bang server running at http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop.")
    ThreadingHTTPServer(("127.0.0.1", PORT), BangHandler).serve_forever()
