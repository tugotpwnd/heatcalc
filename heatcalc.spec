# heatcalc.spec  — fast start (ONEDIR) + instant splash PNG
block_cipher = None

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.building.datastruct import Tree


# --- App entrypoint (module with your run()) ---
entry_script = "heatcalc/main.py"   # or "heatcalc/app.py" if that is your entry
pathex = []


datas = []


# ---- Runtime data files (CSVs, images, gifs) ----
datas += [
    ("heatcalc/data/*.csv", "heatcalc/data"),
    ("heatcalc/data/*.png", "heatcalc/data"),
    ("heatcalc/data/*.gif", "heatcalc/data"),
]

# ---- Assets (icons, PDFs, images) ----
datas += [
    ("heatcalc/assets/Logo.ico", "heatcalc/assets"),
    ("heatcalc/assets/coverpage.pdf", "heatcalc/assets"),
    ("heatcalc/assets/fonts/*.ttf", "heatcalc/assets/fonts"),
    ("heatcalc/assets/cable_install_type*.png", "heatcalc/assets"),
]


# ---- Assets ----
datas += [
    ("heatcalc/assets/Logo.ico", "heatcalc/assets"),
    ("heatcalc/assets/coverpage.pdf", "heatcalc/assets"),
    ("heatcalc/assets/fonts/*.ttf", "heatcalc/assets/fonts"),
]


# ---- Components CSV (only if still used) ----
datas += [
    ('heatcalc/data/components.csv', 'heatcalc/data'),
]


# (ReportLab often loads resources dynamically; if you use it at startup, keep this)
# If ReportLab/Matplotlib are only used later, leaving them out of hiddenimports is fine.
# from PyInstaller.utils.hooks import collect_all
# rl_binaries, rl_datas, rl_hidden = collect_all("reportlab")
# datas += rl_datas

# --- Keep PyQt5 minimal (platform + imageformats). PyInstaller auto-finds these, so
# we don't need to force-collect all of PyQt5 (which slows startup).
# If your app needs extra plugins, you can add them here.

a = Analysis(
    [entry_script],
    pathex=pathex,
    binaries=[],
    datas=datas,
    hiddenimports=[],     # keep empty; let PyInstaller analyze imports normally
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],     # add runtime hooks here if you truly need them
    excludes=[
        # Trim optional stuff you don't use at startup
        "tkinter",
        "pytest",
        "unittest",
        "numpy.tests",
        "PIL.tests",
    ],
    noarchive=False,      # keep as a single pyz (fine for ONEDIR)
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="heatcalc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,          # leave False for Windows stability
    upx=False,            # UPX can *slow* cold start on HDD/AV; leave off unless needed
    console=False,        # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='heatcalc/assets/Logo.ico',
    optimize=2,           # compile .pyc with -OO
    onefile=False,      # <-- make it a single .exe
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="heatcalc",
)
