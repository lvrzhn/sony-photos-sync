"""
py2app setup script for Sony Photos Sync.

Build:
    python3 setup_py2app.py py2app

Dev mode (alias, uses source files):
    python3 setup_py2app.py py2app -A
"""

import sys
sys.setrecursionlimit(5000)

from setuptools import setup

APP = ["app.py"]

OPTIONS = {
    "argv_emulation": False,
    # "iconfile": "resources/app_icon.icns",  # Add custom .icns later
    "plist": {
        "CFBundleName": "Sony Photos Sync",
        "CFBundleDisplayName": "Sony Photos Sync",
        "CFBundleIdentifier": "com.sonyphotossync.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,  # Menu bar only, no Dock icon
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
    "includes": [
        "rumps",
        "pyftpdlib",
        "pyftpdlib.authorizers",
        "pyftpdlib.handlers",
        "pyftpdlib.servers",
        "watchdog",
        "watchdog.observers",
        "watchdog.events",
        "yaml",
        "PIL",
        "requests",
        "sqlite3",
        "configparser",
    ],
    "packages": [
        "pyftpdlib",
        "watchdog",
        "PIL",
        "requests",
    ],
    "resources": [
        "config_default.yaml",
        "resources/icon.png",
    ],
}

setup(
    app=APP,
    name="Sony Photos Sync",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
