#!/usr/bin/env python3
"""
Bang — desktop launcher.

Starts server.py's HTTP server in a background thread, then opens a native
OS window (no browser tabs/URL bar/menus — just the window) pointed at it,
using pywebview. On Windows this renders via the system's built-in Edge
WebView2 runtime, which ships with Windows 10/11, so nothing extra needs
installing beyond `pip install pywebview`.

Run with:  python app.py
"""
import os, sys, threading, time

import server  # the existing Bang HTTP server (scan/open/fsop/home/setroot)

try:
    import webview
except ImportError:
    print("pywebview is not installed. Run:  pip install pywebview")
    print("On Windows, pywebview uses the built-in Edge WebView2 runtime (preinstalled on Win10/11).")
    sys.exit(1)


class Api:
    """Exposed to the page as window.pywebview.api.* — used for things the
    browser sandbox can't do itself, like a native folder picker."""

    def pick_folder(self):
        # native OS folder-selection dialog; returns a path string or None if
        # the user cancels. pywebview returns a tuple/list on some platforms,
        # a single string on others, so normalize defensively.
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return result[0] if result else None
        return result

    def set_window(self, window):
        self._window = window


def start_server():
    httpd = server.ThreadingHTTPServer(("127.0.0.1", server.PORT), server.BangHandler)
    httpd.serve_forever()


def wait_for_server(timeout=5.0):
    # don't open the window until the HTTP server is actually accepting
    # connections, or the window will flash a connection-refused page first.
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", server.PORT), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def main():
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    wait_for_server()

    api = Api()
    window = webview.create_window(
        "Bang",
        f"http://127.0.0.1:{server.PORT}/bang3d.html",
        width=1280, height=800, min_size=(800, 560),
        background_color="#04050a",
        js_api=api,
    )
    api.set_window(window)
    # gui="edgechromium" is the default/auto-detected engine on Windows
    # (Edge WebView2); left unset so pywebview picks the right backend per OS.
    webview.start()


if __name__ == "__main__":
    main()
