#!/usr/bin/env python3
"""
velocity_cursor_x11.py

A velocity-reactive custom mouse cursor for X11/Xorg:
- Hides the ordinary X cursor.
- Draws a custom PyQt6 cursor overlay at the real mouse location.
- Cursor grows and stretches with velocity/acceleration.
- Cursor leans toward its movement direction.
- Overlay is transparent and click-through.

Install:
    pip install --user PyQt6 python-xlib Pillow

Run:
    python3 velocity_cursor_x11.py

Stop:
    Ctrl+C

Important:
    This requires an X11/Xorg desktop session, not Wayland.
"""

from __future__ import annotations

import math
import signal
import sys
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication, QWidget
from Xlib import X, display
from Xlib.ext import xfixes


# ───────────────────────── Appearance / responsiveness ───────────────────────

BASE_SIZE = 10          # Normal cursor size in pixels.
MAX_SCALE = 5.00        # Maximum cursor growth at speed.
MAX_STRETCH = 1      # Maximum elongation in movement direction.
MAX_TILT_DEG = 160.0     # Maximum clockwise/counter-clockwise lean.
POLL_MS = 15             # Update interval: 8 ms ≈ 125 FPS.
SMOOTHING = 0.24        # 0.0 = smooth/heavy, 1.0 = immediate/jittery.
ROTATE_OFFSET = 35     # Initial rotation offset (e.g. to point to the green dot)

# Velocity and acceleration sensitivity:
# Lower values make the effect appear sooner.
SPEED_FULL_SCALE = 800.0       # Pixels per second for maximum effect.
ACCEL_FULL_SCALE = 90000.0      # Pixels per second squared for max pop.
ACCEL_SCALE_BONUS = 0.20        # Extra size briefly added during a flick.


# ─────────────────────────── Math / cursor state ─────────────────────────────

@dataclass
class CursorShape:
    scale: float = 1.0
    angle_deg: float = 0.0
    stretch: float = 1.0
    speed: float = 0.0
    acceleration: float = 0.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lerp(current: float, target: float, amount: float) -> float:
    return current + (target - current) * amount


def lerp_angle(current: float, target: float, amount: float) -> float:
    """Interpolate via the shortest route around a circular angle."""
    delta = (target - current + 180.0) % 360.0 - 180.0
    return (current + delta * amount) % 360.0


class CursorPhysics:
    """Converts pointer position samples into visual cursor shape values."""

    def __init__(self) -> None:
        self.last_x: int | None = None
        self.last_y: int | None = None
        self.last_time: float | None = None
        self.last_speed = 0.0
        self.shape = CursorShape()

    def update(self, x: int, y: int) -> CursorShape:
        now = time.monotonic()

        # First sample establishes initial state only.
        if self.last_time is None:
            self.last_x = x
            self.last_y = y
            self.last_time = now
            return self.shape

        dt = max(now - self.last_time, 0.001)
        dx = x - self.last_x
        dy = y - self.last_y

        self.last_x = x
        self.last_y = y
        self.last_time = now

        speed = math.hypot(dx, dy) / dt
        acceleration = max(0.0, (speed - self.last_speed) / dt)
        self.last_speed = speed

        speed_amount = clamp(speed / SPEED_FULL_SCALE, 0.0, 1.0)
        accel_amount = clamp(acceleration / ACCEL_FULL_SCALE, 0.0, 1.0)

        # Larger on speed; a rapid acceleration produces a brief "pop".
        target_scale = 1.0 + speed_amount * (MAX_SCALE - 1.0)
        target_scale += accel_amount * ACCEL_SCALE_BONUS
        target_scale = clamp(target_scale, 1.0, MAX_SCALE)

        # Stretch in the direction the cursor is traveling.
        target_stretch = 1.0 + speed_amount * (MAX_STRETCH - 1.0)

        # Keep arrow recognisable: lean it toward motion, rather than
        # continually spinning the arrow through all 360 degrees.
        if speed > 15.0:
            # Movement direction in degrees:
            # 0 = right, 90 = down, 180/-180 = left, -90 = up
            direction = math.degrees(math.atan2(dy, dx))

            # Our arrow points up when angle_deg = 0.
            # To make the nose point along the movement direction,
            # we need to rotate the arrow so that "up" aligns with `direction`.
            # "Up" in movement-direction terms is -90°.
            # So the required rotation is:
            #   target_angle = direction - (-90°) = direction + 90°
            raw_angle = direction + 90.0

            # Normalize to (-180, 180]
            raw_angle = (raw_angle + 180.0) % 360.0 - 180.0

            # Optionally limit how far it can tilt away from straight-ahead.
            # If you want the nose to *always* point exactly along the movement,
            # skip the clamp and just use: target_angle = raw_angle
            target_angle = clamp(raw_angle, -MAX_TILT_DEG, MAX_TILT_DEG) + ROTATE_OFFSET

        else:
            target_angle = 0.0
            
        self.shape.scale = lerp(self.shape.scale, target_scale, SMOOTHING)
        self.shape.stretch = lerp(
            self.shape.stretch,
            target_stretch,
            SMOOTHING,
        )
        self.shape.angle_deg = lerp_angle(
            self.shape.angle_deg,
            target_angle,
            SMOOTHING,
        )
        self.shape.speed = speed
        self.shape.acceleration = acceleration

        return self.shape


# ────────────────────────── Cursor image generation ──────────────────────────

def make_dot(logical_size: int) -> Image.Image:
    """
    Create a small square with a 1×1 opaque green dot in the center.
    """
    size = max(3, logical_size)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    cx = size // 2
    cy = size // 2
    # Green dot at the actual pointer position
    draw.point((cx, cy), fill=(0, 255, 0, 255))

    return image

def make_arrow(logical_size: int) -> Image.Image:
    """
    Draw a vector-outline triangular spaceship inspired by Asteroids.

    Returns a transparent RGBA Pillow image. The ship points upward,
    so the cursor physics can rotate it toward movement.
    """
    # Oversized canvas prevents the outline from clipping during rotation.
    canvas = logical_size * 2
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    X = 20
    
    s = logical_size
    cx = canvas / 2
    cy = canvas / 2

    # Main triangular hull.
    # The nose points upward; the rear is open like the arcade ship.
    nose = (cx, cy - s * 1.15)
    left_rear = (cx - s * 0.95, cy + s * 0.90)
    right_rear = (cx + s * 0.95, cy + s * 0.90)

    # Small rear notch, suggesting the engine cutout.
    rear_center_top = (cx, cy + s * 0.35)
    rear_center_bottom = (cx, cy + s * 0.35)

    hull = [
        nose,
        right_rear,
        rear_center_bottom,
        rear_center_top,
        left_rear,
        nose,
    ]

    # Bright vector-style outline.
    line_width = max(2, round(logical_size * 0.10))
    draw.line(
        hull,
        fill=(255, 255, 255, 255),
        width=line_width,
        joint="curve",
    )

    # Optional cockpit line: a short internal stroke near the nose.
    cockpit_top = (cx, cy - s * 0.45)
    cockpit_bottom = (cx, cy + s * 0.10)
    draw.line(
        [cockpit_top, cockpit_bottom],
        fill=(255, 255, 255, 200),
        width=max(1, round(line_width * 0.55)),
    )

    return image


def transform_cursor(shape: CursorShape) -> Image.Image:
    """Apply size, stretch, and angle to the base arrow, then add a green dot at the tip."""
    logical_size = max(12, round(BASE_SIZE * shape.scale))
    arrow = make_arrow(logical_size)

    # Stretch in the direction of travel (horizontal in image space before rotation).
    stretched_width = max(1, round(arrow.width * shape.stretch))
    stretched_height = max(1, round(arrow.height * shape.stretch))
    arrow = arrow.resize(
        (stretched_width, stretched_height),
        Image.Resampling.LANCZOS,
    )

    # Rotate so the nose points along the movement direction.
    # Pillow rotates counterclockwise for positive angles.
    # Our angle_deg is defined such that positive = clockwise lean.
    arrow = arrow.rotate(
        -shape.angle_deg + ROTATE_OFFSET,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )

    # Compute hotspot in the same way as in tick().
    # This is where the tip / green dot will be.
    hotspot_x = round(BASE_SIZE * shape.scale * 0.35)
    hotspot_y = round(BASE_SIZE * shape.scale * 0.15)

    # Create a small green dot image.
    dot = make_dot(logical_size)  # 1×1 green pixel in a small canvas

    # Ensure the arrow canvas is large enough to place the dot.
    # We want the dot's center at (hotspot_x, hotspot_y) in the final image.
    dx = hotspot_x - dot.width // 2
    dy = hotspot_y - dot.height // 2

    min_width = dx + dot.width
    min_height = dy + dot.height
    if arrow.width < min_width or arrow.height < min_height:
        new_width = max(arrow.width, min_width)
        new_height = max(arrow.height, min_height)
        expanded = Image.new("RGBA", (new_width, new_height), (0, 0, 0, 0))
        expanded.paste(arrow, (0, 0))
        arrow = expanded

    # Paste the green dot so its center is exactly at the tip/hotspot.
    arrow.paste(dot, (dx, dy), dot)
    return arrow

# ───────────────────────────── PyQt overlay ──────────────────────────────────

class CursorOverlay(QWidget):
    """An always-on-top, transparent, non-interactive cursor window."""

    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
            | Qt.WindowType.WindowTransparentForInput
        )

        # The Qt window uses per-pixel alpha. Unpainted pixels show the
        # desktop beneath rather than an opaque rectangular background.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Defensive extra setting for Qt/widget-level event delivery.
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self._qimage: QImage | None = None

    def set_image(self, pil_image: Image.Image) -> None:
        """Convert Pillow RGBA data to an owned QImage."""
        rgba = pil_image.convert("RGBA")
        raw = rgba.tobytes("raw", "RGBA")

        # .copy() is crucial: it gives Qt memory ownership independent
        # of Pillow's temporary raw buffer.
        self._qimage = QImage(
            raw,
            rgba.width,
            rgba.height,
            QImage.Format.Format_RGBA8888,
        ).copy()

        self.resize(rgba.width, rgba.height)
        self.update()

    def paintEvent(self, event) -> None:
        if self._qimage is None:
            return

        painter = QPainter(self)
        painter.drawImage(0, 0, self._qimage)
        painter.end()


# ────────────────────────────── X11 controller ───────────────────────────────

class VelocityCursorX11:
    def __init__(self) -> None:
        self.running = True
        self.physics = CursorPhysics()

        # Connect to the active X server via DISPLAY.
        self.dpy = display.Display()
        self.root = self.dpy.screen().root

        # Install a blank cursor on the root window; the Qt overlay becomes
        # the visible pointer.
        # Ask the X server whether XFIXES is available.
        xfixes_version = self.dpy.xfixes_query_version()
        if xfixes_version.major_version < 2:
            raise RuntimeError("XFIXES >= 2.0 is required to hide the cursor.")

        # Hide the actual X cursor for this client, including over
        # applications whose windows would otherwise show their own cursor.
        self.root.xfixes_hide_cursor()

        # Also set a blank root cursor as a fallback for clients/windows
        # that inherit root cursor attributes.
        self.blank_cursor = self._create_transparent_xcursor()
        self.root.change_attributes(cursor=self.blank_cursor)

        self.dpy.sync()
        
        self.qt_app = QApplication(sys.argv)
        self.overlay = CursorOverlay()
        self.overlay.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(POLL_MS)

    def _create_transparent_xcursor(self):
        """
        Make an invisible X cursor from two 1-bit pixmaps.

        Python-Xlib's signature is:
            source.create_cursor(mask, foreground_rgb, background_rgb,
                                 hotspot_x, hotspot_y)
        """
        source = self.root.create_pixmap(1, 1, 1)
        mask = self.root.create_pixmap(1, 1, 1)

        source_gc = source.create_gc(foreground=0, background=0)
        mask_gc = mask.create_gc(foreground=0, background=0)

        # A zero mask means no visible cursor pixels.
        source.poly_fill_rectangle(source_gc, [(0, 0, 1, 1)])
        mask.poly_fill_rectangle(mask_gc, [(0, 0, 1, 1)])

        cursor = source.create_cursor(
            mask,
            (0, 0, 0),  # foreground RGB, irrelevant with an empty mask
            (0, 0, 0),  # background RGB, irrelevant with an empty mask
            0,
            0,
        )

        source_gc.free()
        mask_gc.free()
        source.free()
        mask.free()

        return cursor

    def pointer_position(self) -> tuple[int, int]:
        """Return the actual pointer location in root-window coordinates."""
        reply = self.root.query_pointer()
        return reply.root_x, reply.root_y

    def tick(self) -> None:
        """Read pointer motion and redraw/reposition the custom cursor."""
        if not self.running:
            return

        try:
            x, y = self.pointer_position()
            shape = self.physics.update(x, y)
            image = transform_cursor(shape)

            self.overlay.set_image(image)

            # The drawn arrow's nominal tip is near the image's upper-left
            # interior; preserve it around the true pointer position.
            hotspot_x = round(BASE_SIZE * shape.scale * 0.35)
            hotspot_y = round(BASE_SIZE * shape.scale * 0.15)

            self.overlay.move(x - hotspot_x, y - hotspot_y)

        except Exception as error:
            print(f"Cursor update failed: {error}", file=sys.stderr)
            self.stop()
            self.qt_app.quit()

    def stop(self, *_args) -> None:
        """Restore the normal X cursor and close resources."""
        if not self.running:
            return

        self.running = False

        try:
            self.timer.stop()
        except Exception:
            pass

        try:
            self.overlay.close()
        except Exception:
            pass

        try:
            # X.NONE restores regular cursor inheritance.
            self.root.change_attributes(cursor=X.NONE)
            self.dpy.sync()
        except Exception:
            pass

        try:
            self.blank_cursor.free()
        except Exception:
            pass

        try:
            self.dpy.close()
        except Exception:
            pass


# ───────────────────────────────── Entrypoint ────────────────────────────────

def main() -> None:
    # Fail early with a clearer message if DISPLAY is unavailable.
    if not __import__("os").environ.get("DISPLAY"):
        raise SystemExit(
            "No X11 DISPLAY detected. Run this from an X11/Xorg desktop session."
        )

    controller = VelocityCursorX11()

    def exit_cleanly(*_args) -> None:
        controller.stop()
        controller.qt_app.quit()

    signal.signal(signal.SIGINT, exit_cleanly)
    signal.signal(signal.SIGTERM, exit_cleanly)

    controller.qt_app.aboutToQuit.connect(controller.stop)
    sys.exit(controller.qt_app.exec())


if __name__ == "__main__":
    main()