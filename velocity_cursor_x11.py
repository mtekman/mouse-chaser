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

from __future__ import annotations

import math
import os
import signal
import sys
import time
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QWidget
from Xlib import X, display
from Xlib.ext import xfixes


# ───────────────────────── Appearance / responsiveness ───────────────────────

# Visual response.
VIZ_SIZE_BASE = 10
VIZ_ANGLE_OFFSET = 0
VIZ_SPEED_MULT = 1.0 / 800.0
VIZ_ACCEL_MULT = 1.0 / 90000.0
VIZ_SCALE_MAX = 2
VIZ_ELONG_MAX = 0.3
SCALE_ACCEL_MULT = 0.20
VIZ_SHAPE_SMOOTH = 0.4

# Linear motion.
LIN_SPEED_MAX = 700
LIN_SPEED_MIN = 500
LIN_SPEED_DRAG = 1.2

# Multi-segment Bezier route.
DEBUG_DRAW_BEZIER = True
BEZIER_SEGMENT_COUNT = 1                ## This is dynamically calcualted.
BEZIER_REPLAN_DISTANCE = 100.0          ## How far to move before recalculating a curve
BEZIER_WAYPOINT_CURVE_FRACTION = 0.1    ## 0 less curvy, high is more S-curvy. 1 is crazy. 0.1 feels good
BEZIER_START_HANDLE_FRACTION = 10       ## High = more drift with broader turns. 10 gives nice looser feel
BEZIER_START_HANDLE_MAX = 180.0         ## Stops fast movement from creating massive cruve. 180 is a good value.
BEZIER_END_HANDLE_FRACTION = 0.8        ## How strong current momentum carries into new route. Higher = more drift
BEZIER_END_HANDLE_MAX = 300             ## Max runway to ease into target. Low = tighter, high = broader, graceful
BEZIER_MIN_SPEED = 10 * LIN_SPEED_MAX     ## Min speed to maintain along route
BEZIER_ACCELERATION = 100            ## Rate at which to meet LIN_SPEED_MAX
BEZIER_LENGTH_SAMPLES = 10               ## Number of samples to estimate Bezier

# Dead zone around the green dot.
PHYS_DEAD_ZONE = 100  ## Do nothing until dot moves away

# Force a route recalculation after the pointer is roughly stationary
# for this many consecutive update ticks.
POLL_MS = 8
STATIONARY_TICKS_REQUIRED = 10
STATIONARY_RADIUS = 10
STATIONARY_RESET_RADIUS = 20

# Overlay sizing.
OVERLAY_SIZE = int(VIZ_SIZE_BASE * VIZ_SCALE_MAX * max(VIZ_ELONG_MAX, 1.0) * 6)
GREEN_DOT_SIZE = 12

with open("spaceship.svg", "br") as f:
    SPACESHIP_SVG = f.read()

screen_dist = math.hypot(2560, 1440)
# ─────────────────────────────── Math helpers ────────────────────────────────

BezierSegment = tuple[
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lerp(current: float, target: float, amount: float) -> float:
    return current + (target - current) * amount


def lerp_angle(current: float, target: float, amount: float) -> float:
    """Interpolate through the shortest circular route."""
    delta = (target - current + 180.0) % 360.0 - 180.0
    return (current + delta * amount) % 360.0


@dataclass
class CursorShape:
    scale: float = 1.0
    angle_deg: float = 0.0
    stretch: float = 1.0
    speed: float = 0.0
    acceleration: float = 0.0


# ─────────────────────────── Bezier flight physics ───────────────────────────

class CursorPhysics:
    """
    Moves a spaceship along a smooth multi-segment cubic Bezier route.

    The green dot is the navigation target. When the target moves materially,
    a new route is generated from the ship's current position and velocity.
    This preserves momentum while producing gentle curved intercepts.
    """

    def __init__(self) -> None:
        self.pointer_x: int | None = None
        self.pointer_y: int | None = None
        self.last_time: float | None = None

        # Ship state.
        self.ship_x = 0.0
        self.ship_y = 0.0
        self.ship_vx = 0.0
        self.ship_vy = 0.0

        # The unrotated SVG spaceship points upward, corresponding to -90
        # degrees in screen/movement coordinates.
        self.heading_deg = -90.0

        # Active route.
        self.bezier_segments: list[BezierSegment] = []
        self.segment_lengths: list[float] = []
        self.total_length = 0.0

        # Distance already traveled along the current route.
        self.traveled_distance = 0.0

        self.last_speed = 0.0
        self.shape = CursorShape()

        # Stationary-pointer detection.
        self.stationary_tick_count = 0
        self.last_stationary_x: float | None = None
        self.last_stationary_y: float | None = None
        self.stationary_recalculated = False

    def _update_stationary_detection(
        self,
        pointer_x: float,
        pointer_y: float,
    ) -> bool:
        """
        Return True only once after the pointer becomes roughly stationary
        for STATIONARY_TICKS_REQUIRED consecutive updates.

        After triggering, it will not trigger again until the pointer moves
        farther than STATIONARY_RESET_RADIUS from the stationary location.
        """
        # No previous reference: begin monitoring from the current position.
        if self.last_stationary_x is None:
            self.last_stationary_x = pointer_x
            self.last_stationary_y = pointer_y
            self.stationary_tick_count = 1
            self.stationary_recalculated = False
            return False

        movement_from_reference = math.hypot(
            pointer_x - self.last_stationary_x,
            pointer_y - self.last_stationary_y,
        )

        # Pointer has moved materially since the last stationary reference.
        if movement_from_reference > STATIONARY_RESET_RADIUS:
            self.last_stationary_x = pointer_x
            self.last_stationary_y = pointer_y
            self.stationary_tick_count = 1
            self.stationary_recalculated = False
            return False

        # Pointer remains near the reference.
        self.stationary_tick_count += 1

        if (
            self.stationary_tick_count >= STATIONARY_TICKS_REQUIRED
            and not self.stationary_recalculated
        ):
            # Latch: prevent repeated recalculations while still stationary.
            self.stationary_recalculated = True
            return True

        return False
        
    # ─────────────────────────── Public update ───────────────────────────

    def update(self, pointer_x: int, pointer_y: int) -> CursorShape:
        now = time.monotonic()

        if self.last_time is None:
            self._initialize_state(pointer_x, pointer_y, now)
            return self.shape

        dt = max(now - self.last_time, 0.001)
        self.last_time = now

        self.pointer_x = pointer_x
        self.pointer_y = pointer_y

        # If the pointer has been roughly still for three ticks, force a
        # fresh route to its exact current location.
        if self._update_stationary_detection(pointer_x, pointer_y):
            self._create_route(
                self.ship_x,
                self.ship_y,
                float(pointer_x),
                float(pointer_y),
            )

        # Replan when needed, preserving current position and velocity.
        self._update_route(pointer_x, pointer_y)
        
        # Advance along the route.
        ship_speed = self._follow_route(dt)

        # Light drag retains a drifting, momentum-based feel.
        ship_speed = self._apply_drag_and_limit(ship_speed, dt)

        # Update visual scale, stretch, heading, and metrics.
        self._update_visuals(ship_speed, dt)

        return self.shape

    # ───────────────────────────── Initialization ────────────────────────

    def _initialize_state(
        self,
        pointer_x: int,
        pointer_y: int,
        now: float,
    ) -> None:
        self.pointer_x = pointer_x
        self.pointer_y = pointer_y
        self.last_time = now

        self.ship_x = float(pointer_x)
        self.ship_y = float(pointer_y)

        self._create_route(
            float(pointer_x),
            float(pointer_y),
            float(pointer_x),
            float(pointer_y),
        )

    # ───────────────────────────── Route planning ────────────────────────

    def _update_route(
        self,
        target_x: float,
        target_y: float,
    ) -> None:
        """Create or replace the Bezier route when appropriate."""
        if not self.bezier_segments:
            self._create_route(
                self.ship_x,
                self.ship_y,
                target_x,
                target_y,
            )
            return

        # Route is complete.
        if self.traveled_distance >= self.total_length - 0.001:
            self._create_route(
                self.ship_x,
                self.ship_y,
                target_x,
                target_y,
            )
            return

        # Target has moved materially since the route was planned.
        old_target = self.bezier_segments[-1][3]
        target_movement = math.hypot(
            target_x - old_target[0],
            target_y - old_target[1],
        )

        if target_movement > BEZIER_REPLAN_DISTANCE:
            self._create_route(
                self.ship_x,
                self.ship_y,
                target_x,
                target_y,
            )

    def _create_route(
        self,
        start_x: float,
        start_y: float,
        target_x: float,
        target_y: float,
    ) -> None:
        """
        Build a smooth multi-segment cubic Bezier route.

        The first segment's outgoing tangent preserves the ship's current
        velocity. Interior waypoints use a Catmull-Rom-derived tangent scheme,
        producing connected cubic curves without sharp corners.
        """
        dx = target_x - start_x
        dy = target_y - start_y
        distance = math.hypot(dx, dy)

        self.bezier_segments.clear()
        self.segment_lengths.clear()
        self.traveled_distance = 0.0

        if distance < PHYS_DEAD_ZONE:
            # Create an exact straight-line cubic Bezier from the ship to
            # the target.
            #
            # For a straight line, place:
            #   P1 = P0 + (P3 - P0) / 3
            #   P2 = P0 + 2 * (P3 - P0) / 3
            #
            # All four control points are collinear, so the evaluated curve
            # is exactly the straight segment from P0 to P3.
            one_third_x = start_x + dx / 3.0
            one_third_y = start_y + dy / 3.0

            two_thirds_x = start_x + (2.0 * dx) / 3.0
            two_thirds_y = start_y + (2.0 * dy) / 3.0

            segment: BezierSegment = (
                (start_x, start_y),
                (one_third_x, one_third_y),
                (two_thirds_x, two_thirds_y),
                (target_x, target_y),
            )

            self.bezier_segments.append(segment)
            self.segment_lengths.append(max(distance, 1.0))
            self.total_length = max(distance, 1.0)
            return
        
        waypoints = self._make_waypoints(
            start_x,
            start_y,
            target_x,
            target_y,
            distance,
        )

        # Build one cubic segment between each adjacent pair of waypoints.
        for i in range(len(waypoints) - 1):
            p0 = waypoints[i]
            p3 = waypoints[i + 1]

            c1, c2 = self._segment_control_points(
                waypoints,
                i,
                p0,
                p3,
            )

            self.bezier_segments.append((p0, c1, c2, p3))

        self.segment_lengths = [
            self._estimate_segment_length(segment)
            for segment in self.bezier_segments
        ]
        self.total_length = sum(self.segment_lengths)

    def _make_waypoints(
        self,
        start_x: float,
        start_y: float,
        target_x: float,
        target_y: float,
        distance: float,
    ) -> list[tuple[float, float]]:
        """
        Create waypoints for the multi-segment route.

        With BEZIER_SEGMENT_COUNT = 1, this is simply:
            [ship, target]

        With higher values, alternating perpendicular offsets create gentle
        S-shaped routes.
        """
        BEZIER_SEGMENT_COUNT = round(4 * clamp(distance / screen_dist, 0, 1))
        print(f"BEZIER_SEGMENT_COUNT={BEZIER_SEGMENT_COUNT}, {distance:.0f}/{screen_dist:.0f}", end="\r")
        count = max(1, BEZIER_SEGMENT_COUNT)
        waypoints: list[tuple[float, float]] = []

        # Perpendicular unit vector to the direct ship-to-target line.
        if distance > 0.001:
            perpendicular_x = -(target_y - start_y) / distance
            perpendicular_y = (target_x - start_x) / distance
        else:
            perpendicular_x = 0.0
            perpendicular_y = 0.0

        for i in range(count + 1):
            amount = i / count

            x = start_x + (target_x - start_x) * amount
            y = start_y + (target_y - start_y) * amount

            # Curve only interior waypoints; endpoints remain exact.
            if 0 < i < count:
                side = 1.0 if i % 2 == 1 else -1.0

                curve_amount = (
                    distance
                    * BEZIER_WAYPOINT_CURVE_FRACTION
                    * side
                )

                x += perpendicular_x * curve_amount
                y += perpendicular_y * curve_amount

            waypoints.append((x, y))

        return waypoints

    def _segment_control_points(
        self,
        waypoints: list[tuple[float, float]],
        index: int,
        p0: tuple[float, float],
        p3: tuple[float, float],
    ) -> tuple[
        tuple[float, float],
        tuple[float, float],
    ]:
        """
        Calculate cubic Bezier control points for one route segment.

        Uses Catmull-Rom-style tangents for smooth continuity between
        segments. The first segment's outgoing tangent is overridden so that
        the ship's existing momentum is preserved.
        """
        previous = waypoints[max(0, index - 1)]
        following = waypoints[min(len(waypoints) - 1, index + 2)]

        # Catmull-Rom-derived tangent at p0.
        tangent0_x = (p3[0] - previous[0]) / 6.0
        tangent0_y = (p3[1] - previous[1]) / 6.0

        # Catmull-Rom-derived tangent at p3.
        tangent1_x = (following[0] - p0[0]) / 6.0
        tangent1_y = (following[1] - p0[1]) / 6.0

        # Preserve current momentum on the first segment.
        if index == 0:
            speed = math.hypot(self.ship_vx, self.ship_vy)

            if speed > 1.0:
                momentum_x = self.ship_vx / speed
                momentum_y = self.ship_vy / speed

                momentum_handle = min(
                    math.hypot(
                        p3[0] - p0[0],
                        p3[1] - p0[1],
                    )
                    * BEZIER_START_HANDLE_FRACTION,
                    BEZIER_START_HANDLE_MAX,
                )

                tangent0_x = momentum_x * momentum_handle
                tangent0_y = momentum_y * momentum_handle

        c1 = (
            p0[0] + tangent0_x,
            p0[1] + tangent0_y,
        )

        c2 = (
            p3[0] - tangent1_x,
            p3[1] - tangent1_y,
        )

        return c1, c2

    # ─────────────────────────── Bezier evaluation ───────────────────────

    def _bezier_point(
        self,
        segment: BezierSegment,
        t: float,
    ) -> tuple[float, float]:
        """Evaluate a cubic Bezier segment at normalized parameter t."""
        p0, p1, p2, p3 = segment
        u = 1.0 - t

        x = (
            u * u * u * p0[0]
            + 3.0 * u * u * t * p1[0]
            + 3.0 * u * t * t * p2[0]
            + t * t * t * p3[0]
        )
        y = (
            u * u * u * p0[1]
            + 3.0 * u * u * t * p1[1]
            + 3.0 * u * t * t * p2[1]
            + t * t * t * p3[1]
        )

        return x, y

    def _bezier_tangent(
        self,
        segment: BezierSegment,
        t: float,
    ) -> tuple[float, float]:
        """
        Return the instantaneous direction of a cubic Bezier segment.

        This is the derivative of the cubic Bezier with respect to t.
        """
        p0, p1, p2, p3 = segment
        u = 1.0 - t

        tangent_x = (
            3.0 * u * u * (p1[0] - p0[0])
            + 6.0 * u * t * (p2[0] - p1[0])
            + 3.0 * t * t * (p3[0] - p2[0])
        )
        tangent_y = (
            3.0 * u * u * (p1[1] - p0[1])
            + 6.0 * u * t * (p2[1] - p1[1])
            + 3.0 * t * t * (p3[1] - p2[1])
        )

        return tangent_x, tangent_y

    def _estimate_segment_length(
        self,
        segment: BezierSegment,
    ) -> float:
        """Estimate a segment's arc length by sampling it."""
        length = 0.0
        previous_x, previous_y = self._bezier_point(segment, 0.0)

        for i in range(1, BEZIER_LENGTH_SAMPLES + 1):
            t = i / BEZIER_LENGTH_SAMPLES
            x, y = self._bezier_point(segment, t)

            length += math.hypot(
                x - previous_x,
                y - previous_y,
            )

            previous_x = x
            previous_y = y

        return max(length, 1.0)

    def _position_at_distance(
        self,
        distance: float,
    ) -> tuple[float, float, BezierSegment, float]:
        """
        Return the point, active segment, and segment-local t corresponding
        to a given distance along the complete multi-segment route.
        """
        remaining = clamp(distance, 0.0, self.total_length)

        for segment, segment_length in zip(
            self.bezier_segments,
            self.segment_lengths,
        ):
            if remaining <= segment_length:
                local_t = remaining / segment_length
                point = self._bezier_point(segment, local_t)
                return point, segment, local_t

            remaining -= segment_length

        # Past the end: return the final point.
        segment = self.bezier_segments[-1]
        point = self._bezier_point(segment, 1.0)
        return point, segment, 1.0

    # ───────────────────────────── Route following ───────────────────────

    def _follow_route(self, dt: float) -> float:
        """Advance the ship along the active Bezier route."""
        # current_speed = math.hypot(self.ship_vx, self.ship_vy)

        # # Gently accelerate toward a cruising speed.
        # target_speed = min(
        #     LIN_SPEED_MAX,
        #     max(
        #         BEZIER_MIN_SPEED,
        #         current_speed + BEZIER_ACCELERATION * dt,
        #     ),
        # )

        # # Near the destination, decelerate smoothly.
        remaining_distance = max(
            0.0,
            self.total_length - self.traveled_distance,
        )
        target_speed = max(remaining_distance, LIN_SPEED_MIN)


        travel_distance = target_speed * dt
        self.traveled_distance = min(
            self.traveled_distance + travel_distance,
            self.total_length,
        )

        new_position, active_segment, segment_t = self._position_at_distance(
            self.traveled_distance,
        )

        new_x, new_y = new_position

        # Derive actual velocity from movement along the route.
        self.ship_vx = (new_x - self.ship_x) / dt
        self.ship_vy = (new_y - self.ship_y) / dt

        self.ship_x = new_x
        self.ship_y = new_y

        # Heading exactly follows the current path tangent.
        tangent_x, tangent_y = self._bezier_tangent(
            active_segment,
            segment_t,
        )
        tangent_magnitude = math.hypot(tangent_x, tangent_y)

        if tangent_magnitude > 0.0001:
            movement_direction = math.degrees(
                math.atan2(tangent_y, tangent_x)
            )

            # SVG nose points upward, corresponding to -90 degrees.
            self.heading_deg = (movement_direction + 90.0) % 360.0

        return math.hypot(self.ship_vx, self.ship_vy)

    # ───────────────────────────── Linear physics ────────────────────────

    def _apply_drag_and_limit(
        self,
        ship_speed: float,
        dt: float,
    ) -> float:
        """Apply gentle drag and enforce the maximum speed."""
        drag_factor = math.exp(-LIN_SPEED_DRAG * dt)

        self.ship_vx *= drag_factor
        self.ship_vy *= drag_factor

        ship_speed = math.hypot(self.ship_vx, self.ship_vy)

        if ship_speed > LIN_SPEED_MAX:
            factor = LIN_SPEED_MAX / ship_speed
            self.ship_vx *= factor
            self.ship_vy *= factor
            ship_speed = LIN_SPEED_MAX

        acceleration = max(
            0.0,
            (ship_speed - self.last_speed) / dt,
        )
        self.last_speed = ship_speed

        self._last_ship_speed = ship_speed
        self._last_acceleration = acceleration

        return ship_speed

    # ───────────────────────────── Visual updates ────────────────────────

    def _update_visuals(self, ship_speed: float, dt: float) -> None:
        acceleration = getattr(self, "_last_acceleration", 0.0)

        speed_amount = clamp(ship_speed * VIZ_SPEED_MULT, 0.0, 1.0)
        accel_amount = clamp(acceleration * VIZ_ACCEL_MULT, 0.0, 1.0)

        target_scale = 1.0 + speed_amount * (VIZ_SCALE_MAX - 1.0)
        target_scale += accel_amount * SCALE_ACCEL_MULT
        target_scale = clamp(target_scale, 1.0, VIZ_SCALE_MAX)

        target_stretch = 1.0 + speed_amount * (VIZ_ELONG_MAX - 1.0)

        self.shape.scale = lerp(
            self.shape.scale,
            target_scale,
            VIZ_SHAPE_SMOOTH,
        )
        self.shape.stretch = lerp(
            self.shape.stretch,
            target_stretch,
            VIZ_SHAPE_SMOOTH,
        )

        target_qt_angle = self.heading_deg + VIZ_ANGLE_OFFSET

        self.shape.angle_deg = lerp_angle(
            self.shape.angle_deg,
            target_qt_angle,
            VIZ_SHAPE_SMOOTH,
        )

        self.shape.speed = ship_speed
        self.shape.acceleration = acceleration

    # ───────────────────────────── Debug accessors ───────────────────────

    def debug_segments(self) -> list[BezierSegment]:
        """Return all active Bezier segments for debug rendering."""
        return self.bezier_segments

    def debug_position(self) -> tuple[float, float]:
        """Return the ship's current position."""
        return self.ship_x, self.ship_y


# ───────────────────────────── PyQt vector overlays ──────────────────────────

class CursorOverlay(QWidget):
    """Transparent, click-through, always-on-top SVG spaceship overlay."""

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

        self.svg_renderer = QSvgRenderer()
        self.svg_renderer.load(SPACESHIP_SVG)

        if not self.svg_renderer.isValid():
            raise RuntimeError("The embedded spaceship SVG could not be parsed.")

    def set_shape(self, shape: CursorShape) -> None:
        self.shape = shape
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        center_x = OVERLAY_SIZE / 2.0
        center_y = OVERLAY_SIZE / 2.0

        painter.save()
        painter.translate(center_x, center_y)

        # Qt rotation is clockwise in widget/screen coordinates.
        painter.rotate(self.shape.angle_deg)

        # Scale uniformly, then stretch along the SVG's forward axis.
        painter.scale(
            self.shape.scale,
            self.shape.scale * self.shape.stretch,
        )

        # Render at the SVG's logical bounds. The painter transform scales and
        # rotates the vector image at draw time.
        self.svg_renderer.render(
            painter,
            QRectF(-10.0, -2.0, 20.0, 24.0),
        )

        painter.restore()
        painter.end()


class GreenDotOverlay(QWidget):
    """Tiny always-on-top marker locked to the real pointer position."""

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

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 255, 0, 255))
        painter.drawEllipse(2, 2, 5, 5)

        painter.end()


class BezierDebugOverlay(QWidget):
    """
    Optional full-screen debug overlay.

    Draws:
    - Green curves: active multi-segment Bezier route.
    - Cyan dashed lines: control polygons.
    - Red/cyan/blue/magenta points: P0/P1/P2/P3 for each segment.
    - Yellow circle: current ship position.
    """

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

        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

        self.segments: list[BezierSegment] = []
        self.current_position: tuple[float, float] | None = None

    def set_route(
        self,
        segments: list[BezierSegment],
        current_position: tuple[float, float],
    ) -> None:
        self.segments = segments
        self.current_position = current_position
        self.update()

    def paintEvent(self, event) -> None:
        if not self.segments:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Control polygons.
        control_pen = QPen(QColor(0, 220, 255, 130))
        control_pen.setWidthF(1.0)
        control_pen.setStyle(Qt.PenStyle.DashLine)

        painter.setPen(control_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for p0, p1, p2, p3 in self.segments:
            painter.drawLine(QPointF(*p0), QPointF(*p1))
            painter.drawLine(QPointF(*p1), QPointF(*p2))
            painter.drawLine(QPointF(*p2), QPointF(*p3))

        # Bezier curves.
        curve_pen = QPen(QColor(0, 255, 130, 230))
        curve_pen.setWidthF(2.0)

        painter.setPen(curve_pen)

        for p0, p1, p2, p3 in self.segments:
            curve = QPainterPath()
            curve.moveTo(QPointF(*p0))
            curve.cubicTo(
                QPointF(*p1),
                QPointF(*p2),
                QPointF(*p3),
            )
            painter.drawPath(curve)

        # Control points.
        colors = [
            QColor(255, 100, 100, 255),
            QColor(0, 220, 255, 255),
            QColor(0, 180, 255, 255),
            QColor(255, 80, 255, 255),
        ]
        labels = ["P0", "P1", "P2", "P3"]

        for segment in self.segments:
            for point, color, label in zip(
                segment,
                colors,
                labels,
            ):
                self._draw_point(
                    painter,
                    QPointF(*point),
                    color,
                    label,
                )

        # Current position.
        if self.current_position is not None:
            current = QPointF(*self.current_position)

            current_pen = QPen(QColor(255, 235, 0, 255))
            current_pen.setWidthF(2.0)

            painter.setPen(current_pen)
            painter.setBrush(QColor(255, 235, 0, 110))
            painter.drawEllipse(current, 6.0, 6.0)

        painter.end()

    def _draw_point(
        self,
        painter: QPainter,
        point: QPointF,
        color: QColor,
        label: str,
    ) -> None:
        pen = QPen(color)
        pen.setWidthF(1.5)

        painter.setPen(pen)
        painter.setBrush(
            QColor(color.red(), color.green(), color.blue(), 120)
        )
        painter.drawEllipse(point, 4.0, 4.0)

        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(
            QPointF(point.x() + 7.0, point.y() - 7.0),
            label,
        )


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

        # Exactly one spaceship overlay.
        self.overlay = CursorOverlay()
        self.overlay.show()

        # Exactly one green-dot overlay.
        self.green_dot = GreenDotOverlay()
        self.green_dot.show()

        # Optional debug overlay.
        self.bezier_debug: BezierDebugOverlay | None = None

        if DEBUG_DRAW_BEZIER:
            self.bezier_debug = BezierDebugOverlay()
            self.bezier_debug.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(POLL_MS)

    def _create_transparent_xcursor(self):
        """Create an invisible X cursor from two one-bit pixmaps."""
        source = self.root.create_pixmap(1, 1, 1)
        mask = self.root.create_pixmap(1, 1, 1)

        source_gc = source.create_gc(foreground=0, background=0)
        mask_gc = mask.create_gc(foreground=0, background=0)

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
        """Update physics, repaint the ship, and lock the dot to the pointer."""
        if not self.running:
            return

        try:
            pointer_x, pointer_y = self.pointer_position()
            shape = self.physics.update(pointer_x, pointer_y)

            # Update the ship's visual state before moving its window.
            self.overlay.set_shape(shape)

            ship_x = round(self.physics.ship_x)
            ship_y = round(self.physics.ship_y)

            self.overlay.move(
                ship_x - OVERLAY_SIZE // 2,
                ship_y - OVERLAY_SIZE // 2,
            )

            # Immediate repaint reduces the chance of a stale X11 frame.
            self.overlay.repaint()

            # Keep the green dot exactly on the real pointer.
            self.green_dot.move(
                pointer_x - GREEN_DOT_SIZE // 2,
                pointer_y - GREEN_DOT_SIZE // 2,
            )

            if self.bezier_debug is not None:
                self.bezier_debug.set_route(
                    self.physics.debug_segments(),
                    self.physics.debug_position(),
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
            if self.bezier_debug is not None:
                self.bezier_debug.close()
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