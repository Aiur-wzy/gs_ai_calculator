"""Small presentation preference; never stores sessions or document content."""
from __future__ import annotations

import json


def visible_geometry(saved, screens):
    screens = [screen for screen in screens if screen.width > 0 and screen.height > 0]
    if not screens:
        return {"width": 620, "height": 820}
    row = saved if isinstance(saved, dict) else {}
    def number(key, default):
        value = row.get(key)
        return value if type(value) is int and abs(value) < 100000 else default
    x, y = number("x", screens[0].x + 32), number("y", screens[0].y + 32)
    width, height = max(400, number("width", 620)), max(420, number("height", 820))
    # Keep the display containing most of the old window, otherwise the first.
    def overlap(screen):
        return max(0, min(x+width, screen.x+screen.width)-max(x, screen.x)) * max(0, min(y+height, screen.y+screen.height)-max(y, screen.y))
    screen = max(screens, key=overlap)
    width, height = min(width, screen.width), min(height, screen.height)
    return {"x": min(max(x, screen.x), screen.x+screen.width-width),
            "y": min(max(y, screen.y), screen.y+screen.height-height), "width": width, "height": height}


def load_geometry(path, screens):
    try:
        saved = json.loads(path.read_text("utf-8")) if path and path.stat().st_size < 4096 else {}
    except (OSError, ValueError):
        saved = {}
    return visible_geometry(saved, screens)


def save_geometry(path, window):
    if path is None:
        return
    row = getattr(window, "_genshin_normal_geometry", None)
    if not isinstance(row, dict):
        row = {key: getattr(window, key) for key in ("x", "y", "width", "height")}
    if all(type(value) is int for value in row.values()) and row["width"] >= 400 and row["height"] >= 420 and row["x"] > -30000 and row["y"] > -30000:
        from genshin_app.database_transaction import atomic_json
        atomic_json(path, row)


def track_normal_geometry(window, initial):
    """Remember restored bounds, not the last maximized/minimized frame.

    pywebview delivers normal position/size through its public events. Native
    WinForms state is also checked because move/resize can be delivered while
    the asynchronous maximize notification is still queued.
    """
    normal = {key: initial.get(key) for key in ("x", "y", "width", "height")}
    if all(type(value) is int for value in normal.values()):
        window._genshin_normal_geometry = normal
    suspended = False

    def pause(*_):
        nonlocal suspended
        suspended = True

    def resume(*_):
        nonlocal suspended
        suspended = False

    def update(values):
        native = getattr(window, "native", None)
        state = getattr(native, "WindowState", None)
        # Other supported WebView backends use the public events only.
        if suspended or (state is not None and str(state) in {"Maximized", "Minimized"}):
            return
        if not all(type(value) is int for value in values.values()):
            return
        normal.update(values)
        if all(type(value) is int for value in normal.values()):
            window._genshin_normal_geometry = dict(normal)

    window.events.maximized += pause
    window.events.minimized += pause
    window.events.restored += resume
    window.events.moved += lambda x, y: update({"x": x, "y": y})
    window.events.resized += lambda width, height: update({"width": width, "height": height})
