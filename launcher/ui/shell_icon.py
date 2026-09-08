"""Icons for things that are not files.

A Store application has no path on disk, only an identifier such as
`Microsoft.WindowsCalculator_8wekyb3d8bbwe!App`. Handing that to
`QFileIconProvider` gives the blank document placeholder, because there is no
file for the shell to look up: measured at 5 distinct colours against 168 for
the real Calculator artwork.

The shell can resolve it, just not as a path. `SHParseDisplayName` turns
`shell:AppsFolder\\<identifier>` into a namespace item, and `SHGetFileInfoW`
returns an icon handle for it. Qt 6 removed `QPixmap.fromWinHICON`, so the
handle's pixels are copied out by hand.

Everything here fails quietly. A missing icon is a placeholder glyph, never an
error, and every handle is released on all paths because the launcher is meant
to run all day.
"""

import ctypes
from ctypes import wintypes

from PySide6.QtGui import QImage, QPixmap

# Anything addressed through the shell namespace rather than the filesystem.
SHELL_PREFIX = "shell:"

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
ole32 = ctypes.windll.ole32


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class _BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


SHGFI_ICON = 0x000000100
SHGFI_PIDL = 0x000000008
SHGFI_LARGEICON = 0x000000000
DIB_RGB_COLORS = 0
BI_RGB = 0

# Without explicit signatures ctypes assumes a C int for every handle, which
# overflows on 64 bit Windows where a handle is pointer sized. The first
# attempt raised "int too long to convert" from DeleteObject.
user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(_ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
shell32.SHGetFileInfoW.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_SHFILEINFOW),
    wintypes.UINT, wintypes.UINT,
]
shell32.SHGetFileInfoW.restype = ctypes.c_void_p
shell32.SHParseDisplayName.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
]
shell32.SHParseDisplayName.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]


def is_shell_path(target: str) -> bool:
    """True for something the shell addresses by name rather than by path."""
    return target.startswith(SHELL_PREFIX)


def _image_from_hicon(hicon) -> QImage:
    """Copy an icon handle's pixels into a QImage.

    Qt 6 dropped QPixmap.fromWinHICON, so the colour bitmap is read out with
    GetDIBits. Both bitmaps the icon owns are deleted before returning.
    """
    info = _ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return QImage()

    try:
        bitmap = _BITMAP()
        gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(_BITMAP), ctypes.byref(bitmap))
        width, height = bitmap.bmWidth, bitmap.bmHeight
        if width <= 0 or height <= 0:
            return QImage()

        header = _BITMAPINFO()
        header.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.bmiHeader.biWidth = width
        header.bmiHeader.biHeight = -height        # negative means top down
        header.bmiHeader.biPlanes = 1
        header.bmiHeader.biBitCount = 32
        header.bmiHeader.biCompression = BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        device = user32.GetDC(None)
        try:
            got = gdi32.GetDIBits(
                device, info.hbmColor, 0, height, buffer,
                ctypes.byref(header), DIB_RGB_COLORS,
            )
        finally:
            user32.ReleaseDC(None, device)

        if not got:
            return QImage()

        # Windows returns BGRA, which is what Format_ARGB32 expects on a little
        # endian machine. copy() detaches from the temporary buffer.
        return QImage(bytes(buffer), width, height, QImage.Format_ARGB32).copy()
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)


def pixmap_for_shell_path(target: str, size: int) -> QPixmap:
    """Return the shell's icon for a namespace item, or a null pixmap.

    `target` is something like `shell:AppsFolder\\<identifier>`.
    """
    pidl = ctypes.c_void_p()
    try:
        hresult = shell32.SHParseDisplayName(
            target, None, ctypes.byref(pidl), 0, None
        )
    except Exception:
        return QPixmap()

    if hresult != 0 or not pidl:
        return QPixmap()

    try:
        info = _SHFILEINFOW()
        if not shell32.SHGetFileInfoW(
            pidl, 0, ctypes.byref(info), ctypes.sizeof(info),
            SHGFI_PIDL | SHGFI_ICON | SHGFI_LARGEICON,
        ):
            return QPixmap()
        if not info.hIcon:
            return QPixmap()

        try:
            image = _image_from_hicon(info.hIcon)
        finally:
            user32.DestroyIcon(info.hIcon)

        if image.isNull():
            return QPixmap()

        pixmap = QPixmap.fromImage(image)
        if pixmap.width() != size or pixmap.height() != size:
            from PySide6.QtCore import Qt
            pixmap = pixmap.scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        return pixmap
    except Exception:
        return QPixmap()
    finally:
        ole32.CoTaskMemFree(pidl)
