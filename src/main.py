#!/usr/bin/env python3
"""
velocity_cursor_x11_bezier.py

A velocity-reactive vector mouse cursor for X11/Xorg.

Features:
- Hides the ordinary X cursor.
- Draws an SVG spaceship with PyQt6/QSvgRenderer.
- The green dot is locked to the real pointer position.
- The spaceship follows a smooth multi-segment cubic Bezier route.
- The ship preserves momentum between route replans.
- The ship's heading always follows the active Bezier tangent.
- Optional debugging overlay displays the Bezier route.
- Transparent, click-through overlays.
- No Pillow dependency.

Install:
    pip install --user PyQt6 python-xlib

Run:
    python3 velocity_cursor_x11_bezier.py

Stop:
    Ctrl+C

Important:
    Requires an X11/Xorg session, not Wayland.
"""

##from __future__ import annotations
from math import hypot
from os import environ
from sys import exit
from signal import signal, SIGINT, SIGTERM

from cursor_x11 import CursorX11
from config import load_config
from pathlib import Path

CONFIG, SPACESHIP_SVG = load_config(Path("config.yaml"))

def main() -> None:
    if not environ.get("DISPLAY"):
        raise SystemExit(
            "No X11 DISPLAY detected. Run this from an X11/Xorg desktop session."
        )
    controller = CursorX11(SPACESHIP_SVG,
                           CONFIG["OVERLAY_SIZE"], CONFIG["GREEN_DOT_SIZE"],
                           CONFIG["DEBUG_DRAW_BEZIER"], CONFIG["POLL_MS"])

    def exit_cleanly(*_args) -> None:
        controller.stop(); controller.qt_app.quit()

    signal(SIGINT, exit_cleanly); signal(SIGTERM, exit_cleanly)

    controller.qt_app.aboutToQuit.connect(controller.stop)
    exit(controller.qt_app.exec())

if __name__ == "__main__":
    main()