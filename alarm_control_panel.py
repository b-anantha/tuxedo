"""Interfaces with Tuxedo control panel."""
import asyncio
from binascii import a2b_base64, a2b_hex, b2a_base64
import json
import logging
import urllib

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import requests
from requests.packages import urllib3

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.components.alarm_control_panel.const import (
    SUPPORT_ALARM_ARM_AWAY,
    SUPPORT_ALARM_ARM_HOME,
    SUPPORT_ALARM_ARM_NIGHT,
)
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_HOME,
    STATE_ALARM_ARMED_NIGHT,
    STATE_ALARM_ARMING,
    STATE_ALARM_DISARMED,
    STATE_UNAVAILABLE,
)

_LOGGER = logging.getLogger(__name__)

# to reduce warning messages when local polling for alarm status
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Cipher:
    """The Tuxedo Touch API encryption configuration."""

    def __init__(self, key, iv):
        """Convert key and iv to binary."""
        self._key_bin = a2b_hex(key)
        self._iv_bin = a2b_hex(iv)

    def encrypt_params(self, params):
        """Encrypt data params being sent to Tuxedo Touch."""
        params_encoded = urllib.parse.urlencode(params).encode("UTF-8")
        params_padded = pad(params_encoded, 16)

        cipher = AES.new(self._key_bin, AES.MODE_CBC, self._iv_bin)
        params_encrypted = cipher.encrypt(params_padded)

        return b2a_base64(params_encrypted).decode("UTF-8")

    def decrypt_params(self, params_encrypted):
        """Decrypt endpoint responses before parsing."""
        cipher = AES.new(self._key_bin, AES.MODE_CBC, self._iv_bin)
        params_decrypted = cipher.decrypt(a2b_base64(params_encrypted))
        params = unpad(params_decrypted, AES.block_size)

        return json.loads(params)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Tuxedo Touch alarm control panel."""
    async_add_entities(
        [
            TuxedoTouch(
                entry.data["ip_address"],
                entry.title,
                entry.data["secret_key"],
                entry.data["initial_value"],
                entry.data.get("code"),
            )
        ],
        update_before_add=True,
    )


class TuxedoTouch(alarm.AlarmControlPanelEntity):
    """Representation of a Tuxedo Touch alarm status."""

    def __init__(self, ip_address, name, secret_key, initial_value, code):
        """Initialize the Tuxedo Touch alarm panel."""

        self._attr_name = name
        self._attr_unique_id = secret_key
        self._attr_supported_features = (
            SUPPORT_ALARM_ARM_HOME | SUPPORT_ALARM_ARM_AWAY | SUPPORT_ALARM_ARM_NIGHT
        )
        self._attr_code_arm_required = False if code else True
        self._attr_code_format = None if code else alarm.FORMAT_NUMBER

        self._url = f"https://{ip_address}/system_http_api/API_REV01"
        self._key = secret_key
        self._iv = initial_value
        self._code = code

        self._cipher = Cipher(self._key, self._iv)

    def _post_request(self, endpoint, params):
        encrypted_params = self._cipher.encrypt_params(params)
        response = requests.post(
            url=self._url + endpoint,
            headers={"authtoken": self._key, "identity": self._iv},
            data={"param": encrypted_params},
            verify=False,
        )

        if response.status_code == 200:
            return self._cipher.decrypt_params(response.json()["Result"])
        else:
            _LOGGER.exception(f"endpoint {endpoint} returned a {response.status_code}")
        return

    async def async_update(self):
        """Return the state of the device."""
        state = None
        response = await self.hass.async_add_executor_job(
            self._post_request, "/GetSecurityStatus", {"operation": "get"}
        )
        if response:
            status = response["Status"]
            if status == "Armed Away":
                state = STATE_ALARM_ARMED_AWAY
            elif status == "Armed Instant":
                state = STATE_ALARM_ARMED_NIGHT
            elif status == "Armed Stay":
                state = STATE_ALARM_ARMED_HOME
            elif status == "Ready To Arm":
                state = STATE_ALARM_DISARMED
            elif status.endswith(" Secs Remaining"):
                state = STATE_ALARM_ARMING

            # Entry Delay Active - system is armed and will trigger if code is not entered in time
            # Not Ready Fault - system cannot be armed due to a detected fault

            _LOGGER.info(f"current alarm status: {status}")
        else:
            _LOGGER.exception("unable to get alarm status")

        self._attr_state = STATE_UNAVAILABLE if state is None else state

    async def _alarm_arm(self, arm_name, code):
        arm_code = code if code else self._code
        if arm_code is None:
            _LOGGER.warning("arm code is missing")
            return

        response = await self.hass.async_add_executor_job(
            self._post_request,
            "/AdvancedSecurity/ArmWithCode",
            {
                "arming": arm_name,
                "pID": "1",
                "ucode": str(arm_code),
                "operation": "set",
            },
        )

        if response:
            _LOGGER.info("api_arm_request %s result: %s", arm_name, response)
            await asyncio.sleep(2)
            self.async_schedule_update_ha_state()

    async def async_alarm_arm_away(self, code=None):
        """Send arm away command."""
        await self._alarm_arm("AWAY", code)

    async def async_alarm_arm_home(self, code=None):
        """Send arm home command."""
        await self._alarm_arm("STAY", code)

    async def async_alarm_arm_night(self, code=None):
        """Send arm night command."""
        await self._alarm_arm("NIGHT", code)

    async def async_alarm_disarm(self, code=None):
        """Send disarm command."""
        disarm_code = code if code else self._code
        if disarm_code is None:
            _LOGGER.warning("disarm code is missing")
            return

        response = await self.hass.async_add_executor_job(
            self._post_request,
            "/AdvancedSecurity/DisarmWithCode",
            {
                "pID": "1",
                "ucode": str(disarm_code),
                "operation": "set",
            },
        )

        if response:
            _LOGGER.info("api_disarm_request result: %s", response)
            await asyncio.sleep(2)
            self.async_schedule_update_ha_state()
