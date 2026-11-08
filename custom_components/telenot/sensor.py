"""Bounded diagnostics without duplicating the raw state model."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .entity import TelenotEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TelenotDiagnostic(entry.runtime_data, kind) for kind in
                        ("discovery", "occupied_points", "last_command")])


class TelenotDiagnostic(TelenotEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, kind):
        super().__init__(coordinator, kind)
        self.kind = kind
        self._attr_translation_key = kind
        if kind == "discovery":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["pending", "running", "complete", "partial", "failed", "disconnected"]
        if kind == "last_command":
            self._attr_entity_registry_enabled_default = False
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["queued", "acknowledged", "confirmed_by_status", "rejected", "unknown",
                                  "cancelled_or_unknown"]

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        client = self.coordinator.client
        if self.kind == "discovery":
            return client.discovery_status if client.connected else "disconnected"
        if self.kind == "occupied_points":
            return sum(len(a) for a in client.state.occupied.values()) if client.state.occupancy_confirmed else None
        return (client.last_command or {}).get("result")

    @property
    def extra_state_attributes(self):
        client = self.coordinator.client
        if self.kind == "discovery":
            return {"completed": client.discovery_done, "total": client.discovery_total,
                    "failed": len(client.discovery_failures), "last_error": client.last_error}
        if self.kind == "last_command":
            return client.last_command or {}
        return {"security_areas": len(client.state.areas)}
