from pathlib import Path

from yaml import safe_load


def _overlay_size(profile: dict) -> int:
    visual = profile["visual"]
    return int(
        float(visual["size_base"])
        * float(visual["scale_max"])
        * max(float(visual["elong_max"]), 1.0)
        * 6
    )


def _key_names(application: dict, setting: str) -> tuple[str, ...]:
    keys = application.get(setting)
    if not isinstance(keys, list) or not keys or not all(
        isinstance(key, str) and key for key in keys
    ):
        raise ValueError(
            f"application.{setting} must be a non-empty list of X11 keysyms."
        )
    return tuple(keys)


def _validate_event_profiles(config: dict) -> list[dict]:
    profile_groups = config.get("event_profile", [])
    if not isinstance(profile_groups, list):
        raise ValueError("event_profile must be a list.")

    crafts = config["craft"]
    physics_profiles = config["physics_profile"]
    valid_types = {"down", "hold", "up"}

    events = []
    event_names = set()
    for group in profile_groups:
        if not isinstance(group, dict):
            raise ValueError("Each event_profile entry must be a mapping.")
        if not isinstance(group.get("name"), str) or not group["name"]:
            raise ValueError("Event profile name must be a non-empty string.")
        event_names.add(group["name"])
        if not isinstance(group.get("key"), str) or not group["key"]:
            raise ValueError("Event profile key must be a non-empty string.")
        group_events = group.get("events")
        if not isinstance(group_events, list) or not group_events:
            raise ValueError(
                "Each event_profile entry must define a non-empty events list."
            )

        for event in group_events:
            if not isinstance(event, dict):
                raise ValueError("Each event profile event must be a mapping.")

            missing = {
                "type", "craft", "cursor",
                "physics_profile", "physics_restore",
            } - event.keys()
            if missing:
                raise ValueError(
                    f"Event profile event is missing required fields: "
                    f"{sorted(missing)}"
                )
            if event["type"] not in valid_types:
                raise ValueError(
                    f"Unknown event type {event['type']!r}; "
                    "expected down, hold, or up."
                )
            if event["craft"] not in crafts:
                raise ValueError(f"Unknown craft {event['craft']!r}.")
            if event["physics_profile"] not in physics_profiles:
                raise ValueError(
                    f"Unknown physics profile {event['physics_profile']!r}."
                )
            if not isinstance(event["physics_restore"], bool):
                raise ValueError("physics_restore must be true or false.")
            cursor = event["cursor"]
            if isinstance(cursor, bool) or not isinstance(cursor, (int, str)):
                raise ValueError(
                    "Event profile cursor must be 0, a marker size, or an SVG "
                    "name."
                )
            if event["type"] == "hold":
                every = event.get("every")
                if not isinstance(every, int) or every < 1:
                    raise ValueError(
                        "Hold event profiles require an integer every value "
                        ">= 1."
                    )
                every_offset = event.get("every_offset", 0)
                if (
                    not isinstance(every_offset, int)
                    or every_offset < 0
                    or every_offset >= every
                ):
                    raise ValueError(
                        "Hold event profile every_offset must be an integer "
                        "from 0 through every - 1."
                    )

            events.append({
                "name": group["name"],
                "key": group["key"],
                **event,
            })

    for craft_name, craft in crafts.items():
        subscribed_events = craft.get("subscribed_events", [])
        if not isinstance(subscribed_events, list) or not all(
            isinstance(event_name, str) for event_name in subscribed_events
        ):
            raise ValueError(
                f"Craft {craft_name!r} subscribed_events must be a list of "
                "event names."
            )
        unknown_events = set(subscribed_events) - event_names
        if unknown_events:
            raise ValueError(
                f"Craft {craft_name!r} subscribes to unknown events: "
                f"{sorted(unknown_events)}"
            )

    return events


def load_svg(name: str) -> bytes:
    with Path("svgs", f"{name}.svg").open("rb") as svg_file:
        return svg_file.read()


def select_profile(
    conf: dict,
    *,
    craft_name: str,
    physics_profile_name: str,
) -> bytes:
    """Apply a craft and physics profile, returning the selected craft SVG."""
    craft = conf["CRAFTS"][craft_name]
    profile = conf["PHYSICS_PROFILES"][physics_profile_name]
    visual = profile["visual"]
    linear_motion = profile["linear_motion"]
    bezier = profile["bezier"]

    conf.update({
        "VIZ_SIZE_BASE": float(visual["size_base"]),
        "VIZ_ANGLE_OFFSET": float(visual["angle_offset"]),
        "VIZ_SPEED_MULT": float(visual["speed_mult"]),
        "VIZ_ACCEL_MULT": float(visual["accel_mult"]),
        "VIZ_SCALE_MAX": float(visual["scale_max"]),
        "VIZ_ELONG_MAX": float(visual["elong_max"]),
        "VIZ_SHAPE_SMOOTH": float(visual["shape_smooth"]),
        "LIN_SPEED_MAX": float(linear_motion["speed_max"]),
        "LIN_SPEED_MIN": float(linear_motion["speed_min"]),
        "LIN_SPEED_DRAG": float(linear_motion["speed_drag"]),
        "BEZIER_WAYPOINT_CURVE_FRACTION": float(
            bezier["waypoint_curve_fraction"]
        ),
        "BEZIER_START_HANDLE_FRACTION": float(
            bezier["start_handle_fraction"]
        ),
        "BEZIER_START_HANDLE_MAX": float(bezier["start_handle_max"]),
        "BEZIER_END_HANDLE_FRACTION": float(bezier["end_handle_fraction"]),
        "BEZIER_END_HANDLE_MAX": float(bezier["end_handle_max"]),
        "BEZIER_MIN_SPEED": float(bezier["min_speed"]),
        "BEZIER_ACCELERATION": float(bezier["acceleration"]),
        "SPACESHIP_FADE_START_DISTANCE": float(craft["fade"]["distance_beg"]),
        "SPACESHIP_FADE_END_DISTANCE": float(craft["fade"]["distance_end"]),
        "SPACESHIP_MIN_OPACITY": float(craft["fade"]["opacity_min"]),
        "OVERLAY_SIZE": _overlay_size(profile),
        "CURSOR": craft["svg"]["cursor"],
        "GREEN_DOT_SIZE": (
            int(craft["svg"]["cursor"])
            if (
                isinstance(craft["svg"]["cursor"], int)
                and not isinstance(craft["svg"]["cursor"], bool)
            )
            else 0
        ),
        "ACTIVE_CRAFT": craft_name,
        "ACTIVE_PHYSICS_PROFILE": physics_profile_name,
    })
    return load_svg(craft["svg"]["chaser"])


def load_config(path: Path) -> tuple[dict, bytes]:
    """Load configuration and the SVG for the default craft."""
    with path.open("rb") as config_file:
        config = safe_load(config_file)

    application = config["application"]
    crafts = config["craft"]
    physics_profiles = config["physics_profile"]
    default_craft = application["default_craft"]
    if default_craft not in crafts:
        raise ValueError(f"Unknown default craft {default_craft!r}.")

    events = _validate_event_profiles(config)
    default_profile = crafts[default_craft]["physics_profile"]
    if default_profile not in physics_profiles:
        raise ValueError(f"Unknown physics profile {default_profile!r}.")

    craft_toggle_keys = _key_names(application, "craft_toggle_keys")
    debug_toggle_keys = _key_names(application, "debug_toggle_keys")
    conf = {
        "BEZIER_LENGTH_SAMPLES": int(application["bezier"]["length_samples"]),
        "PHYS_DEAD_ZONE": float(application["dead_zone"]),
        "STATIONARY_TICKS_REQUIRED": int(
            application["stationary"]["ticks_required"]
        ),
        "STATIONARY_RADIUS": float(application["stationary"]["radius_beg"]),
        "STATIONARY_RESET_RADIUS": float(
            application["stationary"]["radius_end"]
        ),
        "DEBUG_DRAW_BEZIER": bool(application["bezier"]["debug_draw"]),
        "BEZIER_REPLAN_DISTANCE": float(application["replan_distance"]),
        "POLL_MS": int(application["poll_ms"]),
        "MOUSE_DOWN_RECALC_INTERVAL": int(
            application["mouse_down_recalc_interval"]
        ),
        "CRAFT_TOGGLE_KEYS": craft_toggle_keys,
        "DEBUG_TOGGLE_KEYS": debug_toggle_keys,
        "CRAFTS": crafts,
        "PHYSICS_PROFILES": physics_profiles,
        "EVENT_PROFILES": events,
    }
    svg = select_profile(
        conf,
        craft_name=default_craft,
        physics_profile_name=default_profile,
    )
    return conf, svg
