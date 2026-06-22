#!/usr/bin/env python3
import os, sys, json, stat, time
import concurrent.futures as _cf

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    # Windows system/program areas — the equivalent of Linux's /etc, /usr, etc.
    # Built from %SystemDrive% so this works on any drive letter, not just C:.
    _sysdrive = os.environ.get("SystemDrive", "C:") + "\\"
    VOID_PATHS = {
        os.path.join(_sysdrive, "Windows"),
        os.path.join(_sysdrive, "Program Files"),
        os.path.join(_sysdrive, "Program Files (x86)"),
        os.path.join(_sysdrive, "ProgramData"),
        os.path.join(_sysdrive, "$Recycle.Bin"),
        os.path.join(_sysdrive, "System Volume Information"),
        os.path.join(_sysdrive, "Recovery"),
    }
else:
    VOID_PATHS = { "/etc","/usr","/bin","/sbin","/lib","/lib64","/var",
      "/proc","/sys","/dev","/run","/boot","/opt","/tmp","/srv","/mnt","/media" }

CACHE_DIR = os.path.expanduser("~/.bang")
CACHE_FILE = os.path.join(CACHE_DIR, "weights.json")
EXT_CACHE_FILE = os.path.join(CACHE_DIR, "exttypes.json")
WALK_CAP = 20000

# "Delete" doesn't delete — it moves the item into a graveyard folder inside
# the chosen root, so nothing is ever lost to a misclick. The dotfile name
# means is_void() (below) already hides it from normal browsing for free, on
# both Windows (explicit hidden-attr/dotfile check) and Linux/Mac (dotfile
# convention) -- no separate void-list entry needed since it's per-root, not
# a single fixed absolute path like the VOID_PATHS above.
GRAVEYARD_DIRNAME = ".bang_graveyard"

def graveyard_path(root):
    """Resolve (and ensure exists) the graveyard folder for the given root.
    Created lazily on first use, not at every boot, so a fresh root with
    nothing ever sent to the graveyard doesn't get a stray empty folder."""
    path = os.path.join(os.path.abspath(root), GRAVEYARD_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path

# ---- color-by-category ----
# A small, stable set of categories. This is the part that should basically
# never need to grow — new file types get sorted into one of these, they
# don't each get their own bespoke color.
CATEGORY_NAMES = ["document","spreadsheet","presentation","code","image","video",
                  "audio","archive","executable","shortcut","font","data","other"]

# These are only the SEED values written into style.json the first time it's
# created. Once style.json exists, IT is the source of truth — nothing in
# this file is consulted again for color. Editing style.json (by hand or via
# the settings menu in bang3d.html) is what actually changes a planet's color.
DEFAULT_CATEGORY_COLOR = {
    "document":   "rgb(111,177,255)",   # blue
    "spreadsheet":"rgb(120,219,150)",   # green
    "presentation":"rgb(255,184,120)",  # orange
    "code":       "rgb(176,124,255)",   # purple
    "image":      "rgb(72,214,192)",    # teal
    "video":      "rgb(255,107,138)",   # pink
    "audio":      "rgb(255,217,94)",    # yellow
    "archive":    "rgb(212,162,76)",    # tan
    "executable": "rgb(255,93,93)",     # red
    "shortcut":   "rgb(154,166,178)",   # grey-blue
    "font":       "rgb(192,160,255)",   # lilac
    "data":       "rgb(255,157,77)",    # amber
    "other":      "rgb(205,214,224)",   # neutral grey
}

# Age -> structure stage (how "built up" a planet's civilization looks).
# A small, curated set of named stages so the frontend can offer a style
# picker rather than exposing knobs that could produce a broken-looking
# planet. `max_days` is the upper edge of each stage; the last stage has no
# upper bound (null). `pattern` here is the STAGE KEY consumed by the
# frontend's texture system (see bang3d.html's AGE_GENERATORS) -- it picks
# which of the 4 baked/procedural age textures to show, it is NOT the old
# scattered-spheres "pattern" from the previous design.
DEFAULT_AGE_STAGES = [
    {"name": "bare",       "max_days": 5,    "pattern": "bare"},
    {"name": "outpost",    "max_days": 30,   "pattern": "outpost"},
    {"name": "settlement", "max_days": 360,  "pattern": "settlement"},
    {"name": "metropolis", "max_days": None, "pattern": "metropolis"},
]

# Idle time (since last modified) -> decay/overlay stage. Independent axis
# from age: a brand-new file that's already untouched reads as a sparse
# structure with a derelict overlay; an old, actively-edited file reads as a
# dense structure with a clean (active) overlay. This is purely the visual
# TEXTURE overlay -- separate from lastused_glow below, which controls
# emissive brightness, a different visual channel.
DEFAULT_USE_STAGES = [
    {"name": "active",    "max_days": 3,    "pattern": "active"},
    {"name": "quiet",     "max_days": 14,   "pattern": "quiet"},
    {"name": "neglected", "max_days": 60,   "pattern": "neglected"},
    {"name": "derelict",  "max_days": None, "pattern": "derelict"},
]

# Last-used -> glow/brightness. Continuous, not staged: brightness is
# linearly interpolated between max_glow (used today) and min_glow (untouched
# for window_days or more).
DEFAULT_LASTUSED_GLOW = {
    "window_days": 5,
    "max_glow": 1.0,
    "min_glow": 0.15,
}

# Rendering — the existing Tune-panel values, now living here instead of
# being baked into bang3d.html's JS, so they're part of the same one
# persisted/editable style document as everything else.
DEFAULT_RENDERING = {
    "galaxyGlow":0.01, "galaxyHalo":5.3, "galaxyPoint":0.35, "galaxyHaloOp":0.34,
    "bodyGloss":0.26, "bodyRim":0, "bodyEmissive":0, "bodyScale":0.68,
    "fog":1.15, "stars":2, "core":0.85,
    "speed":2.19, "orbitOp":0.06, "trailOp":0.6, "trailLen":2.44,
}

STYLE_FILE = os.path.join(CACHE_DIR, "style.json")

def _default_style():
    return {
        "category_colors": dict(DEFAULT_CATEGORY_COLOR),
        "age_stages": [dict(s) for s in DEFAULT_AGE_STAGES],
        "use_stages": [dict(s) for s in DEFAULT_USE_STAGES],
        "lastused_glow": dict(DEFAULT_LASTUSED_GLOW),
        "rendering": dict(DEFAULT_RENDERING),
    }

_style = None
def load_style():
    """Load style.json, creating it with seed defaults on first run. This is
    the ONLY function the rest of scan.py should call to get style data —
    everything else (color_for_ext, age pattern lookup, etc.) reads through
    this, never through the DEFAULT_* dicts directly, once a style.json
    exists on disk."""
    global _style
    if _style is not None:
        return _style
    try:
        with open(STYLE_FILE) as f:
            on_disk = json.load(f)
        # merge over defaults so a style.json from an older Bang version
        # (missing a newer section) doesn't crash — it just fills the gap
        # with that section's default rather than failing to load.
        merged = _default_style()
        for k, v in on_disk.items():
            merged[k] = v
        _migrate_legacy_age_pattern_names(merged)
        _style = merged
    except (FileNotFoundError, ValueError):
        _style = _default_style()
        save_style(_style)
    return _style

# Old (pre-texture-system) age_stages used a different pattern vocabulary:
# smooth / light_specks / craters / deep_scarring. Those are positionally
# equivalent to the new bare / outpost / settlement / metropolis stages (same
# 4 slots, same ordering, same meaning of "how weathered/built-up"), so an
# upgrading user's existing thresholds and customizations are preserved --
# only the pattern KEY each stage points to is remapped to a name the new
# frontend texture system actually recognizes. Without this, the old names
# still work via the frontend's day-based fallback (see ageStageIndex() in
# bang3d.html), but the stored pattern field would stay stale forever and any
# custom remapping the user made (e.g. swapping which pattern applies to
# which threshold) would be silently lost in the migration, so we translate
# it explicitly instead of just relying on the fallback.
_LEGACY_AGE_PATTERN_MAP = {
    "smooth": "bare", "light_specks": "outpost", "craters": "settlement", "deep_scarring": "metropolis",
}
def _migrate_legacy_age_pattern_names(style):
    stages = style.get("age_stages")
    if not stages:
        return
    for stage in stages:
        old = stage.get("pattern")
        if old in _LEGACY_AGE_PATTERN_MAP:
            stage["pattern"] = _LEGACY_AGE_PATTERN_MAP[old]

def save_style(style=None):
    global _style
    if style is not None:
        _style = style
    if _style is None:
        return False
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = STYLE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_style, f, indent=2)
        os.replace(tmp, STYLE_FILE)
        return True
    except OSError:
        return False

def reset_style_section(section):
    """Reset just one section (category_colors / age_stages / lastused_glow
    / rendering) back to its shipped default, leaving the rest of the user's
    customization untouched."""
    style = load_style()
    defaults = _default_style()
    if section not in defaults:
        return False, "unknown section"
    style[section] = defaults[section]
    save_style(style)
    return True, "reset"

# Curated extension -> category lookup. This is allowed to grow, and is a
# TAXONOMY concern (what category is a .docx?) — separate from STYLE (what
# color is the "document" category?). Anything not listed here gets guessed
# (via mimetypes) on first encounter and the guess is cached to disk so it
# only happens once per extension.
EXT_CATEGORY = {
    # documents
    "txt":"document","md":"document","pdf":"document","doc":"document","docx":"document",
    "odt":"document","rtf":"document","pages":"document","tex":"document","log":"data",
    # spreadsheets
    "xls":"spreadsheet","xlsx":"spreadsheet","csv":"spreadsheet","ods":"spreadsheet","numbers":"spreadsheet","tsv":"spreadsheet",
    # presentations
    "ppt":"presentation","pptx":"presentation","key":"presentation","odp":"presentation",
    # code / config / markup
    "py":"code","js":"code","ts":"code","jsx":"code","tsx":"code","html":"code","htm":"code","css":"code",
    "c":"code","cpp":"code","h":"code","hpp":"code","java":"code","cs":"code","go":"code","rs":"code",
    "rb":"code","php":"code","swift":"code","kt":"code","json":"code","yaml":"code","yml":"code",
    "xml":"code","toml":"code","ini":"code","cfg":"code","conf":"code","sql":"code","ps1":"code",
    "psm1":"code","vbs":"code","r":"code","lua":"code","ipynb":"code",
    # images
    "jpg":"image","jpeg":"image","png":"image","gif":"image","bmp":"image","svg":"image","webp":"image",
    "tiff":"image","tif":"image","ico":"image","psd":"image","ai":"image","eps":"image","heic":"image","raw":"image",
    # video
    "mp4":"video","mov":"video","mkv":"video","avi":"video","wmv":"video","flv":"video","webm":"video","m4v":"video",
    # audio
    "mp3":"audio","wav":"audio","flac":"audio","aac":"audio","ogg":"audio","wma":"audio","m4a":"audio",
    # archives
    "zip":"archive","tar":"archive","gz":"archive","7z":"archive","rar":"archive","bz2":"archive","xz":"archive","iso":"archive",
    # executables / installers / scripts
    "exe":"executable","msi":"executable","app":"executable","sh":"executable","bash":"executable",
    "bat":"executable","cmd":"executable","com":"executable","appimage":"executable","run":"executable",
    "pl":"executable","out":"executable","apk":"executable","deb":"executable",
    # shortcuts / system links
    "lnk":"shortcut","url":"shortcut","desktop":"shortcut","webloc":"shortcut",
    # fonts
    "ttf":"font","otf":"font","woff":"font","woff2":"font","eot":"font",
    # misc data / dlls / certs
    "dll":"data","so":"data","dylib":"data","db":"data","sqlite":"data","sqlite3":"data",
    "dat":"data","bak":"data","cache":"data","pem":"data","crt":"data","key_":"data","pub":"data","pcap":"data",
    "pt":"data","onnx":"data","model":"data",
}

_ext_cache = None
def _load_ext_cache():
    global _ext_cache
    if _ext_cache is None:
        try:
            with open(EXT_CACHE_FILE) as f:
                _ext_cache = json.load(f)
        except (FileNotFoundError, ValueError):
            _ext_cache = {}
    return _ext_cache

def _save_ext_cache():
    if _ext_cache is None:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = EXT_CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_ext_cache, f, indent=2, sort_keys=True)
        os.replace(tmp, EXT_CACHE_FILE)
    except OSError:
        pass

def _guess_category(ext):
    # cheap, dependency-free guess for an extension Bang has never seen
    # before, using Python's built-in MIME-type registry (which is itself
    # informed by the OS's real file-association database on Windows).
    import mimetypes
    mime, _ = mimetypes.guess_type("x." + ext)
    if not mime:
        return "other"
    top = mime.split("/")[0]
    if top == "text":
        return "document"
    if top == "image":
        return "image"
    if top == "video":
        return "video"
    if top == "audio":
        return "audio"
    if mime in ("application/zip","application/x-tar","application/gzip","application/x-7z-compressed",
                "application/x-rar-compressed","application/x-bzip2","application/x-xz"):
        return "archive"
    if mime in ("application/pdf","application/msword","application/rtf") or "document" in mime or "wordprocessing" in mime:
        return "document"
    if "spreadsheet" in mime or "excel" in mime:
        return "spreadsheet"
    if "presentation" in mime or "powerpoint" in mime:
        return "presentation"
    if mime in ("application/x-msdownload","application/x-executable","application/vnd.microsoft.portable-executable"):
        return "executable"
    if mime == "font" or "font" in mime:
        return "font"
    return "other"

_NEW_EXT_THIS_SESSION = set()  # tracked so we only write the cache file once at the end of scan(), not per-file

def category_of_ext(ext):
    if not ext:
        return "other"
    if ext in EXT_CATEGORY:
        return EXT_CATEGORY[ext]
    cache = _load_ext_cache()
    if ext in cache:
        return cache[ext]
    guess = _guess_category(ext)
    cache[ext] = guess
    _NEW_EXT_THIS_SESSION.add(ext)
    return guess

def _parse_rgb_string(s, fallback=(205,214,224)):
    if not s:
        return fallback
    try:
        nums = s[s.index("(")+1:s.index(")")].split(",")
        return (int(nums[0]), int(nums[1]), int(nums[2]))
    except (ValueError, IndexError):
        return fallback

def color_for_ext(ext):
    style = load_style()
    colors = style.get("category_colors", DEFAULT_CATEGORY_COLOR)
    category = category_of_ext(ext)
    s = colors.get(category) or colors.get("other") or DEFAULT_CATEGORY_COLOR["other"]
    return _parse_rgb_string(s)

def color_for_name(name):
    return color_for_ext(ext_of(name))

def ext_of(name):
    i = name.rfind(".")
    return name[i+1:].lower() if i > 0 else ""

def _normcase_path(p):
    # Windows paths are case-insensitive; normalize for set membership checks
    return os.path.normcase(os.path.abspath(p)) if IS_WINDOWS else p

_VOID_PATHS_NORM = { _normcase_path(p) for p in VOID_PATHS }

def is_void(path, name):
    if _normcase_path(path) in _VOID_PATHS_NORM:
        return True
    if IS_WINDOWS:
        # Windows hidden-file attribute is the real signal; dotfile naming is
        # a borrowed Unix convention but still common (git, editors), so honor both.
        if name.startswith("."):
            return True
        try:
            attrs = os.stat(path).st_file_attributes
            if attrs & stat.FILE_ATTRIBUTE_HIDDEN or attrs & stat.FILE_ATTRIBUTE_SYSTEM:
                return True
        except (OSError, AttributeError):
            pass
        return False
    if name.startswith("."):
        return True
    return False

_cache = None
def load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE) as f:
                _cache = json.load(f)
        except (FileNotFoundError, ValueError):
            _cache = {}
    return _cache

def save_cache():
    if _cache is None:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_cache, f)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass

def dir_mtime(path):
    try:
        return int(os.stat(path).st_mtime)
    except OSError:
        return 0

# Cloud-sync placeholder files (OneDrive Files On-Demand, Dropbox Smart Sync,
# Google Drive streaming) can make a single os.stat() call block for a long
# time — sometimes indefinitely — waiting on the sync client rather than
# actually touching disk. A plain `for` loop over scandir has no way to
# escape a single hung call, so every stat() in the recursive walk goes
# through this bounded wrapper instead: if it doesn't return within
# STAT_TIMEOUT seconds, treat that entry as unreadable and move on, rather
# than freezing the whole scan on one bad file.
STAT_TIMEOUT = 2.0
_stat_pool = _cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="bang-stat")

def _bounded_stat(entry, follow_symlinks=False):
    fut = _stat_pool.submit(entry.stat, follow_symlinks=follow_symlinks)
    try:
        return fut.result(timeout=STAT_TIMEOUT)
    except _cf.TimeoutError:
        return None  # the underlying call may still finish eventually in its
                      # own thread; we just stop waiting on it here
    except OSError:
        return None

def recursive_census(path):
    cache = load_cache()
    mt = dir_mtime(path)
    hit = cache.get(path)
    if hit and hit.get("mtime") == mt:
        return hit
    total = 0
    count = 0
    ext = {}
    visited = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    visited += 1
                    if visited > WALK_CAP:
                        break
                    try:
                        st = _bounded_stat(e)
                        if st is None:
                            continue  # unreadable or timed out — skip, don't hang
                        total += st.st_size
                        count += 1
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            x = ext_of(e.name)
                            if x:
                                ext[x] = ext.get(x, 0) + 1
                    except OSError:
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            continue
        if visited > WALK_CAP:
            break
    rec = {"mtime": mt, "weight": total, "count": count, "ext": ext}
    cache[path] = rec
    return rec

def blend_from_ext(ext):
    counts = {}
    for x, c in ext.items():
        col = color_for_ext(x)
        counts[col] = counts.get(col, 0) + c
    if not counts:
        return None
    tot = sum(counts.values())
    r=g=b=0.0
    for (cr,cg,cb), w in counts.items():
        f = w/tot
        r+=cr*f; g+=cg*f; b+=cb*f
    return "rgb(%d,%d,%d)" % (int(r),int(g),int(b))

def immediate_composition(path):
    specks = []
    try:
        with os.scandir(path) as it:
            n = 0
            for e in it:
                n += 1
                if n > 60:
                    break
                try:
                    if e.is_dir(follow_symlinks=False):
                        sub = recursive_census(e.path)
                        col = blend_from_ext(sub.get("ext", {})) or "rgb(143,208,255)"
                        specks.append({"kind": "folder", "color": col})
                    else:
                        cr,cg,cb = color_for_name(e.name)
                        specks.append({"kind": "file", "color": "rgb(%d,%d,%d)" % (cr,cg,cb)})
                except OSError:
                    continue
    except (PermissionError, FileNotFoundError, OSError):
        pass
    return specks

def pattern_for_age(created_ts, now=None):
    """Resolve which named age-stage (and its associated structure texture)
    a file falls into, based on style.json's current thresholds."""
    if now is None:
        now = time.time()
    age_days = max(0, (now - created_ts) / 86400.0)
    style = load_style()
    stages = style.get("age_stages") or DEFAULT_AGE_STAGES
    for stage in stages:
        if stage.get("max_days") is None or age_days <= stage["max_days"]:
            return stage.get("pattern", "bare"), stage.get("name", "bare"), round(age_days, 1)
    # shouldn't normally happen (last stage should have max_days: null), but
    # fall back to the last stage's pattern rather than crashing
    last = stages[-1] if stages else {"pattern": "bare"}
    return last.get("pattern", "bare"), last.get("name", "bare"), round(age_days, 1)

def stage_for_use(modified_ts, now=None):
    """Resolve which named use-stage (decay/overlay texture) a file falls
    into, based on idle time since last modified and style.json's current
    use_stages thresholds. Independent from glow_for_lastused below -- this
    drives the TEXTURE overlay (rust/cracks/derelict look), glow drives
    emissive brightness. A file can be visually bright (recently touched)
    while also showing an old decay overlay if your thresholds say so, though
    the default thresholds keep them roughly in sync."""
    if now is None:
        now = time.time()
    idle_days = max(0, (now - modified_ts) / 86400.0)
    style = load_style()
    stages = style.get("use_stages") or DEFAULT_USE_STAGES
    for stage in stages:
        if stage.get("max_days") is None or idle_days <= stage["max_days"]:
            return stage.get("pattern", "active"), stage.get("name", "active"), round(idle_days, 1)
    last = stages[-1] if stages else {"pattern": "active"}
    return last.get("pattern", "active"), last.get("name", "active"), round(idle_days, 1)

def glow_for_lastused(modified_ts, now=None):
    """Resolve a 0..1 brightness value from style.json's last-used curve.
    Recently modified = bright (max_glow); idle past window_days = dim
    (min_glow); linear interpolation between."""
    if now is None:
        now = time.time()
    idle_days = max(0, (now - modified_ts) / 86400.0)
    style = load_style()
    cfg = style.get("lastused_glow") or DEFAULT_LASTUSED_GLOW
    window = max(1, cfg.get("window_days", 60))
    max_g = cfg.get("max_glow", 1.0)
    min_g = cfg.get("min_glow", 0.15)
    f = min(1.0, idle_days / window)
    return round(max_g - (max_g - min_g) * f, 3), round(idle_days, 1)

def created_time(st):
    """Best-available creation time for a stat result. Windows reliably
    exposes real birthtime via st_ctime (which means creation time on
    Windows specifically, NOT on Linux/Mac where st_ctime means something
    else entirely). Most Linux filesystems don't expose birthtime through
    Python's stat at all, so this falls back to mtime there — meaning age
    and last-used will often read the same on Linux, but genuinely differ
    on Windows. That's a real, known platform difference, not a bug."""
    if IS_WINDOWS:
        return int(st.st_ctime)  # true creation time on Windows
    birthtime = getattr(st, "st_birthtime", None)  # macOS, some BSD-derived fs
    if birthtime is not None:
        return int(birthtime)
    return int(st.st_mtime)  # Linux fallback — most filesystems don't track birthtime

def scan(target):
    target = os.path.abspath(target)
    nodes = []
    now = time.time()
    try:
        entries = list(os.scandir(target))
    except PermissionError:
        return {"path": target, "error": "permission denied", "nodes": []}
    for e in entries:
        try:
            st = e.stat(follow_symlinks=False)
            is_dir = e.is_dir(follow_symlinks=False)
            full = os.path.join(target, e.name)
            pattern, stage_name, age_days = pattern_for_age(created_time(st), now)
            use_pattern, use_stage_name, idle_days_use = stage_for_use(st.st_mtime, now)
            glow, idle_days = glow_for_lastused(st.st_mtime, now)
            node = {
                "name": e.name,
                "path": full,
                "type": "planet" if is_dir else "satellite",
                "size": st.st_size,
                "modified": int(st.st_mtime),
                "created": created_time(st),
                "void": is_void(full, e.name),
                "agePattern": pattern,
                "ageStage": stage_name,
                "ageDays": age_days,
                "usePattern": use_pattern,
                "useStage": use_stage_name,
                "glow": glow,
                "idleDays": idle_days,
            }
            if not is_dir:
                node["category"] = category_of_ext(ext_of(e.name))
            if is_dir and not node["void"]:
                rec = recursive_census(full)
                node["weight"] = rec["weight"]
                node["count"] = rec["count"]
                tint = blend_from_ext(rec.get("ext", {}))
                if tint:
                    node["tint"] = tint
                node["specks"] = immediate_composition(full)
            elif not is_dir:
                # files get an explicit tint too now, computed server-side —
                # the frontend no longer needs its own extension/color table.
                cr,cg,cb = color_for_name(e.name)
                node["tint"] = "rgb(%d,%d,%d)" % (cr,cg,cb)
            nodes.append(node)
        except (PermissionError, FileNotFoundError, OSError):
            continue
    nodes.sort(key=lambda n: (n["type"] != "planet", n["name"].lower()))
    save_cache()
    if _NEW_EXT_THIS_SESSION:
        _save_ext_cache()
        _NEW_EXT_THIS_SESSION.clear()
    return {"path": target, "nodes": nodes}

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~")
    print(json.dumps(scan(target), indent=2))
