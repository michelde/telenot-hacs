"""Exactly one alarm panel per configured security area."""

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .entity import TelenotEntity
from .protocol.client import ConnectionLost, Rejected, UncertainResult


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    added = set()

    @callback
    def discover():
        areas = set(coordinator.client.state.areas) - added
        if areas:
            added.update(areas)
            async_add_entities([TelenotAlarm(coordinator, area) for area in sorted(areas)])

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class TelenotAlarm(TelenotEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False
    _attr_code_format = None

    def __init__(self, coordinator, area):
        super().__init__(coordinator, f"area_{area}", extension=2, address=0x530 + 8*(area - 1))
        self.area = area
        self._attr_supported_features = (
            AlarmControlPanelEntityFeature.ARM_HOME | AlarmControlPanelEntityFeature.ARM_AWAY
            if coordinator.client.allow_control else AlarmControlPanelEntityFeature(0)
        )

    @property
    def name(self):
        info = self.coordinator.client.state.metadata_for(self.address)
        return info.name if info and info.name else self.coordinator.translated_name(
            "alarm_control_panel", "area", number=self.area
        )

    @property
    def alarm_state(self):
        if not self.available:
            return None
        mode = self.coordinator.client.state.area_mode(self.area)
        return AlarmControlPanelState(mode) if mode else None

    async def _set_mode(self, mode):
        try:
            await self.coordinator.client.set_mode(self.area, mode)
        except (PermissionError, ConnectionLost, Rejected, UncertainResult) as error:
            key = "control_disabled" if isinstance(error, PermissionError) else (
                "command_rejected" if isinstance(error, Rejected) else
                "command_uncertain" if isinstance(error, UncertainResult) else "area_unavailable"
            )
            raise HomeAssistantError(str(error), translation_domain=DOMAIN, translation_key=key) from error

    async def async_alarm_arm_away(self, code=None):
        await self._set_mode("armed_away")

    async def async_alarm_arm_home(self, code=None):
        await self._set_mode("armed_home")

    async def async_alarm_disarm(self, code=None):
        await self._set_mode("disarmed")
