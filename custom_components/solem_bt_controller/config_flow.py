"""Config flow for the Solem BT Controller integration."""

import re

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .api import APIConnectionError, SolemBleApi
from .const import (
    CONF_BLUETOOTH_TIMEOUT,
    CONF_CONTROLLER_MAC,
    CONF_NUM_STATIONS,
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEFAULT_SAFETY_DURATION,
    DOMAIN,
    MAX_SAFETY_DURATION,
    MAX_STATIONS,
    MIN_BLUETOOTH_TIMEOUT,
    MIN_SAFETY_DURATION,
)

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class SolemConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a Solem BT Controller."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        """Step 1: MAC address and number of stations."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_CONTROLLER_MAC].strip().upper()

            if not MAC_REGEX.match(mac):
                errors["base"] = "invalid_mac"
            else:
                # Test BLE connection
                api = SolemBleApi(mac)
                try:
                    await api.list_characteristics()
                except APIConnectionError:
                    errors["base"] = "cannot_connect"

            if not errors:
                user_input[CONF_CONTROLLER_MAC] = mac
                self._data.update(user_input)

                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()

                return await self.async_step_safety_durations()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONTROLLER_MAC): str,
                    vol.Required(CONF_NUM_STATIONS, default=4): vol.All(
                        int, vol.Range(min=1, max=MAX_STATIONS)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_safety_durations(self, user_input=None):
        """Step 2: safety duration per station."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title=f"Solem {self._data[CONF_CONTROLLER_MAC][-5:]}",
                data=self._data,
            )

        num = self._data[CONF_NUM_STATIONS]
        schema = {}
        for i in range(1, num + 1):
            schema[
                vol.Required(
                    f"station_{i}_safety_duration", default=DEFAULT_SAFETY_DURATION
                )
            ] = vol.All(
                int, vol.Range(min=MIN_SAFETY_DURATION, max=MAX_SAFETY_DURATION)
            )

        return self.async_show_form(
            step_id="safety_durations",
            data_schema=vol.Schema(schema),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return SolemOptionsFlow(config_entry)


class SolemOptionsFlow(config_entries.OptionsFlow):
    """Handle options (bluetooth timeout + safety durations)."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_timeout = self.config_entry.options.get(
            CONF_BLUETOOTH_TIMEOUT, DEFAULT_BLUETOOTH_TIMEOUT
        )
        num_stations = self.config_entry.data.get(CONF_NUM_STATIONS, 4)

        schema = {
            vol.Required(
                CONF_BLUETOOTH_TIMEOUT, default=current_timeout
            ): vol.All(
                int,
                vol.Range(min=MIN_BLUETOOTH_TIMEOUT, max=60),
            ),
        }

        # Safety duration per station
        for i in range(1, num_stations + 1):
            key = f"station_{i}_safety_duration"
            current_val = self.config_entry.options.get(
                key,
                self.config_entry.data.get(key, DEFAULT_SAFETY_DURATION),
            )
            schema[
                vol.Required(key, default=current_val)
            ] = vol.All(
                int,
                vol.Range(min=MIN_SAFETY_DURATION, max=MAX_SAFETY_DURATION),
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
        )
