import unittest
from pathlib import Path

from config import load_config, select_profile
from event_profiles import EventProfiles


class EventProfilesTest(unittest.TestCase):
    def test_runs_down_hold_and_up_in_order(self) -> None:
        profiles = [
            {"name": "RunSwitch", "type": "down", "key": "Button1"},
            {
                "name": "RunSwitch",
                "type": "hold",
                "every": 2,
                "key": "Button1",
            },
            {"name": "RunSwitch", "type": "up", "key": "Button1"},
        ]
        events: list[str] = []
        dispatcher = EventProfiles(
            profiles,
            lambda profile: events.append(profile["type"]),
        )

        dispatcher.down("Button1")
        dispatcher.tick()
        dispatcher.tick()
        dispatcher.up("Button1")

        self.assertEqual(events, ["down", "hold", "up"])

    def test_selects_configured_craft_and_profile(self) -> None:
        config, _ = load_config(Path("config.yaml"))

        self.assertEqual(
            config["CRAFT_TOGGLE_KEYS"],
            ("Control_L", "Control_R"),
        )
        self.assertEqual(
            config["DEBUG_TOGGLE_KEYS"],
            ("Shift_L", "Shift_R"),
        )

        svg = select_profile(
            config,
            craft_name="ufo",
            physics_profile_name="profile1",
        )

        self.assertEqual(config["ACTIVE_CRAFT"], "ufo")
        self.assertEqual(config["ACTIVE_PHYSICS_PROFILE"], "profile1")
        self.assertEqual(svg, Path("svgs/ufo.svg").read_bytes())

    def test_keeps_the_down_subscriber_for_hold_and_up(self) -> None:
        profiles = [
            {"name": "RunSwitch", "type": "down", "key": "Button1"},
            {
                "name": "RunSwitch",
                "type": "hold",
                "every": 1,
                "key": "Button1",
            },
            {"name": "RunSwitch", "type": "up", "key": "Button1"},
        ]
        subscribed = True
        events: list[str] = []
        dispatcher = EventProfiles(
            profiles,
            lambda profile: events.append(profile["type"]),
            lambda _profile: subscribed,
        )

        self.assertTrue(dispatcher.down("Button1"))
        subscribed = False
        dispatcher.tick()
        dispatcher.up("Button1")

        self.assertEqual(events, ["down", "hold", "up"])

    def test_runs_hold_events_at_their_offsets(self) -> None:
        profiles = [
            {"name": "RunSwitch", "type": "down", "key": "Button1"},
            {
                "name": "RunSwitch",
                "type": "hold",
                "every": 10,
                "key": "Button1",
                "craft": "ufo",
            },
            {
                "name": "RunSwitch",
                "type": "hold",
                "every": 10,
                "every_offset": 5,
                "key": "Button1",
                "craft": "spaceship",
            },
        ]
        crafts: list[str] = []
        dispatcher = EventProfiles(
            profiles,
            lambda profile: crafts.append(profile.get("craft", "down")),
        )

        dispatcher.down("Button1")
        for _ in range(20):
            dispatcher.tick()

        self.assertEqual(
            crafts,
            ["down", "spaceship", "ufo", "spaceship", "ufo"],
        )


if __name__ == "__main__":
    unittest.main()
