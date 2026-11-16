from types import SimpleNamespace

from genshin_app.gui.window_geometry import (
    load_geometry,
    save_geometry,
    track_normal_geometry,
)


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in self.handlers:
            handler(*args)


def test_maximized_and_minimized_window_restore_the_normal_bounds(tmp_path):
    names = ("maximized", "minimized", "restored", "moved", "resized")
    window = SimpleNamespace(events=SimpleNamespace(**{name: Event() for name in names}),
                             native=SimpleNamespace(WindowState="Normal"),
                             x=-32000, y=-32000, width=1920, height=1080)
    original = {"x": 40, "y": 50, "width": 620, "height": 820}
    track_normal_geometry(window, original)
    window.events.moved.fire(70, 80)
    window.events.resized.fire(700, 800)
    window.native.WindowState = "Maximized"
    # A native resize arriving before the async maximize notification is safe.
    window.events.resized.fire(1920, 1080)
    window.events.maximized.fire()
    window.events.moved.fire(0, 0)
    window.native.WindowState = "Minimized"
    window.events.minimized.fire()
    window.events.moved.fire(-32000, -32000)
    path = tmp_path / "window.json"
    save_geometry(path, window)
    screen = SimpleNamespace(x=0, y=0, width=1920, height=1080)
    assert load_geometry(path, [screen]) == {"x": 70, "y": 80, "width": 700, "height": 800}
    window.native.WindowState = "Normal"
    window.events.restored.fire()
    window.events.moved.fire(100, 120)
    save_geometry(path, window)
    assert load_geometry(path, [screen])["x"] == 100


def test_retired_display_and_smaller_display_keep_titlebar_visible(tmp_path):
    path = tmp_path / "window.json"
    path.write_text('{"x":2200,"y":200,"width":1400,"height":900}')
    small = SimpleNamespace(x=0, y=0, width=900, height=600)
    assert load_geometry(path, [small]) == {"x": 0, "y": 0, "width": 900, "height": 600}
