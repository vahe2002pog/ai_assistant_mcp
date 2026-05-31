# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent
DEBUG_CONSOLE = os.environ.get("COMPASS_DEBUG_BUILD") == "1"


def tree(path: str):
    src = ROOT / path
    if not src.exists():
        return []
    return [(str(src), path)]


def safe_collect_submodules(package: str):
    try:
        return collect_submodules(package)
    except Exception:
        return []


def safe_collect_data_files(package: str):
    try:
        return collect_data_files(package)
    except Exception:
        return []


def safe_copy_metadata(package: str):
    try:
        return copy_metadata(package)
    except Exception:
        return []


datas = []
for folder in (
    "webui",
    "src",
    "browser_extension",
    "mcp_modules",
    "ui_automation",
    "voice",
):
    datas += tree(folder)

for package in (
    "certifi",
    "curl_cffi",
    "ddgs",
    "dotenv",
    "fastmcp",
    "fitz",
    "lxml",
    "mcp",
    "openai",
    "openpyxl",
    "PIL",
    "playwright",
    "pptx",
    "pystray",
    "scrapling",
    "sentence_transformers",
    "transformers",
    "webview",
):
    datas += safe_collect_data_files(package)

for package in (
    "aiohttp",
    "art",
    "azure-identity",
    "browserforge",
    "colorama",
    "comtypes",
    "curl_cffi",
    "ddgs",
    "fastmcp",
    "faiss-cpu",
    "langchain",
    "langchain-community",
    "langchain-core",
    "langchain-huggingface",
    "lxml",
    "mcp",
    "msal",
    "numpy",
    "omegaconf",
    "openai",
    "openpyxl",
    "pillow",
    "playwright",
    "pydantic",
    "pydantic_core",
    "pyautogui",
    "pycaw",
    "pymupdf",
    "pypdf",
    "pystray",
    "python-docx",
    "python-pptx",
    "pywebview",
    "pywinauto",
    "pywin32",
    "pyyaml",
    "requests",
    "typer",
    "rich",
    "scrapling",
    "Send2Trash",
    "sentence-transformers",
    "sounddevice",
    "soundfile",
    "starlette",
    "tensorflow",
    "torch",
    "transformers",
    "uiautomation",
    "uvicorn",
    "websockets",
):
    datas += safe_copy_metadata(package)

hiddenimports = []
hiddenimports += [
    "art",
    "azure.identity",
    "browserforge",
    "colorama",
    "comtypes",
    "curl_cffi",
    "ddgs",
    "docx",
    "fitz",
    "lxml",
    "msal",
    "numpy",
    "omegaconf",
    "openpyxl",
    "playwright",
    "pptx",
    "pyautogui",
    "pycaw",
    "pypdf",
    "pywinauto",
    "requests",
    "scrapling",
    "send2trash",
    "sounddevice",
    "soundfile",
    "tensorflow",
    "torch",
    "uiautomation",
    "yaml",
]
for package in (
    "aiohttp",
    "browser_extension",
    "database",
    "dotenv",
    "fastmcp",
    "mcp",
    "mcp_modules",
    "openai",
    "PIL",
    "psutil",
    "pystray",
    "sentence_transformers",
    "transformers",
    "langchain",
    "langchain_community",
    "langchain_core",
    "langchain_huggingface",
    "faiss",
    "pythoncom",
    "pywintypes",
    "ui_automation",
    "voice",
    "websockets",
    "webview",
    "win32api",
    "win32com",
    "win32com.client",
    "win32con",
    "win32gui",
    "win32process",
):
    hiddenimports += safe_collect_submodules(package)


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "transformers.cli.serving",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Compass",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=DEBUG_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "src" / "Icon_Compass.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Compass",
)
