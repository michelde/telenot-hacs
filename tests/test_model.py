from custom_components.telenot.protocol.catalog import describe, metadata_address
from custom_components.telenot.protocol.codec import Block, Frame, Metadata, Record
from custom_components.telenot.protocol.model import PanelState


def test_cache_never_restores_availability_or_other_identity():
    state = PanelState("000815")
    state.update_block(Block(0, 1, b"\xfe"))
    state.update_block(Block(0x530, 2, b"\x9e"))
    state.occupied[0x72] = set(range(0x530, 0x538))
    state.occupancy_confirmed = True
    state.metadata[0] = Metadata(0, 1, 0xFE, 1, b"Contact")
    state.metadata_at[0] = 100
    data = state.export()
    restored = PanelState("000815")
    assert restored.restore(data)
    assert restored.value(1, 0) is True
    assert not restored.fresh(1) and not restored.occupancy_confirmed
    assert restored.areas == [1]
    assert not PanelState("999").restore(data)
    data["blocks"]["1"]["hex"] = "invalid"
    assert not restored.restore(data)
    assert restored.value(1, 0) is True


def test_changes_events_and_conflicting_area_states():
    state = PanelState()
    block = Block(0, 1, b"\xff")
    state.update_block(block)
    before = dict(state.changed_at)
    state.update_block(block)
    assert state.changed_at == before
    state.record_events(Frame(0x73, 2, (
        Record(2, bytes.fromhex("01 00 00 01 22")), Record(0x54, b"Operator"),
    )))
    assert state.events[-1]["active"]
    assert state.value(1, 0) is False  # Alarm does not overwrite the physical input.
    state.update_block(Block(0x530, 2, b"\x9e"))
    assert state.area_mode(1) == "disarmed"
    state.update_block(Block(0x530, 2, b"\xff"))
    assert state.area_mode(1) is None
    state.update_block(Block(0x530, 2, b"\xfc"))
    assert state.area_mode(1) is None  # Two conflicting mode bits.
    state.update_block(Block(0x530, 2, b"\xf7"))
    assert state.area_mode(1) == "triggered"


def test_catalog_boundaries():
    assert describe(0x0F).label == "Input group 16"
    assert describe(0x18).label == "Input group 17"
    assert describe(0x10).label == "Panel cover"
    assert not describe(0xB3).status_supported
    assert not describe(0x380).status_supported
    assert describe(0x408).kind == "input"
    assert describe(0x777).kind == "output"
    assert metadata_address(0x56F) == 0x568
    assert metadata_address(0x66F) == 0x5EF


def test_readable_export_has_independent_names_and_retains_wire_text():
    state = PanelState("000815")
    state.occupied[0x71] = {0}
    state.occupied[0x72] = {*range(0x530, 0x538), 0x570, 0x5F0, 0x515}
    for address, text in ((0, b"Point name"), (0x570, b"Message area name"),
                          (0x530, b"Alarm area name"), (0x515, b" " * 16)):
        state.metadata[address] = Metadata(address, 0, 0xFE, 1, text)
    exported = state.export()
    assert exported["metadata"]["0"]["name"] == "Point name"
    assert exported["metadata"]["0"]["text"] == b"Point name".hex()
    inventory = exported["inventory"]
    assert inventory["input_points"][0]["name"] == "Point name"
    assert inventory["message_areas"][0]["name"] == "Message area name"
    assert inventory["security_areas"][0]["name"] == "Alarm area name"
    assert inventory["message_areas"][0]["bypass_address_hex"] == "0x05F0"
    assert inventory["outputs"][0]["name_source"] == "fallback"
    assert inventory["outputs"][0]["configured_name"] == ""
    # Restore raw source fields, not edited human-facing values.
    exported["metadata"]["0"]["name"] = "Wrong derived name"
    restored = PanelState("000815")
    assert restored.restore(exported)
    assert restored.metadata[0].name == "Point name"
