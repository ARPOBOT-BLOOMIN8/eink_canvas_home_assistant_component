"""Config flow for BLOOMIN8 E-Ink Canvas integration."""
from __future__ import annotations

import asyncio
import aiohttp
import async_timeout
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    CONF_NAME,
    ENDPOINT_STATUS,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_UNKNOWN,
)

async def validate_input(hass: HomeAssistant, data: dict) -> dict:
    """Validate the user input allows us to connect."""
    headers = {
        "Accept": "*/*",
        "User-Agent": "Home Assistant",
    }
    
    try:
        async with async_timeout.timeout(10):
            async with aiohttp.ClientSession() as session:
                # Try to connect to the status endpoint
                async with session.get(
                    f"http://{data[CONF_HOST]}{ENDPOINT_STATUS}",
                    headers=headers,
                    ssl=False  # In case it's using self-signed cert
                ) as response:
                    # Any successful response means the device is accessible
                    if response.status < 400:
                        return {"title": data[CONF_NAME]}
                    elif response.status == 401 or response.status == 403:
                        raise InvalidAuth
                    else:
                        raise CannotConnect

    except asyncio.TimeoutError:
        raise CannotConnect
    except aiohttp.ClientResponseError as err:
        if err.status in (401, 403):
            raise InvalidAuth
        raise CannotConnect
    except Exception as err:  # pylint: disable=broad-except
        raise CannotConnect from err

class EinkDisplayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BLOOMIN8 E-Ink Canvas."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
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
            except Exception:  # pylint: disable=broad-except
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
