"""Game window discovery and capture via the Win32 API (ctypes, no deps).

Two capture paths, picked automatically on the first grab:

- PrintWindow with PW_RENDERFULLCONTENT asks the window to render its own
  content into our bitmap. It keeps working while the window is partially
  covered, which is the preferred path.
- Some hardware-rendered game windows hand PrintWindow a black frame. When
  the first PrintWindow result is a single flat color, we fall back to
  BitBlt-ing the window's client rectangle straight off the screen (which
  requires the window to be visible/unobstructed).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# Make client rects real pixels, not DPI-virtualized ones.
try:
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # per-monitor
except OSError:
    user32.SetProcessDPIAware()

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class CaptureError(Exception):
    pass


class WindowNotFound(CaptureError):
    pass


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    width: int
    height: int


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _client_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return (0, 0)
    return (rect.right, rect.bottom)


def _is_flat(img: Image.Image) -> bool:
    """True when the whole image is one single color (black frame etc.)."""
    return all(lo == hi for lo, hi in img.getextrema())


class GameCapture:
    """Finds the game window by title substring and grabs frames from it."""

    def __init__(self, title_substring: str):
        self.title_substring = title_substring
        self.hwnd: int | None = None
        self.info: WindowInfo | None = None
        # None = undecided, False = PrintWindow works, True = screen BitBlt.
        self._use_screen: bool | None = None

    def list_windows(self) -> list[WindowInfo]:
        """All visible top-level windows that have a title."""
        result: list[WindowInfo] = []

        @_WNDENUMPROC
        def on_window(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                title = _window_title(hwnd)
                if title:
                    w, h = _client_size(hwnd)
                    result.append(WindowInfo(hwnd, title, w, h))
            return True

        user32.EnumWindows(on_window, 0)
        return result

    def find(self) -> WindowInfo:
        needle = self.title_substring.lower()
        candidates = [w for w in self.list_windows() if needle in w.title.lower()]
        if not candidates:
            self.hwnd = None
            self.info = None
            raise WindowNotFound(
                f"No window with title containing {self.title_substring!r}"
            )
        # Prefer the largest match: launchers/notifications can share the name.
        best = max(candidates, key=lambda w: w.width * w.height)
        self.hwnd = best.hwnd
        self.info = best
        return best

    def grab(self) -> Image.Image:
        """Capture the current content of the game window as a PIL image."""
        if self.hwnd is None:
            self.find()
        hwnd = self.hwnd
        if not user32.IsWindow(hwnd):
            self.hwnd = None
            self.info = None
            raise WindowNotFound("Game window was closed")
        if user32.IsIconic(hwnd):
            raise CaptureError("Game window is minimized — restore it")
        width, height = _client_size(hwnd)
        if width < 4 or height < 4:
            raise CaptureError(f"Game window has no client area ({width}x{height})")

        if self._use_screen is None:
            try:
                img = self._grab_into(hwnd, width, height, use_screen=False)
            except CaptureError:
                img = None
            if img is not None and not _is_flat(img):
                self._use_screen = False
                return img
            screen_img = self._grab_into(hwnd, width, height, use_screen=True)
            if not _is_flat(screen_img):
                self._use_screen = True
                return screen_img
            # Both flat: window may genuinely be blank — stay undecided.
            return img if img is not None else screen_img

        return self._grab_into(hwnd, width, height, use_screen=self._use_screen)

    def _grab_into(self, hwnd: int, width: int, height: int, use_screen: bool) -> Image.Image:
        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        old = gdi32.SelectObject(hdc_mem, hbmp)
        try:
            if use_screen:
                origin = wintypes.POINT(0, 0)
                user32.ClientToScreen(hwnd, ctypes.byref(origin))
                if not gdi32.BitBlt(hdc_mem, 0, 0, width, height,
                                    hdc_screen, origin.x, origin.y, SRCCOPY):
                    raise CaptureError("BitBlt failed")
            else:
                if not user32.PrintWindow(hwnd, hdc_mem,
                                          PW_CLIENTONLY | PW_RENDERFULLCONTENT):
                    raise CaptureError("PrintWindow failed")

            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height  # top-down rows
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            buf = ctypes.create_string_buffer(width * height * 4)
            if not gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf,
                                   ctypes.byref(bmi), DIB_RGB_COLORS):
                raise CaptureError("GetDIBits failed")
            return Image.frombuffer("RGB", (width, height), buf, "raw", "BGRX", 0, 1)
        finally:
            gdi32.SelectObject(hdc_mem, old)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
