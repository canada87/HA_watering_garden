"""Constants for the Solem BT Controller integration."""

DOMAIN = "solem_bt_controller"

CHARACTERISTIC_UUID = "108b0002-eab5-bc09-d0ea-0b8f467ce8ee"

DEFAULT_BLUETOOTH_TIMEOUT = 15  # seconds
MIN_BLUETOOTH_TIMEOUT = 5

CONF_CONTROLLER_MAC = "controller_mac_address"
CONF_NUM_STATIONS = "num_stations"
CONF_BLUETOOTH_TIMEOUT = "bluetooth_timeout"

DEFAULT_SAFETY_DURATION = 30  # minutes
MIN_SAFETY_DURATION = 1
MAX_SAFETY_DURATION = 240

MAX_STATIONS = 16
