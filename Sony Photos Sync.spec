# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('config_default.yaml', '.'), ('resources/icon.png', 'resources'), ('resources/rclone_arm64', 'resources/rclone_arm64'), ('resources/rclone_x86_64', 'resources/rclone_x86_64')],
    hiddenimports=['rumps', 'pyftpdlib.authorizers', 'pyftpdlib.handlers', 'watchdog.observers', 'watchdog.events', 'PIL', 'yaml', 'requests', 'AppKit', 'Foundation', 'objc', 'PyObjCTools', 'PyObjCTools.AppHelper', 'thumbnail_popup'],
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
    a.binaries,
    a.datas,
    [],
    name='Sony Photos Sync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
app = BUNDLE(
    exe,
    name='Sony Photos Sync.app',
    icon=None,
    bundle_identifier='com.sonyphotossync.app',
)
