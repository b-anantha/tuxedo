"""Config flow for Tuxedo Touch integration."""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

from bs4 import BeautifulSoup
import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CODE, CONF_IP_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IP_ADDRESS): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_CODE): cv.positive_int,
    }
)


def _obtain_key(ip_address: str) -> tuple[str, str]:
    """Obtain the API key for making subsequent calls."""
    try:
        readit = requests.get(f"http://{ip_address}/TuxedoAPI")
        if readit.status_code != 200:
            _LOGGER.warning(f"request to obtain key returned code {readit.status_code}")
            raise CannotConnect from ConnectionError

        soup = BeautifulSoup(readit.text, "lxml")
        value = soup.find("input")["value"]

        return value[:64], value[64:]
    except requests.exceptions.ConnectionError:
        raise CannotConnect from requests.exceptions.ConnectionError
    except TypeError:
        raise CannotObtainkey from TypeError
    except KeyError:
        raise CannotObtainkey from KeyError


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""

    ip_address = data[CONF_IP_ADDRESS]
    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        raise InvalidAddress from ValueError

    code = data.get(CONF_CODE)
    if code is not None:
        try:
            int(code)
        except ValueError:
            raise InvalidCode from ValueError

        if len(str(code)) != 4:
            raise InvalidCode from ValueError

    title = data[CONF_NAME] if data.get(CONF_NAME) else "Tuxedo Touch Controller"
    secret_key, initial_value = await hass.async_add_executor_job(
        _obtain_key, ip_address
    )

    return {
        "ip_address": ip_address,
        "title": title,
        "secret_key": secret_key,
        "initial_value": initial_value,
        "code": code,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuxedo Touch."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except CannotObtainkey:
            errors["base"] = "cannot_obtain_key"
        except InvalidAddress:
            errors["base"] = "invalid_address"
        except InvalidCode:
            errors["base"] = "invalid_code"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            user_input["ip_address"] = info["ip_address"]
            user_input["secret_key"] = info["secret_key"]
            user_input["initial_value"] = info["initial_value"]
            return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class CannotObtainkey(HomeAssistantError):
    """Error to indicate API key cannot be obtaied."""


class InvalidAddress(HomeAssistantError):
    """Error to indicate an invalid ip address was provided."""


class InvalidCode(HomeAssistantError):
    """Error to indicate an invalid code was provided."""
