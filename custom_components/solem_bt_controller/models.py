"""Data models for the Solem BT Controller integration."""


class IrrigationController:
    """Represents the Solem controller device."""

    def __init__(self, mac_address: str) -> None:
        self.mac_address = mac_address
        self.state: str = "On"
        self.battery_level: int | None = None

    def update_state(self, new_state: str) -> None:
        self.state = new_state

    def update_battery(self, level: int | None) -> None:
        if level is not None:
            self.battery_level = level


class IrrigationStation:
    """Represents a single irrigation station."""

    def __init__(self, station_number: int, safety_duration: int) -> None:
        self.station_number = station_number
        self.safety_duration = safety_duration
        self.state: str = "Stopped"

    def update_state(self, new_state: str) -> None:
        self.state = new_state
