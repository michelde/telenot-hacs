"""Push updates, periodic freshness checks, and Home Assistant storage."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_ALLOW_CONTROL, DEFAULT_PORT, DOMAIN
from .protocol.client import GMSClient

_LOGGER = logging.getLogger(__name__)


class TelenotCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=5))
        self.entry = entry
        self.client = GMSClient(
            entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT),
            identity=entry.data["identity"],
            allow_control=entry.options.get(CONF_ALLOW_CONTROL, False),
            on_update=self._on_update,
        )
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}", private=True, atomic_writes=True)
        self._save_scheduled = False
        self._revision = 0
        self._closed = False
        self._refresh_task = None
        self._unsubscribe_stop = None
        self.translations = {}

    async def async_start(self):
        self.translations = await async_get_translations(self.hass, self.hass.config.language, "entity", {DOMAIN})
        if cached := await self.store.async_load():
            if not self.client.state.restore(cached):
                _LOGGER.warning("Ignoring invalid or mismatched TELENOT cache")
        await self.client.start()
        self._unsubscribe_stop = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._on_stop
        )

    def translated_name(self, platform, key, **placeholders):
        """Translate fallback/suffix names while keeping configured panel text intact."""
        template = self.translations.get(f"component.{DOMAIN}.entity.{platform}.{key}.name", key)
        return template.format(**placeholders)

    async def _on_stop(self, event):
        await self.async_close()

    @callback
    def _on_update(self, reason):
        if self._closed:
            return
        self._revision += 1
        self.async_set_updated_data({"revision": self._revision})
        if not self._save_scheduled:
            self._save_scheduled = True
            self.store.async_delay_save(self._snapshot, 30)
        if reason == "event":
            for event in self.client.latest_events:
                self.hass.bus.async_fire("telenot_event", {
                    "entry_id": self.entry.entry_id,
                    "panel_id": self.client.state.identity,
                    **event,
                })

    @callback
    def _snapshot(self):
        self._save_scheduled = False
        return self.client.state.export()

    async def _async_update_data(self):
        # Freshness checks only: no competing reader or unnecessary network poll.
        self._revision += 1
        return {"revision": self._revision}

    async def async_rediscover(self):
        if self.client.discovery_status == "running" or self._refresh_task:
            return
        self._refresh_task = self.hass.async_create_background_task(
            self.client.rediscover(force=True), "telenot-manual-discovery"
        )
        self._refresh_task.add_done_callback(self._refresh_finished)

    @callback
    def _refresh_finished(self, task):
        self._refresh_task = None

    async def async_close(self):
        if self._closed:
            return
        self._closed = True
        if self._unsubscribe_stop:
            self._unsubscribe_stop()
        if self._refresh_task:
            self._refresh_task.cancel()
            import asyncio
            from contextlib import suppress
            with suppress(asyncio.CancelledError):
                await self._refresh_task
        await self.client.stop()
        await self.store.async_save(self._snapshot())
