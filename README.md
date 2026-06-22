# Bang — Windows Setup

Bang is a spatial filesystem viewer: your folders become galaxies, your files
become evolving planets you can fly through.

## Easiest: just run Bang.exe

If you have a `Bang.exe` (built via `build.bat` — see below, or handed to you
already built), you don't need Python, pip, or a terminal at all. Just
double-click it. Skip straight to "Run" below.

The only requirement for the .exe path is Windows 10 or 11 with the built-in
Edge WebView2 runtime, which is already installed on virtually all current
Windows machines; if it's somehow missing, Windows will prompt to install it
automatically the first time Bang opens.

## Running from source instead (no .exe)

If you'd rather just run the Python directly without building an .exe:

### Requirements

- Python 3.9 or newer, installed from python.org (check "Add Python to PATH"
  during install)
- Windows 10 or 11 (uses the built-in Edge WebView2 runtime — already
  installed on virtually all current Windows machines; if missing, Windows
  will prompt to install it automatically the first time Bang opens)

### Install

Open Command Prompt or PowerShell in this folder and run:

    pip install -r requirements.txt

### Run

    python app.py

A window titled "Bang" will open — not a browser tab, an actual app window.

On first launch, you'll be asked to choose a folder. That folder becomes the
center of your universe ("Bang" — the origin). Everything inside it, and its
subfolders, renders as galaxies (folders) and planets (files). This choice is
remembered, so you won't be asked again on future launches.

## Files

| File | Purpose |
|---|---|
| `app.py` | Launches the app window. Run this one (or run `Bang.exe`, if you built it). |
| `server.py` | The backend — scans your files, serves the API, handles file operations. |
| `scan.py` | The filesystem scanner used by server.py. |
| `bang3d.html` | The 3D universe itself — everything you see and interact with. |
| `requirements.txt` | Python dependencies (pywebview). |
| `Bang.spec` | PyInstaller build configuration — used by build.bat, you shouldn't need to edit it. |
| `build.bat` | Double-click to build `dist\Bang.exe`. |
| `build_debug.bat` | Same, but keeps a console window open for diagnosing a build/launch problem. |

## Notes

- Write operations (create, rename, move, copy-into, and graveyard) are
  confined to inside your current origin folder — Bang won't create, modify,
  move, or copy *into* anything outside it, even if Void mode lets you
  browse out there. Copying *from* outside the origin into it is fine, since
  that doesn't touch anything outside.
- Opening a file from inside Bang uses Windows' normal "open with" behavior —
  it never runs anything as a script, it just hands the file to whatever
  program Windows already has associated with that file type.
- "Graveyard" (the action formerly called Delete) never deletes anything —
  it moves the item into a `.bang_graveyard` folder inside your chosen
  origin, so a misclick is always recoverable. That folder is hidden from
  normal browsing (same as any dotfolder) but you can still find it in
  Windows Explorer if you ever want to clear it out for good.
- System folders (`C:\Windows`, `C:\Program Files`, etc.) are hidden by
  default and protected from rename/delete/move, the same way Bang protects
  Linux system folders on the original Raspberry Pi build. The graveyard
  folder itself is protected the same way — it can't be renamed or moved.
- Your chosen origin folder, and the file-weight cache, are stored in
  `%USERPROFILE%\.bang\` — delete that folder if you ever want to reset
  Bang back to a clean first-launch state.
- To change your origin folder later, open Settings → Rendering and use
  "Change Origin Folder…" near the bottom. (You can also delete
  `%USERPROFILE%\.bang\config.json` and relaunch to get the first-run picker
  again, if you ever need to do it that way instead.)
