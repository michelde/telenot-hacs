import asyncio

import pytest

from custom_components.telenot.protocol.client import ConnectionLost, GMSClient, Rejected, UncertainResult

from .simulator import until


def client_for(gateway, **kwargs):
    return GMSClient("127.0.0.1", gateway.port, ack_timeout=0.2, response_timeout=0.5,
                     reconnect_delay=0.02, idle_timeout=1, **kwargs)


async def test_discovery_fragmentation_correlation_and_read_only(gateway):
    gateway.inject_unrelated = True
    client = client_for(gateway)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        assert client.state.identity == "000815"
        assert client.state.areas == [1]
        assert len(client.state.blocks[1].data) == 131
        assert len(client.state.blocks[2].data) == 80
        assert client.state.metadata[3].name == "Tür Test"
        assert client.state.metadata[3].areas == [1]
        assert client.state.metadata[3].reporting_area == 1
        assert 0x5555 not in client.state.metadata
        assert client.state.metadata[0].name != "Operator text"
        assert client.discovery_total == 5
        assert not gateway.commands
        assert not client.counters["invalid_frames"]
        with pytest.raises(PermissionError):
            await client.set_mode(1, "disarmed")
        assert not gateway.commands
    finally:
        await client.stop()
    assert not client.connected and not client.state.fresh(1)


async def test_panel_wins_collision_and_discovery_continues(gateway):
    gateway.collide_once = True
    client = client_for(gateway)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        assert client.counters["collisions"] == 1
        assert client.state.occupancy_confirmed
        assert not gateway.commands
    finally:
        await client.stop()


async def test_rejected_metadata_yields_partial_discovery(gateway):
    gateway.reject_metadata.add(3)
    client = client_for(gateway)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "partial")
        assert client.state.occupancy_confirmed
        assert client.discovery_done == client.discovery_total
        assert 3 in client.discovery_failures
        assert 0 in client.state.metadata
    finally:
        await client.stop()


async def test_explicit_arm_requires_ack_and_following_status(gateway):
    client = client_for(gateway, allow_control=True)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        assert not gateway.commands
        result = await client.set_mode(1, "armed_away")
        assert result == "confirmed_by_status"
        assert gateway.commands == [bytes.fromhex("05 02 00 05 32 02 61")]
        assert client.state.area_mode(1) == "armed_away"
        assert client.last_command["result"] == "confirmed_by_status"
    finally:
        await client.stop()


async def test_error_inside_ack_is_not_success(gateway):
    gateway.reject_command = True
    client = client_for(gateway, allow_control=True)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        with pytest.raises(Rejected) as error:
            await client.set_mode(1, "armed_home")
        assert error.value.code == 0x18
        assert client.last_command["result"] == "rejected"
        assert len(gateway.commands) == 1
    finally:
        await client.stop()


async def test_lost_ack_is_uncertain_and_never_replayed(gateway):
    gateway.drop_command_ack = True
    client = client_for(gateway, allow_control=True)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        with pytest.raises(UncertainResult):
            await client.set_mode(1, "armed_home")
        assert client.last_command["result"] == "unknown"
        await until(lambda: gateway.connections >= 2 and client.state.occupancy_confirmed)
        assert len(gateway.commands) == 1
    finally:
        await client.stop()


async def test_reconnect_refreshes_occupancy_without_duplicate_metadata_scan(gateway):
    client = client_for(gateway)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        before = len([q for q in gateway.queries if q[-1] == 0x0C])
        gateway.occupied_inputs.remove(3)
        await gateway.disconnect_clients()
        await until(lambda: gateway.connections >= 2 and 3 not in client.state.occupied[0x71])
        await until(lambda: client.discovery_status == "complete")
        assert 3 not in client.state.metadata
        assert len([q for q in gateway.queries if q[-1] == 0x0C]) == before
    finally:
        await client.stop()


async def test_cancelled_queued_command_is_not_sent(gateway):
    client = client_for(gateway, allow_control=True)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        gateway.enable_polls.clear()
        client._window.clear()
        task = asyncio.create_task(client.set_mode(1, "armed_home"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gateway.enable_polls.set()
        await until(lambda: client._queue.empty())
        assert not gateway.commands
    finally:
        await client.stop()


async def test_changed_identity_never_adopts_other_panel(gateway):
    client = client_for(gateway)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        gateway.ident = bytes.fromhex("06 56 99 99 99 FF FF FF")
        await gateway.disconnect_clients()
        await until(lambda: client.discovery_status == "failed")
        assert client.state.identity == "000815"
        assert not client.connected and not client.verified_identity
        assert not client.state.occupancy_confirmed
        assert not gateway.commands
    finally:
        await client.stop()


async def test_unconfigured_area_cannot_be_controlled(gateway):
    client = client_for(gateway, allow_control=True)
    try:
        await client.start()
        await until(lambda: client.discovery_status == "complete")
        with pytest.raises(ConnectionLost):
            await client.set_mode(2, "armed_away")
        assert not gateway.commands
    finally:
        await client.stop()
