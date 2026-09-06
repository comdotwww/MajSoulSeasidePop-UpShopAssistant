# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: single-file windowed exe with templates & assets bundled.
# Build:  pyinstaller shop_assistant.spec --noconfirm
# Output: dist/ShopAssistant.exe

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),   # recognition template images
        ('assets', 'assets'),         # logo
        ('tools', 'tools'),           # calibrate script (run in-process when frozen)
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ShopAssistant',
    icon='assets/logo.ico',
    console=False,      # GUI app, no console window
    upx=False,          # UPX can trigger antivirus false positives
)
