# -*- mode: python ; coding: utf-8 -*-
import sys
import os

# 获取 PyAV 的 FFmpeg 库
def get_av_libs():
    try:
        import av
        av_path = os.path.dirname(av.__file__)
        libs = []
        
        if sys.platform == 'win32':
            lib_ext = '.dll'
        elif sys.platform == 'darwin':
            lib_ext = '.dylib'
        else:  # Linux
            lib_ext = '.so'
        
        # 遍历 av 包目录查找动态库
        for root, dirs, files in os.walk(av_path):
            for file in files:
                if file.endswith(lib_ext) or '.so.' in file:
                    full_path = os.path.join(root, file)
                    libs.append((full_path, '.'))
        
        return libs
    except ImportError:
        return []

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=get_av_libs(),  # ← 添加 PyAV 的二进制文件
    datas=[],
    hiddenimports=[
        'av',
        'av.audio',
        'av.container',
        'av.codec',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5.QtBluetooth',
        'PyQt5.QtDesigner',
        'PyQt5.QtLocation',
        'PyQt5.QtMultimedia',
        'PyQt5.QtMultimediaWidgets',
        'PyQt5.QtNfc',
        'PyQt5.QtPositioning',
        'PyQt5.QtQml',
        'PyQt5.QtQuick',
        'PyQt5.QtSensors',
        'PyQt5.QtSerialPort',
        'PyQt5.QtSql',
        'PyQt5.QtSvg',
        'PyQt5.QtTest',
        'PyQt5.QtWebChannel',
        'PyQt5.QtWebEngineCore',
        'PyQt5.QtWebEngineWidgets',
        'PyQt5.QtWebSockets',
        'PyQt5.QtXml',
        'PyQt5.Qsci'
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
    name='audiohub',
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
    icon=None,
)

# macOS .app 打包
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='AudioHub.app',
        icon=None,
        bundle_identifier='com.yourname.audiohub',
        info_plist={
            'NSHighResolutionCapable': 'True',
            'LSBackgroundOnly': 'False',
        },
    )
