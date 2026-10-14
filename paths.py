# -*- coding: utf-8 -*-
"""
Path management — works both when run normally (python app.py) and when packaged
as a PyInstaller .exe.

There are two kinds of paths:
  * RESOURCE_DIR : READ-ONLY packaged resources (templates, static, seed files).
                   When frozen this is PyInstaller's extraction folder
                   (sys._MEIPASS).
  * DATA_DIR     : WRITABLE persistent data (users.db, cache.db, logs). When
                   frozen this is %LOCALAPPDATA%\\Zabbix Report Studio, so the exe
                   can be installed in a read-only location (like Program Files)
                   while data stays writable and survives updates.
"""
import os
import sys
import shutil

_FROZEN = getattr(sys, "frozen", False)


def _resource_dir():
    if _FROZEN:
        # PyInstaller extraction folder (valid for both onedir and onefile)
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    if os.environ.get("ZRS_DATA_DIR"):
        # Explicit location, e.g. a mounted volume when running in Docker
        d = os.environ["ZRS_DATA_DIR"]
    elif _FROZEN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "Zabbix Report Studio")
    else:
        # In development, keep the old behaviour: files live in the project folder
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


RESOURCE_DIR = _resource_dir()
DATA_DIR = _data_dir()


def resource_path(*parts):
    """Full path to a packaged read-only resource."""
    return os.path.join(RESOURCE_DIR, *parts)


def data_path(*parts):
    """Full path to writable persistent data."""
    return os.path.join(DATA_DIR, *parts)


def seed_file(name):
    """
    On first run, copy the packaged 'seed' file (if any) into DATA_DIR once and
    return the DATA_DIR path. If it already exists, leave it untouched (preserves
    user data). In development RESOURCE_DIR == DATA_DIR, so no copy happens.
    """
    dst = data_path(name)
    if not os.path.exists(dst):
        src = resource_path(name)
        if os.path.abspath(src) != os.path.abspath(dst) and os.path.exists(src):
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass
    return dst
