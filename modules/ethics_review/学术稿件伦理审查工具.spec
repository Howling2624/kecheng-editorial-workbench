datas = [
    ("templates", "templates"),
    ("config.example.json", "."),
]
binaries = []
hiddenimports = [
    "config",
    "ethics_checkerV2",
    "flask",
    "flask_cors",
    "requests",
    "bs4",
    "pypdf",
    "docx",
]


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "pytest",
        "IPython",
        "pandas",
        "numpy",
        "scipy",
        "sklearn",
        "torch",
        "PIL",
        "matplotlib",
        "sphinx",
        "babel",
    ],
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
    name="学术稿件伦理审查工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
