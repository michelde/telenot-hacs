"""Read-only points, outputs, message areas, and area diagnostics."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .entity import TelenotEntity
from .protocol.catalog import describe

AREA_KEYS = {3: "area_alarm", 4: "area_fault", 5: "area_ready_home", 6: "area_ready_away", 7: "area_buzzer"}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    added = set()
    async_add_entities([GatewayConnection(coordinator)])

    @callback
    def discover():
        entities = []
        for extension, occupancy in ((1, 0x71), (2, 0x72)):
            for address in sorted(coordinator.client.state.occupied[occupancy]):
                key = (extension, address)
                point = describe(address)
                if key in added or not point.status_supported:
                    continue
                if point.kind == "area" and (address - 0x530) % 8 < 3:
                    continue
                added.add(key)
                entities.append(TelenotBinarySensor(coordinator, extension, point))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class GatewayConnection(TelenotEntity, BinarySensorEntity):
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "connection")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        client = self.coordinator.client
        return client.connected and client.verified_identity and client.state.fresh(1) and client.state.fresh(2)


class TelenotBinarySensor(TelenotEntity, BinarySensorEntity):
    def __init__(self, coordinator, extension, point):
        super().__init__(coordinator, f"point_{extension}_{point.address:04x}",
                         extension=extension, address=point.address)
        self.point = point
        if point.device_class:
            self._attr_device_class = BinarySensorDeviceClass(point.device_class)
        self._attr_entity_registry_enabled_default = not point.diagnostic
        if point.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if point.kind == "area":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False

    @property
    def name(self):
        info = self.coordinator.client.state.metadata_for(self.address)
        translate = self.coordinator.translated_name
        if self.point.kind == "area":
            number = (self.address - 0x530)//8 + 1
            name = info.name if info and info.name else translate("alarm_control_panel", "area", number=number)
            return translate("binary_sensor", AREA_KEYS[(self.address - 0x530) % 8], name=name)
        if self.point.kind == "bypass":
            name = info.name if info and info.name else translate(
                "binary_sensor", "message_area", number=self.address - 0x5F0 + 1
            )
            return translate("binary_sensor", "bypass", name=name)
        if info and info.name:
            return info.name
        if self.point.kind == "zone":
            return translate("binary_sensor", "message_area", number=self.address - 0x570 + 1)
        key = "output" if self.point.kind == "output" else "input" if self.point.kind == "input" else "point"
        return translate("binary_sensor", key, address=f"0x{self.address:04X}")

    @property
    def is_on(self):
        return self.coordinator.client.state.value(self.extension, self.address) if self.available else None
