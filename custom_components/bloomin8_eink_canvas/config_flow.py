"""Config flow for BLOOMIN8 E-Ink Canvas integration."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api_client import EinkCanvasApiClient
from .const import (
    DOMAIN,
    CONF_NAME,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_UNKNOWN,
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict) -> dict:
    """Validate the user input allows us to connect."""
    host = data[CONF_HOST]
    _LOGGER.info("Attempting to connect to device at: %s", host)

    api_client = EinkCanvasApiClient(hass, host)

    # Try to get device info to verify connection
    device_info = await api_client.get_device_info()
    if device_info is None:
        _LOGGER.error("Failed to connect to device at %s - no response from /deviceInfo endpoint", host)
        raise CannotConnect

    _LOGGER.info("Successfully connected to device at %s", host)
    return {"title": data[CONF_NAME]}

class EinkDisplayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BLOOMIN8 E-Ink Canvas."""

    VERSION = 1

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates=user_input,
                )
            except CannotConnect:
                errors["base"] = ERROR_CANNOT_CONNECT
            except InvalidAuth:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception as err:
                _LOGGER.exception("Unexpected error during reconfigure: %s", err)
                errors["base"] = ERROR_UNKNOWN

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=reconfigure_entry.data.get(CONF_HOST, "")): str,
                vol.Required(CONF_NAME, default=reconfigure_entry.data.get(CONF_NAME, "BLOOMIN8 E-Ink Canvas")): str,
            }),
            errors=errors,
        )

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input
                )
            except CannotConnect:
                errors["base"] = ERROR_CANNOT_CONNECT
            except InvalidAuth:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error during config flow: %s", err)
                errors["base"] = ERROR_UNKNOWN

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_NAME, default="BLOOMIN8 E-Ink Canvas"): str,
            }),
            errors=errors,
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
