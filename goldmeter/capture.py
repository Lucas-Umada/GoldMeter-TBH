"""Game window discovery and capture via X11 (works for XWayland too).

Proton/Wine games on a Wayland desktop normally run through XWayland, so
their windows are reachable from the X side. Capturing the window drawable
directly (XGetImage on the window, not the root) returns the window's own
content without compositor involvement and without portal permission
prompts — and keeps working while the window is partially covered.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image
from Xlib import X, display
from Xlib.error import BadDrawable, BadMatch, BadWindow


class CaptureError(Exception):
    pass


class WindowNotFound(CaptureError):
    pass


@dataclass
class WindowInfo:
    wid: int
    title: str
    width: int
    height: int


def _window_title(disp: display.Display, win) -> str:
    """Best-effort title: _NET_WM_NAME (UTF-8) first, then WM_NAME."""
    try:
        net_name = disp.intern_atom("_NET_WM_NAME")
        utf8 = disp.intern_atom("UTF8_STRING")
        prop = win.get_full_property(net_name, utf8)
        if prop and prop.value:
            value = prop.value
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        prop = win.get_full_property(disp.intern_atom("WM_NAME"), X.AnyPropertyType)
        if prop and prop.value:
            value = prop.value
            return value.decode("latin-1", "replace") if isinstance(value, bytes) else str(value)
    except (BadWindow, BadMatch):
        pass
    return ""


class GameCapture:
    """Finds the game window by title substring and grabs frames from it."""

    def __init__(self, title_substring: str):
        self.title_substring = title_substring
        self.disp = display.Display()
        self.window = None
        self.info: WindowInfo | None = None

    def list_windows(self) -> list[WindowInfo]:
        """All client windows the window manager knows about."""
        root = self.disp.screen().root
        result: list[WindowInfo] = []
        client_list = root.get_full_property(
            self.disp.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType
        )
        wids = list(client_list.value) if client_list else []
        if not wids:
            # WM without _NET_CLIENT_LIST: walk the tree one level deep.
            wids = [w.id for w in root.query_tree().children]
        for wid in wids:
            try:
                win = self.disp.create_resource_object("window", wid)
                title = _window_title(self.disp, win)
                if not title:
                    continue
                geo = win.get_geometry()
                result.append(WindowInfo(wid, title, geo.width, geo.height))
            except (BadWindow, BadMatch, BadDrawable):
                continue
        return result

    def find(self) -> WindowInfo:
        needle = self.title_substring.lower()
        candidates = [w for w in self.list_windows() if needle in w.title.lower()]
        if not candidates:
            self.window = None
            self.info = None
            raise WindowNotFound(
                f"No window with title containing {self.title_substring!r}"
            )
        # Prefer the largest match: launchers/notifications can share the name.
        best = max(candidates, key=lambda w: w.width * w.height)
        self.window = self.disp.create_resource_object("window", best.wid)
        self.info = best
        return best

    def grab(self) -> Image.Image:
        """Capture the current content of the game window as a PIL image."""
        if self.window is None:
            self.find()
        try:
            geo = self.window.get_geometry()
            raw = self.window.get_image(
                0, 0, geo.width, geo.height, X.ZPixmap, 0xFFFFFFFF
            )
        except (BadWindow, BadMatch, BadDrawable) as exc:
            # Window closed or resized mid-grab; force a re-find next time.
            self.window = None
            self.info = None
            raise WindowNotFound(f"Lost game window: {exc}") from exc

        data = raw.data
        if isinstance(data, str):
            data = data.encode("latin-1")

        if raw.depth in (24, 32):
            img = Image.frombytes("RGB", (geo.width, geo.height), data, "raw", "BGRX")
        elif raw.depth == 16:
            img = Image.frombytes("RGB", (geo.width, geo.height), data, "raw", "BGR;16")
        else:
            raise CaptureError(f"Unsupported window depth: {raw.depth}")
        return img
