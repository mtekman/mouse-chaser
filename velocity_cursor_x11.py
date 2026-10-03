#!/usr/bin/env python3
"""
velocity_cursor_x11_inertia.py

A velocity-reactive vector mouse cursor for X11/Xorg:
- Hides the ordinary X cursor.
- Draws a vector spaceship cursor with PyQt6/QPainter.
- The green dot is locked to the real pointer position.
- The spaceship follows the green dot with inertia.
- Cursor grows and stretches with velocity/acceleration.
- Cursor rotates toward its movement direction.
- Both overlays are transparent and click-through.
- No Pillow dependency.

Install:
    pip install --user PyQt6 python-xlib

Run:
    python3 velocity_cursor_x11_inertia.py

Stop:
    Ctrl+C

Important:
    This requires an X11/Xorg desktop session, not Wayland.
"""

from __future__ import annotations

import math
import os
import signal
import sys
import time
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QWidget
from Xlib import X, display
from Xlib.ext import xfixes


# ───────────────────────── Appearance / responsiveness ───────────────────────

BASE_SIZE = 8              # Base vector cursor size in logical pixels.
MAX_SCALE = 2.0             # Maximum growth at speed.
MAX_STRETCH = 1.5           # 1.0 means no additional elongation.
MAX_TILT_DEG = 160.0        # Maximum clockwise/counter-clockwise lean.
POLL_MS = 15                # Update interval.
SMOOTHING = 0.24            # Shape smoothing.

# Spaceship inertia: spring-follows the real pointer.
# Higher SPRING_STIFFNESS = snappier catch-up.
# Higher SPRING_DAMPING = less overshoot / less wobble.
SPRING_STIFFNESS =    90.0
SPRING_DAMPING   =    18.0
MAX_SHIP_SPEED   = 120.0    # Pixels per second safety limit.
ANGULAR_STIFFNESS = 30.0
ANGULAR_DAMPING = 7.0
MAX_ANGULAR_VELOCITY_DEG = 420.0   # Degrees per second.
ANGULAR_SETTLE_RATE = 10.0 

# Lower values make the visual effects appear sooner.
SPEED_FULL_SCALE  =   800.0     # Pixels per second for maximum effect.
ACCEL_FULL_SCALE  = 90000.0     # Pixels per second squared for max pop.
ACCEL_SCALE_BONUS =     0.20    # Extra size briefly added during a flick.

ROTATE_OFFSET = 0        # Applied in all contexts
REST_ROTATE_OFFSET = -50  # Applied additionally below the speed threshold
SPEED_THRESHOLD = 5       # 

# Overlay canvas must contain the largest rotated vector cursor.
OVERLAY_SIZE = int(BASE_SIZE * MAX_SCALE * max(MAX_STRETCH, 1.0) * 6)

# Green pointer marker size.
GREEN_DOT_SIZE = 5

ANGULAR_STIFFNESS = 14.0
ANGULAR_DAMPING = 5.5
MAX_ANGULAR_VELOCITY_DEG = 320.0
ANGULAR_SETTLE_RATE = 3.0

with open("spaceship.svg", "br") as f:
    SPACESHIP_SVG = f.read()

# ─────────────────────────── Cursor state / physics ──────────────────────────

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
    """
    Converts pointer samples into a momentum-driven spaceship state.

    The spaceship has:
    - Linear momentum: position and velocity follow the pointer via a spring.
    - Angular momentum: heading changes through steering torque, rather than
      immediately matching the movement direction.
    """

    def __init__(self) -> None:
        self.pointer_x: int | None = None
        self.pointer_y: int | None = None
        self.last_time: float | None = None

        # Linear state.
        self.ship_x = 0.0
        self.ship_y = 0.0
        self.ship_vx = 0.0
        self.ship_vy = 0.0

        # Angular state.
        # The unrotated SVG spaceship points upward, which is -90 degrees in
        # standard screen/movement coordinates.
        self.heading_deg = -90.0
        self.angular_velocity_deg = 0.0

        self.last_speed = 0.0
        self.shape = CursorShape()

    def update(self, pointer_x: int, pointer_y: int) -> CursorShape:
        now = time.monotonic()

        if self.last_time is None:
            self.pointer_x = pointer_x
            self.pointer_y = pointer_y
            self.last_time = now

            self.ship_x = float(pointer_x)
            self.ship_y = float(pointer_y)

            return self.shape

        dt = max(now - self.last_time, 0.001)
        self.last_time = now

        self.pointer_x = pointer_x
        self.pointer_y = pointer_y

        # ── Linear spring physics ────────────────────────────────────────
        dx = pointer_x - self.ship_x
        dy = pointer_y - self.ship_y

        ax = SPRING_STIFFNESS * dx - SPRING_DAMPING * self.ship_vx
        ay = SPRING_STIFFNESS * dy - SPRING_DAMPING * self.ship_vy

        self.ship_vx += ax * dt
        self.ship_vy += ay * dt

        ship_speed = math.hypot(self.ship_vx, self.ship_vy)

        if ship_speed > MAX_SHIP_SPEED:
            factor = MAX_SHIP_SPEED / ship_speed
            self.ship_vx *= factor
            self.ship_vy *= factor
            ship_speed = MAX_SHIP_SPEED

        self.ship_x += self.ship_vx * dt
        self.ship_y += self.ship_vy * dt

        acceleration = max(0.0, (ship_speed - self.last_speed) / dt)
        self.last_speed = ship_speed

        # ── Visual scale and stretch ─────────────────────────────────────
        speed_amount = clamp(ship_speed / SPEED_FULL_SCALE, 0.0, 1.0)
        accel_amount = clamp(acceleration / ACCEL_FULL_SCALE, 0.0, 1.0)

        target_scale = 1.0 + speed_amount * (MAX_SCALE - 1.0)
        target_scale += accel_amount * ACCEL_SCALE_BONUS
        target_scale = clamp(target_scale, 1.0, MAX_SCALE)

        target_stretch = 1.0 + speed_amount * (MAX_STRETCH - 1.0)

        self.shape.scale = lerp(self.shape.scale, target_scale, SMOOTHING)
        self.shape.stretch = lerp(
            self.shape.stretch,
            target_stretch,
            SMOOTHING,
        )

        # ── Angular momentum / steering ──────────────────────────────────
        if ship_speed > SPEED_THRESHOLD:
            # Desired nose direction, based on the ship's actual velocity.
            movement_direction = math.degrees(
                math.atan2(self.ship_vy, self.ship_vx)
            )

            # The SVG nose points upward, i.e. -90 degrees.
            desired_heading = movement_direction + 90.0

            # Shortest signed angular error, in (-180, 180].
            heading_error = (
                desired_heading - self.heading_deg + 180.0
            ) % 360.0 - 180.0

            # Steering torque pulls angular velocity toward the desired
            # heading. Angular damping prevents endless spinning.
            angular_acceleration = (
                ANGULAR_STIFFNESS * heading_error
                - ANGULAR_DAMPING * self.angular_velocity_deg
            )

            self.angular_velocity_deg += angular_acceleration * dt

            # Limit turn rate so a sudden reversal cannot produce an
            # instantaneous 180-degree flip.
            self.angular_velocity_deg = clamp(
                self.angular_velocity_deg,
                -MAX_ANGULAR_VELOCITY_DEG,
                MAX_ANGULAR_VELOCITY_DEG,
            )
        else:
            # When nearly stopped, gently settle the heading and rotation.
            self.angular_velocity_deg *= math.exp(-ANGULAR_SETTLE_RATE * dt)

        # Integrate heading.
        self.heading_deg += self.angular_velocity_deg * dt
        self.heading_deg %= 360.0

        # Convert ship heading to the overlay's Qt rotation.
        #
        # The SVG's unrotated nose is upward. Qt rotation is clockwise.
        # We preserve the physical heading while applying the user's optional
        # visual offset.
        target_qt_angle = self.heading_deg + ROTATE_OFFSET

        self.shape.angle_deg = lerp_angle(
            self.shape.angle_deg,
            target_qt_angle,
            SMOOTHING,
        )

        self.shape.speed = ship_speed
        self.shape.acceleration = acceleration

        return self.shape

# ───────────────────────────── PyQt vector overlays ──────────────────────────

class CursorOverlay(QWidget):
    """An always-on-top, transparent, click-through vector spaceship window."""

    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
            | Qt.WindowType.WindowTransparentForInput
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self.setFixedSize(OVERLAY_SIZE, OVERLAY_SIZE)

        self.shape = CursorShape()
        self.svg_renderer = self._make_svg_renderer()

    def _make_svg_renderer(self) -> QSvgRenderer:
        """
        Create a renderer for the generic SVG/XML spaceship declaration.
    
        QSvgRenderer parses SVG XML and renders it through QPainter, preserving
        vector quality while the cursor is scaled, stretched, and rotated.
        """
        renderer = QSvgRenderer()
        renderer.load(SPACESHIP_SVG)
    
        if not renderer.isValid():
            raise RuntimeError("The embedded spaceship SVG could not be parsed.")
    
        return renderer

    def set_shape(self, shape: CursorShape) -> None:
        """Set the latest physics state and request a repaint."""
        self.shape = shape
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # The overlay is positioned so that its center is the simulated
        # spaceship location. The ship's local origin is therefore centered.
        center_x = OVERLAY_SIZE / 2.0
        center_y = OVERLAY_SIZE / 2.0

        painter.save()

        painter.translate(center_x, center_y)

        # Positive Qt rotation is clockwise in widget/screen coordinates.
        painter.rotate(self.shape.angle_deg)

        # Scale width uniformly, then elongate along the ship's forward axis.
        painter.scale(
            self.shape.scale,
            self.shape.scale * self.shape.stretch,
        )

        self._draw_ship(painter)

        painter.restore()
        painter.end()

    def _draw_ship(self, painter: QPainter) -> None:
        """
        Render the generic SVG spaceship through the current painter transform.
    
        The painter has already been translated to the simulated ship position,
        rotated toward its velocity, and scaled/stretched.
        """
        # SVG bounds from the declaration:
        # viewBox="-10 -2 20 24"
        svg_bounds = QRectF(-10.0, -2.0, 20.0, 24.0)
    
        # Render at the SVG's natural logical size. The active painter transform
        # scales and rotates it without rasterizing it in advance.
        self.svg_renderer.render(painter, svg_bounds)
        

class GreenDotOverlay(QWidget):
    """A tiny always-on-top overlay that marks the real pointer position."""

    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.BypassWindowManagerHint
            | Qt.WindowType.WindowTransparentForInput
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self.setFixedSize(GREEN_DOT_SIZE, GREEN_DOT_SIZE)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Bright green filled circle centered on the real pointer.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 255, 0, 255))
        painter.drawEllipse(2, 2, 5, 5)

        painter.end()


# ────────────────────────────── X11 controller ───────────────────────────────

class VelocityCursorX11:
    def __init__(self) -> None:
        self.running = True
        self.physics = CursorPhysics()

        self.dpy = display.Display()
        self.root = self.dpy.screen().root

        xfixes_version = self.dpy.xfixes_query_version()
        if xfixes_version.major_version < 2:
            raise RuntimeError("XFIXES >= 2.0 is required to hide the cursor.")

        self.root.xfixes_hide_cursor()

        self.blank_cursor = self._create_transparent_xcursor()
        self.root.change_attributes(cursor=self.blank_cursor)
        self.dpy.sync()

        self.qt_app = QApplication(sys.argv)

        self.overlay = CursorOverlay()
        self.overlay.show()

        self.green_dot = GreenDotOverlay()
        self.green_dot.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(POLL_MS)

    def _create_transparent_xcursor(self):
        """
        Create an invisible X cursor from two one-bit pixmaps.

        Python-Xlib signature:
            source.create_cursor(
                mask, foreground_rgb, background_rgb, hotspot_x, hotspot_y
            )
        """
        source = self.root.create_pixmap(1, 1, 1)
        mask = self.root.create_pixmap(1, 1, 1)

        source_gc = source.create_gc(foreground=0, background=0)
        mask_gc = mask.create_gc(foreground=0, background=0)

        # Zero mask bits make the cursor fully transparent.
        source.poly_fill_rectangle(source_gc, [(0, 0, 1, 1)])
        mask.poly_fill_rectangle(mask_gc, [(0, 0, 1, 1)])

        cursor = source.create_cursor(
            mask,
            (0, 0, 0),
            (0, 0, 0),
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
        """
        Read the real pointer, advance the spaceship simulation, then position
        the vector ship while keeping the green dot on the real pointer.
        """
        if not self.running:
            return

        try:
            pointer_x, pointer_y = self.pointer_position()
            shape = self.physics.update(pointer_x, pointer_y)

            # Move the ship overlay to its simulated position. Its center is
            # the ship's local origin/nose.
            ship_x = round(self.physics.ship_x)
            ship_y = round(self.physics.ship_y)

            self.overlay.move(
                ship_x - OVERLAY_SIZE // 2,
                ship_y - OVERLAY_SIZE // 2,
            )

            # Repaint the vector spaceship with the latest shape.
            self.overlay.set_shape(shape)

            # Center the green dot overlay on the actual pointer coordinate.
            self.green_dot.move(
                pointer_x - GREEN_DOT_SIZE // 2,
                pointer_y - GREEN_DOT_SIZE // 2,
            )

        except Exception as error:
            print(f"Cursor update failed: {error}", file=sys.stderr)
            self.stop()
            self.qt_app.quit()

    def stop(self, *_args) -> None:
        """Restore the normal X cursor and release resources."""
        if not self.running:
            return

        self.running = False

        try:
            self.timer.stop()
        except Exception:
            pass

        try:
            self.green_dot.close()
        except Exception:
            pass

        try:
            self.overlay.close()
        except Exception:
            pass

        try:
            # Restore ordinary cursor inheritance.
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
    if not os.environ.get("DISPLAY"):
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