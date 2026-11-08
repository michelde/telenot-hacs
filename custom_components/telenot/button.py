"""Explicit read-only metadata refresh; no reset/actuator buttons in V1."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import TelenotEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RediscoverButton(entry.runtime_data)])


class RediscoverButton(TelenotEntity, ButtonEntity):
    _attr_translation_key = "rediscover"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, "rediscover")

    async def async_press(self):
        await self.coordinator.async_rediscover()
