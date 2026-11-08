"""Exercise actual Home Assistant 2026.11 helpers against a loopback gateway."""

import asyncio
from contextlib import suppress
from pathlib import Path
from types import MappingProxyType

import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    floor_registry,
    frame,
    label_registry,
)

from custom_components.telenot.alarm_control_panel import TelenotAlarm
from custom_components.telenot.diagnostics import async_get_config_entry_diagnostics

from .simulator import until


@pytest.fixture
async def hass(tmp_path):
    (tmp_path / "custom_components").symlink_to(Path("custom_components").resolve(), target_is_directory=True)
    instance = HomeAssistant(str(tmp_path))
    instance.config.skip_pip = True
    instance.config.time_zone = "Europe/Berlin"
    loader.async_setup(instance)
    frame.async_setup(instance)
    device_registry.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    for registry in (area_registry, device_registry, entity_registry, floor_registry, label_registry):
        await registry.async_load(instance)
    yield instance
    for entry in instance.config_entries.async_entries("telenot"):
        if hasattr(entry, "runtime_data"):
            await asyncio.wait_for(entry.runtime_data.async_close(), 3)
    with suppress(TimeoutError):
        await asyncio.wait_for(instance.async_stop(force=True), 5)


def make_entry(gateway, *, write=False):
    return ConfigEntry(
        domain="telenot", title="TELENOT 000815", version=1, minor_version=1,
        source="user", unique_id="000815", discovery_keys=MappingProxyType({}),
        subentries_data=(), data={"host": "127.0.0.1", "port": gateway.port, "identity": "000815"},
        options={"allow_control": write},
    )


async def test_setup_entities_persistence_unload_reload(hass, gateway):
    entry = make_entry(gateway)
    await hass.config_entries.async_add(entry)
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    await until(lambda: coordinator.client.discovery_status == "complete")
    await hass.async_block_till_done()
    states = hass.states.async_all("alarm_control_panel")
    assert len(states) == 1
    assert states[0].state == "disarmed"
    assert states[0].attributes["supported_features"] == 0
    registry = entity_registry.async_get(hass)
    before = {e.unique_id for e in registry.entities.values() if e.config_entry_id == entry.entry_id}
    assert "000815_area_1" in before
    assert "000815_point_1_0000" in before
    for domain, unique_id, name in (
        ("binary_sensor", "000815_point_1_0000", "Test point"),
        ("binary_sensor", "000815_point_2_0570", "Message area"),
        ("alarm_control_panel", "000815_area_1", "Security area"),
    ):
        entity_id = registry.async_get_entity_id(domain, "telenot", unique_id)
        assert registry.entities[entity_id].original_name == name
        assert hass.states.get(entity_id).attributes["friendly_name"].endswith(name)
    assert not gateway.commands
    diagnostic = await async_get_config_entry_diagnostics(hass, entry)
    assert "000815" not in str(diagnostic)
    assert "Test point" not in str(diagnostic)
    assert "127.0.0.1" not in str(diagnostic)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not coordinator.client.connected
    saved = await coordinator.store.async_load()
    assert saved["identity"] == "000815"
    assert "0" in saved["metadata"]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await until(lambda: entry.runtime_data.client.discovery_status == "complete")
    await hass.async_block_till_done()
    after = {e.unique_id for e in registry.entities.values() if e.config_entry_id == entry.entry_id}
    assert after == before
    assert not gateway.commands
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_alarm_unknown_not_disarmed_and_write_gate(hass, gateway):
    entry = make_entry(gateway)
    await hass.config_entries.async_add(entry)
    coordinator = entry.runtime_data
    await until(lambda: coordinator.client.discovery_status == "complete")
    alarm = TelenotAlarm(coordinator, 1)
    assert alarm.alarm_state == "disarmed"
    gateway.enable_polls.clear()
    coordinator.client.state.disconnected()
    assert not alarm.available and alarm.alarm_state is None
    with pytest.raises(HomeAssistantError, match="Writing is disabled"):
        await alarm.async_alarm_disarm()
    assert not gateway.commands
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow_and_identity_duplicate(hass, gateway):
    result = await hass.config_entries.flow.async_init("telenot", context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port,
    })
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.unique_id == "000815"
    await hass.async_block_till_done()
    duplicate = await hass.config_entries.flow.async_init("telenot", context={"source": "user"}, data={
        "host": "127.0.0.1", "port": gateway.port,
    })
    assert duplicate["type"] == "abort" and duplicate["reason"] == "already_configured"
    assert not gateway.commands
    await hass.config_entries.async_unload(entry.entry_id)


async def test_ha_service_arm_and_live_binary_sensor(hass, gateway):
    entry = make_entry(gateway, write=True)
    await hass.config_entries.async_add(entry)
    coordinator = entry.runtime_data
    await until(lambda: coordinator.client.discovery_status == "complete")
    await hass.async_block_till_done()
    registry = entity_registry.async_get(hass)
    alarm_id = registry.async_get_entity_id("alarm_control_panel", "telenot", "000815_area_1")
    input_id = registry.async_get_entity_id("binary_sensor", "telenot", "000815_point_1_0000")
    assert hass.states.get(input_id).state == "off"
    gateway.input_data[0] &= ~1
    await until(lambda: hass.states.get(input_id).state == "on")
    await hass.services.async_call("alarm_control_panel", "alarm_arm_home",
                                   {"entity_id": alarm_id}, blocking=True)
    assert hass.states.get(alarm_id).state == "armed_home"
    assert gateway.commands == [bytes.fromhex("05 02 00 05 31 02 62")]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_closes_probe_before_reloading(hass, gateway):
    entry = make_entry(gateway)
    await hass.config_entries.async_add(entry)
    await until(lambda: entry.runtime_data.client.discovery_status == "complete")
    result = await hass.config_entries.flow.async_init("telenot", context={
        "source": "reconfigure", "entry_id": entry.entry_id,
    })
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "host": "127.0.0.1", "port": gateway.port,
    })
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert not gateway.commands
    await hass.config_entries.async_unload(entry.entry_id)


async def test_marketplace_german_names_preserve_panel_text(hass, gateway):
    hass.config.language = "de"
    gateway.names[0x515] = b""  # Unnamed output must use a translated fallback.
    entry = make_entry(gateway)
    await hass.config_entries.async_add(entry)
    coordinator = entry.runtime_data
    await until(lambda: coordinator.client.discovery_status == "complete")
    await hass.async_block_till_done()
    registry = entity_registry.async_get(hass)
    for domain, unique_id, expected_name in (
        ("binary_sensor", "000815_connection", "Gatewayverbindung"),
        ("binary_sensor", "000815_point_1_0000", "Test point"),
        ("binary_sensor", "000815_point_2_0515", "Ausgang 0x0515"),
        ("binary_sensor", "000815_point_2_05f0", "Message area gesperrt"),
        ("alarm_control_panel", "000815_area_1", "Security area"),
        ("button", "000815_rediscover", "Discovery aktualisieren"),
        ("sensor", "000815_occupied_points", "Belegte Positionen"),
        ("sensor", "000815_last_command", "Letzter Befehl"),
    ):
        entity_id = registry.async_get_entity_id(domain, "telenot", unique_id)
        assert registry.entities[entity_id].original_name == expected_name
    assert coordinator.translated_name("alarm_control_panel", "area", number=2) == "Sicherungsbereich 2"
    assert coordinator.translations["component.telenot.entity.sensor.discovery.state.complete"] == "Vollständig"
    assert not gateway.commands
    await hass.config_entries.async_unload(entry.entry_id)
