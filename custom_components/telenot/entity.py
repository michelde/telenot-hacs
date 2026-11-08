"""Common entity identity, device grouping and live availability."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class TelenotEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, *, extension=None, address=None):
        super().__init__(coordinator)
        self.extension = extension
        self.address = address
        ident = coordinator.entry.data["identity"]
        self._attr_unique_id = f"{ident}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ident)}, name=f"TELENOT {ident}",
            manufacturer="TELENOT", model="complex 400 / 400h (GMS)",
        )

    @property
    def available(self):
        client = self.coordinator.client
        if not client.connected or not client.verified_identity:
            return False
        if self.extension is None:
            return True
        occupancy = 0x71 if self.extension == 1 else 0x72
        return (client.state.occupancy_confirmed and client.state.fresh(self.extension)
                and self.address in client.state.occupied[occupancy]
                and client.state.value(self.extension, self.address) is not None)

    @property
    def extra_state_attributes(self):
        if self.address is None:
            return {}
        state = self.coordinator.client.state
        attributes = {"gms_address": f"0x{self.address:04X}"}
        if self.extension:
            attributes["last_changed_at"] = state.changed_at.get(f"{self.extension}:{self.address}")
        if info := state.metadata_for(self.address):
            attributes["security_areas"] = info.areas
            attributes["message_area"] = info.reporting_area or None
        return attributes
