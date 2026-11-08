import json
from pathlib import Path

import pytest

from custom_components.telenot.protocol.codec import (
    ACK,
    NORM,
    Block,
    Frame,
    FrameParser,
    ProtocolError,
    Record,
    arm_command,
    decode_identity,
    decode_records,
    decode_text,
    decode_time,
    metadata_from_frame,
    metadata_query,
    occupancy_query,
)

RESTART = bytes.fromhex(
    "68 1A 1A 68 73 02 05 02 00 FF FF 01 53 07 50 05 14 03 0A 0B 10 39 "
    "06 56 00 80 51 FF FF FF C9 16"
)


@pytest.mark.parametrize(("actual", "expected"), [
    (ACK, "68 02 02 68 00 02 02 16"),
    (NORM, "68 02 02 68 40 02 42 16"),
    (occupancy_query(), "68 09 09 68 73 02 05 10 00 00 00 71 24 1F 16"),
    (metadata_query(7), "68 09 09 68 73 02 05 10 00 00 07 73 0C 10 16"),
    (metadata_query(0x530), "68 09 09 68 73 02 05 10 00 05 30 73 0C 3E 16"),
    (arm_command(1, "armed_away")[0], "68 09 09 68 73 01 05 02 00 05 32 02 61 15 16"),
    (arm_command(1, "armed_home")[0], "68 09 09 68 73 01 05 02 00 05 31 02 62 15 16"),
    (arm_command(1, "disarmed")[0], "68 09 09 68 73 01 05 02 00 05 30 02 E1 93 16"),
])
def test_pdf_frames(actual, expected):
    assert actual == bytes.fromhex(expected)


def test_all_fragment_boundaries_and_concatenation():
    data = b"junk" + NORM + RESTART + ACK
    for boundary in range(len(data) + 1):
        parser = FrameParser()
        frames = parser.feed(data[:boundary]) + parser.feed(data[boundary:])
        assert [frame.encode() for frame in frames] == [NORM, RESTART, ACK]
    parser = FrameParser()
    frames = [frame for byte in data for frame in parser.feed(bytes([byte]))]
    assert len(frames) == 3


def test_corruption_and_embedded_markers():
    corrupted = bytearray(RESTART)
    corrupted[-2] ^= 1
    good = Frame(0x73, 2, (Record(0x54, bytes([0x68, 0x16])),)).encode()
    parser = FrameParser()
    parsed = parser.feed(b"\x68\x01\x01\x68\x00\x00\x16" + bytes(corrupted) + good)
    assert [f.encode() for f in parsed] == [good]
    assert parser.invalid_frames >= 1


def test_record_bounds_and_id_not_part_of_status():
    decoded = decode_records(bytes.fromhex("05 24 00 05 00 02 FF 06 56 00 80 51 FF FF FF"))
    assert len(decoded) == 2
    block = Block.from_record(decoded[0])
    assert block.base == 0x500 and block.data == b"\xff"
    assert block.value(0x500) is False
    assert block.value(0x508) is None
    assert Block(0x5F0, 2, b"\xfb").active_addresses() == {0x5F2}
    for bad in (b"\x06", b"\x06\x56\x00", b"\x04\x02\x00\x00\x00\x00"):
        with pytest.raises(ProtocolError):
            decode_records(bad)


@pytest.mark.parametrize(("raw", "expected"), [
    ("00 80 51 FF FF FF", "000815"), ("21 43 F5 FF FF FF", "12345"),
    ("21 43 65 87 F9 FF", "123456789"), ("21 43 65 87 09 21", "123456789012"),
])
def test_identity(raw, expected):
    assert decode_identity(bytes.fromhex(raw)) == expected


@pytest.mark.parametrize("raw", ["FF FF FF FF FF FF", "A1 FF FF FF FF FF", "F1 23 FF FF FF FF"])
def test_invalid_identity(raw):
    with pytest.raises(ProtocolError):
        decode_identity(bytes.fromhex(raw))


def test_text_time_and_area_eight():
    assert decode_text(b"T\xf5r EG".ljust(16) + b"Test".ljust(16)) == "Tür EG Test"
    assert decode_text(b"\x81") == "�"
    assert decode_time(bytes.fromhex("0D 14 06 1A 0D 2E 15")) == "2013-06-26T13:46:21"
    assert decode_time(bytes.fromhex("0D 14 FF 1A 0D 2E 15")) is None
    raw, target = arm_command(8, "armed_away")
    assert target == 0x56A
    assert raw[6:-2] == bytes.fromhex("05 02 00 05 6A 02 61")
    with pytest.raises(ValueError):
        arm_command(9, "armed_away")


def test_variable_ack_with_error_and_unknown_record():
    parsed = FrameParser().feed(bytes.fromhex("68 06 06 68 00 02 02 11 00 10 25 16"))[0]
    assert parsed.is_ack and parsed.records[0].body == bytes([0, 16])
    assert decode_records(bytes([1, 0x99, 0xFE])) == (Record(0x99, b"\xfe"),)


def test_frame_limit():
    maximum = Frame(0x73, 2, (Record(0x54, b"x" * 251),)).encode()
    assert len(maximum) == 261
    assert FrameParser().feed(maximum)[0].encode() == maximum
    with pytest.raises(ProtocolError):
        Frame(0x73, 2, (Record(0x54, b"x" * 252),)).encode()


def test_anonymized_actual_metadata_shapes():
    fixtures = json.loads((Path(__file__).parent / "fixtures" / "metadata_frames.json").read_text())
    for item in fixtures["frames"]:
        raw = bytes.fromhex(item["hex"])
        assert len(raw) == item["frame_length"]
        frame = FrameParser().feed(raw)[0]
        info = metadata_from_frame(frame)
        assert info.address == 3
        assert info.name == item["name"]
        assert info.areas == item["areas"]
        assert info.reporting_area == item["message_area"]
        assert decode_identity(frame.records[-1].body) == "000815"


@pytest.mark.parametrize(("raw", "expected"), [
    (b"St\xefrung".ljust(16), "Störung"),
    (b"T\xf5r".ljust(16) + b"St\xefrung".ljust(16), "Tür Störung"),
    (b"Unknown \x81", "Unknown �"),
])
def test_observed_extended_text_characters(raw, expected):
    assert decode_text(raw) == expected
