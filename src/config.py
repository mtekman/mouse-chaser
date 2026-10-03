from pathlib import Path
from yaml import safe_load

def load_config(path: Path) -> tuple[dict, bytes]:
    """Load TOML settings and the configured SVG file."""
    with path.open("rb") as config_file:
        config = safe_load(config_file)

    visual = config["visual"]
    linear = config["linear_motion"]
    bezier = config["bezier"]
    physics = config["physics"]
    fade = config["spaceship_fade"]
    input_config = config["input"]
    overlay = config["overlay"]

    lin_speed_max = float(linear["speed_max"])

    min_speed_value = bezier["min_speed"]
    if min_speed_value == "lin_speed_max":
        bezier_min_speed = lin_speed_max
    else:
        bezier_min_speed = float(min_speed_value)

    overlay_size_value = overlay["size"]
    if overlay_size_value == "auto":
        overlay_size = int(
            float(visual["size_base"])
            * float(visual["scale_max"])
            * max(float(visual["elong_max"]), 1.0)
            * 6
        )
    else:
        overlay_size = int(overlay_size_value)

    conf = {
        "VIZ_SIZE_BASE": float(visual["size_base"]),
        "VIZ_ANGLE_OFFSET": float(visual["angle_offset"]),
        "VIZ_SPEED_MULT": float(visual["speed_mult"]),
        "VIZ_ACCEL_MULT": float(visual["accel_mult"]),
        "VIZ_SCALE_MAX": float(visual["scale_max"]),
        "VIZ_ELONG_MAX": float(visual["elong_max"]),
        "VIZ_SHAPE_SMOOTH": float(visual["shape_smooth"]),
        "LIN_SPEED_MAX": lin_speed_max,
        "LIN_SPEED_MIN": float(linear["speed_min"]),
        "LIN_SPEED_DRAG": float(linear["speed_drag"]),
        "DEBUG_DRAW_BEZIER": bool(bezier["debug_draw_bezier"]),
        "BEZIER_REPLAN_DISTANCE": float(bezier["replan_distance"]),
        "BEZIER_WAYPOINT_CURVE_FRACTION": float(bezier["waypoint_curve_fraction"]),
        "BEZIER_START_HANDLE_FRACTION": float(bezier["start_handle_fraction"]),
        "BEZIER_START_HANDLE_MAX": float(bezier["start_handle_max"]),
        "BEZIER_END_HANDLE_FRACTION": float(bezier["end_handle_fraction"]),
        "BEZIER_END_HANDLE_MAX": float(bezier["end_handle_max"]),
        "BEZIER_MIN_SPEED": bezier_min_speed,
        "BEZIER_ACCELERATION": float(bezier["acceleration"]),
        "BEZIER_LENGTH_SAMPLES": int(bezier["length_samples"]),
        "PHYS_DEAD_ZONE": float(physics["dead_zone"]),
        "STATIONARY_TICKS_REQUIRED": int(physics["stationary_ticks_required"]),
        "STATIONARY_RADIUS": float(physics["stationary_radius"]),
        "STATIONARY_RESET_RADIUS": float(physics["stationary_reset_radius"]),
        "SPACESHIP_FADE_START_DISTANCE": float(fade["fade_start_distance"]),
        "SPACESHIP_FADE_END_DISTANCE": float(fade["fade_end_distance"]),
        "SPACESHIP_MIN_OPACITY": float(fade["min_opacity"]),
        "POLL_MS": int(input_config["poll_ms"]),
        "MOUSE_DOWN_RECALC_INTERVAL": int(input_config["mouse_down_recalc_interval"]),
        "OVERLAY_SIZE": overlay_size,
        "GREEN_DOT_SIZE": int(overlay["green_dot_size"])
    }
    svg_path = Path("svgs", config["spaceship"]["svg_path"])
    with svg_path.open("br") as svg_file:
        spaceship_svg = svg_file.read()

    return conf, spaceship_svg


