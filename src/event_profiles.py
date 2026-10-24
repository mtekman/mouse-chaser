from collections.abc import Callable


EventCallback = Callable[[dict], None]
SubscriptionCallback = Callable[[dict], bool]


class EventProfiles:
    """Dispatch ordered down, periodic hold, and up event profiles."""

    def __init__(
        self,
        profiles: list[dict],
        callback: EventCallback,
        is_subscribed: SubscriptionCallback | None = None,
    ) -> None:
        self.profiles = profiles
        self.callback = callback
        self.is_subscribed = is_subscribed or (lambda _profile: True)
        self.active_keys: set[str] = set()
        self.hold_ticks: dict[str, int] = {}

    def down(self, key: str) -> bool:
        if key in self.active_keys:
            return False

        profiles = [
            profile
            for profile in self._profiles_for(key, "down")
            if self.is_subscribed(profile)
        ]
        if not profiles:
            return False

        self.active_keys.add(key)
        self.hold_ticks[key] = 0
        for profile in profiles:
            self.callback(profile)
        return True

    def tick(self) -> None:
        for key in tuple(self.active_keys):
            self.hold_ticks[key] += 1
            tick = self.hold_ticks[key]
            for profile in self._profiles_for(key, "hold"):
                every_offset = profile.get("every_offset", 0)
                if (tick - every_offset) % profile["every"] == 0:
                    self.callback(profile)

    def up(self, key: str) -> None:
        if key not in self.active_keys:
            return

        self._dispatch(key, "up")
        self.active_keys.remove(key)
        del self.hold_ticks[key]

    def _dispatch(self, key: str, event_type: str) -> None:
        for profile in self._profiles_for(key, event_type):
            self.callback(profile)

    def _profiles_for(self, key: str, event_type: str) -> list[dict]:
        return [
            profile
            for profile in self.profiles
            if profile["key"] == key and profile["type"] == event_type
        ]
