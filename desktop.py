# -*- coding: utf-8 -*-
"""
Zabbix Report Studio — desktop application launcher.

Runs the Flask server in the background and opens the portal in a native desktop
window (its own application window instead of a browser tab). Closing the window
shuts the application down.

Usage:
    pythonw desktop.py      (no console, behaves like an app)  <- run-app.bat uses this
    python  desktop.py      (with console, for debugging)

For website (browser) mode, run instead:  python app.py
"""
import os
import sys
import time
import socket
import threading

HOST = "127.0.0.1"
PORT = 5050
URL = f"http://{HOST}:{PORT}"
import paths
_BASE_DIR = paths.DATA_DIR

# Desktop-mode flag: when app.py sees this it injects a custom title bar on the
# SERVER side (as HTML) into every page, so pywebview does not need to inject JS
# via evaluate_js — that injection used to cause freezes.
os.environ["ZRS_DESKTOP"] = "1"


def _server_up(timeout=0.6):
    """Is the server up? (tries to connect to the port)"""
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_server(timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        if _server_up():
            return True
        time.sleep(0.3)
    return False


def _run_flask():
    import app as A
    A.create_admin()
    # In desktop mode Flask's development server (Werkzeug) can stall and freeze
    # under concurrent requests (ACK progress polling + Zabbix calls). Use the
    # production WSGI server 'waitress' instead.
    try:
        from waitress import serve
        serve(A.app, host=HOST, port=PORT, threads=12,
              channel_timeout=600, connection_limit=200, ident="ZabbixReportStudio")
    except ImportError:
        _log("waitress not found — falling back to Werkzeug (pip install waitress).")
        A.app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


def _log(msg):
    """Under pythonw there is no console, so also write errors to a file."""
    try:
        with open(os.path.join(_BASE_DIR, "desktop.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass
    print(msg)


def main():
    # Start the server in the background if it is not already running
    if not _server_up():
        threading.Thread(target=_run_flask, daemon=True).start()
        if not _wait_server():
            _log("ERROR: server failed to start (port 5050).")
            sys.exit(1)

    # Desktop window (pywebview + the built-in Edge WebView2 on Windows)
    try:
        import webview

        class WindowApi:
            """The custom title bar buttons control the window from here.

            IMPORTANT: pywebview serializes the PUBLIC (non-underscore) attributes
            of the js_api object to the JS side. If we keep the pywebview Window
            object public (self.window), pywebview walks into the
            window.native.DefaultForeColor (.NET Color) chain during serialization
            and hits infinite recursion -> 'maximum recursion depth exceeded' spam
            and stutter. That is why we keep the reference underscored (_window);
            pywebview then skips it.
            """
            def __init__(self):
                self._window = None
                self._maximized = False

            def minimize(self):
                try:
                    self._window.minimize()
                except Exception as e:
                    _log(f"minimize error: {e}")

            def toggle_maximize(self):
                try:
                    if self._maximized:
                        self._window.restore()
                    else:
                        self._window.maximize()
                    self._maximized = not self._maximized
                except Exception as e:
                    _log(f"maximize error: {e}")

            def close(self):
                try:
                    self._window.destroy()
                except Exception as e:
                    _log(f"close error: {e}")

        api = WindowApi()
        window = webview.create_window(
            "Zabbix Report Studio",
            URL,
            width=1440,
            height=900,
            min_size=(1024, 640),
            frameless=True,        # remove the native (white) title bar
            easy_drag=False,       # drag only from the title bar
            background_color="#100e0a",
            js_api=api,
        )
        api._window = window

        # NOTE: the title bar is now added server-side (app.py after_request) to
        # every page; we do not inject JS here (evaluate_js caused freezes). The
        # buttons work through window.pywebview.api.
        webview.start()  # returns when the window is closed -> app exits
        return
    except ImportError:
        _log("pywebview not found — opening in browser. Install: pip install pywebview")
    except Exception as e:
        _log(f"Could not open desktop window ({e}) — opening in browser.")

    # Fallback: if the desktop window cannot open, open in the browser and keep
    # the server running
    import webbrowser
    webbrowser.open(URL)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
