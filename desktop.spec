# -*- mode: python ; coding: utf-8 -*-
# Zabbix Report Studio — desktop application packaging configuration.
# Build:  pyinstaller desktop.spec --noconfirm

from PyInstaller.utils.hooks import collect_submodules

datas = [
    ('templates', 'templates'),   # Flask templates
    ('static', 'static'),         # favicon etc.
    # NOTE: no secrets are bundled. The app generates its own secret.key on first
    # run, and Zabbix servers are configured from the in-app "Servers" page.
]

hiddenimports = [
    'waitress',
    'sqlalchemy.dialects.sqlite',
]
# pywebview's Windows (EdgeChromium/WinForms) back ends are imported lazily
hiddenimports += collect_submodules('webview.platforms')


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Zabbix Report Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/favicon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Zabbix Report Studio',
)
