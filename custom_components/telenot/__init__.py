"""TELENOT complex 400 GMS integration.

Home Assistant imports are deferred so the protocol subpackage remains usable
without Home Assistant, including on small command-line test systems.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.exceptions import ConfigEntryNotReady

    from .const import PLATFORMS
    from .coordinator import TelenotCoordinator
    from .protocol.client import ConnectionLost

    coordinator = TelenotCoordinator(hass, entry)
    try:
        await coordinator.async_start()
    except ConnectionLost as error:
        await coordinator.async_close()
        raise ConfigEntryNotReady(str(error)) from error
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_close()
        raise
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .const import PLATFORMS

    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_close()
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from homeassistant.helpers.storage import Store

    from .const import DOMAIN

    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
