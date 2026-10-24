import math
from time import monotonic

from spaceship import SpacecraftState, clamp, lerp, lerp_angle
from config import load_config
from pathlib import Path

CONFIG_PATH = Path("..").with_name("config.yaml")
CONFIG, SPACESHIP_SVG = load_config(CONFIG_PATH)

BezierSegment = tuple[
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
]

screen_dist = math.hypot(2560, 1440)

class BezierPhysics:
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
        self.shape = SpacecraftState()

        # Stationary-pointer detection.
        self.stationary_tick_count = 0
        self.last_stationary_x: float | None = None
        self.last_stationary_y: float | None = None
        self.stationary_recalculated = False
        # Mouse-down route recalculation.
        self.mouse_down_tick_count = 0

    def _distance_opacity(
        self,
        pointer_x: float,
        pointer_y: float,
    ) -> float:
        """
        Return an opacity value that increases with distance from the target.

        The spaceship is most transparent at or below
        SPACESHIP_FADE_START_DISTANCE, then becomes fully opaque at or beyond
        SPACESHIP_FADE_END_DISTANCE.
        """
        distance = math.hypot(
            pointer_x - self.ship_x,
            pointer_y - self.ship_y,
        )

        if distance <= CONFIG["SPACESHIP_FADE_START_DISTANCE"]:
            return CONFIG["SPACESHIP_MIN_OPACITY"]

        if distance >= CONFIG["SPACESHIP_FADE_END_DISTANCE"]:
            return 1.0

        fade_range = (
            CONFIG["SPACESHIP_FADE_END_DISTANCE"]
            - CONFIG["SPACESHIP_FADE_START_DISTANCE"]
        )

        fade_progress = (
            distance - CONFIG["SPACESHIP_FADE_START_DISTANCE"]
        ) / fade_range

        # Interpolate from the minimum opacity at close range to full
        # opacity at long range.
        return lerp(
            CONFIG["SPACESHIP_MIN_OPACITY"],
            1.0,
            fade_progress,
        )
    
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
        if movement_from_reference > CONFIG["STATIONARY_RESET_RADIUS"]:
            self.last_stationary_x = pointer_x
            self.last_stationary_y = pointer_y
            self.stationary_tick_count = 1
            self.stationary_recalculated = False
            return False

        # Pointer remains near the reference.
        self.stationary_tick_count += 1

        if (
            self.stationary_tick_count >= CONFIG["STATIONARY_TICKS_REQUIRED"]
            and not self.stationary_recalculated
        ):
            # Latch: prevent repeated recalculations while still stationary.
            self.stationary_recalculated = True
            return True

        return False
        
    # ─────────────────────────── Public update ───────────────────────────
    def update(
        self,
        pointer_x: int,
        pointer_y: int,
        *,
        mouse_down: bool = False,
    ) -> SpacecraftState:
        now = monotonic()

        if self.last_time is None:
            self._initialize_state(pointer_x, pointer_y, now)
            return self.shape

        dt = max(now - self.last_time, 0.001)
        self.last_time = now

        self.pointer_x = pointer_x
        self.pointer_y = pointer_y
        # While the left mouse button is held, force a fresh route every
        # MOUSE_DOWN_RECALC_INTERVAL ticks.
        if mouse_down:
            self.mouse_down_tick_count += 1

            if (
                self.mouse_down_tick_count
                >= CONFIG["MOUSE_DOWN_RECALC_INTERVAL"]
            ):
                self._create_route(
                    self.ship_x,
                    self.ship_y,
                    float(pointer_x),
                    float(pointer_y),
                )

                # Reset after recalculating, so the next route is created
                # two ticks later.
                self.mouse_down_tick_count = 0
        else:
            self.mouse_down_tick_count = 0
            
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

        # Fade based on current distance from the green dot.
        self.shape.distance_opacity = self._distance_opacity(
            pointer_x,
            pointer_y,
        )
        
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

        if target_movement > CONFIG["BEZIER_REPLAN_DISTANCE"]:
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

        if distance < CONFIG["PHYS_DEAD_ZONE"]:
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
        BEZIER_SEGMENT_COUNT = round(3.5 * clamp(math.sqrt(distance / screen_dist), 0, 1))
        print(f"BEZIER_SEGMENT_COUNT={BEZIER_SEGMENT_COUNT}, {distance:.0f}/{screen_dist:.0f}                    ", end="\r")
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
                    * CONFIG["BEZIER_WAYPOINT_CURVE_FRACTION"]
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
                    * CONFIG["BEZIER_START_HANDLE_FRACTION"],
                    CONFIG["BEZIER_START_HANDLE_MAX"],
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

        for i in range(1, CONFIG["BEZIER_LENGTH_SAMPLES"] + 1):
            t = i / CONFIG["BEZIER_LENGTH_SAMPLES"]
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
        target_speed = max(remaining_distance, CONFIG["LIN_SPEED_MIN"])


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
        drag_factor = math.exp(-CONFIG["LIN_SPEED_DRAG"] * dt)

        self.ship_vx *= drag_factor
        self.ship_vy *= drag_factor

        ship_speed = math.hypot(self.ship_vx, self.ship_vy)

        if ship_speed > CONFIG["LIN_SPEED_MAX"]:
            factor = CONFIG["LIN_SPEED_MAX"] / ship_speed
            self.ship_vx *= factor
            self.ship_vy *= factor
            ship_speed = CONFIG["LIN_SPEED_MAX"]

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

        speed_amount = clamp(ship_speed * CONFIG["VIZ_SPEED_MULT"], 0.0, 1.0)
        accel_amount = clamp(acceleration * CONFIG["VIZ_ACCEL_MULT"], 0.0, 1.0)

        target_scale = 1.0 + speed_amount * (CONFIG["VIZ_SCALE_MAX"] - 1.0)
        target_scale += accel_amount * CONFIG["VIZ_ACCEL_MULT"]
        target_scale = clamp(target_scale, 1.0, CONFIG["VIZ_SCALE_MAX"])

        target_stretch = 1.0 + speed_amount * (CONFIG["VIZ_ELONG_MAX"] - 1.0)

        self.shape.scale = lerp(
            self.shape.scale,
            target_scale,
            CONFIG["VIZ_SHAPE_SMOOTH"],
        )
        self.shape.stretch = lerp(
            self.shape.stretch,
            target_stretch,
            CONFIG["VIZ_SHAPE_SMOOTH"],
        )

        target_qt_angle = self.heading_deg + CONFIG["VIZ_ANGLE_OFFSET"]

        self.shape.angle_deg = lerp_angle(
            self.shape.angle_deg,
            target_qt_angle,
            CONFIG["VIZ_SHAPE_SMOOTH"],
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

