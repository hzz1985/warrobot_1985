"""Hardware-level mouse/keyboard input for the broker panel.

The broker's Angular panel ignores programmatic clicks (`element.click()` from
page JS, and even some CDP-injected ones). `SendInput`/`mouse_event` goes through
the Windows input stack, so the page sees a real user click.
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def cursor_pos() -> tuple:
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return int(p.x), int(p.y)


def move(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def click(x: int = None, y: int = None, double: bool = False) -> tuple:
    if x is not None and y is not None:
        move(x, y)
        time.sleep(0.15)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if double:
        time.sleep(0.06)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.06)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    return cursor_pos()


def screen_point_of(rect: dict, win: dict) -> tuple:
    """Convert a DOM rect (page CSS pixels) to absolute screen coordinates.

    `win` comes from the page: {screenX, screenY, outerWidth, innerWidth,
    outerHeight, innerHeight, devicePixelRatio}.
    """
    dpr = float(win.get("devicePixelRatio") or 1.0)
    chrome_w = max(0.0, float(win.get("outerWidth", 0)) - float(win.get("innerWidth", 0)))
    chrome_h = max(0.0, float(win.get("outerHeight", 0)) - float(win.get("innerHeight", 0)))
    x = float(win.get("screenX", 0)) + chrome_w / 2 + (float(rect["x"]) + float(rect["width"]) / 2)
    y = float(win.get("screenY", 0)) + chrome_h - chrome_h / 2 + (float(rect["y"]) + float(rect["height"]) / 2)
    return int(round(x)), int(round(y))
