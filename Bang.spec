# Bang.spec — PyInstaller build spec.
#
# Produces a single Bang.exe that bundles the Python interpreter, all
# dependencies (pywebview), and bang3d.html into one file. No terminal,
# no separately-installed Python, no `pip install` — just double-click.
#
# Build with:  pyinstaller Bang.spec
# (see build.bat for the one-click version of this command)

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    # bang3d.html ships alongside the .exe inside the bundle; server.py's
    # app_base_dir() knows to look in sys._MEIPASS (where PyInstaller
    # unpacks bundled data at runtime) instead of next to the script, which
    # is what makes this resolve correctly once frozen.
    datas=[('bang3d.html', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Bang',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # leave UPX off: it occasionally trips antivirus
                          # false-positives on Windows for no real benefit here
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=False hides the terminal window, which is the whole point of
    # this build. Flag for future debugging: older pywebview+PyInstaller
    # combinations had a documented issue where this exact setting caused
    # silent crashes on launch with zero error output (pywebview GitHub
    # issue #347) -- it's unclear whether that's still present in current
    # versions, and I have no way to test-build/run this on a real Windows
    # machine from here to confirm either way. If Bang.exe ever launches
    # and immediately disappears with no error, that bug is the first thing
    # to suspect -- rebuild with console=True temporarily (or run
    # `pyinstaller --debug=all Bang.spec`) to see what actually happens.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # add icon='bang.ico' here later if you make one
)
