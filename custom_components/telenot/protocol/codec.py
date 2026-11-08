"""Binary GMS V6 framing and record codecs (PDF pages 6–17)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class ProtocolError(Exception):
    """Malformed or unexpected protocol data."""


@dataclass(frozen=True)
class Record:
    kind: int
    body: bytes

    def encode(self) -> bytes:
        if len(self.body) > 255:
            raise ProtocolError("Record too long")
        return bytes((len(self.body), self.kind)) + self.body


@dataclass(frozen=True)
class Frame:
    control: int
    address: int
    records: tuple[Record, ...] = ()

    def encode(self) -> bytes:
        body = bytes((self.control, self.address)) + b"".join(r.encode() for r in self.records)
        if len(body) > 255:
            raise ProtocolError("Frame too long")
        return bytes((0x68, len(body), len(body), 0x68)) + body + bytes((sum(body) & 255, 0x16))

    @property
    def function(self) -> int:
        return self.control & 0x0F

    @property
    def is_norm(self) -> bool:
        return bool(self.control & 0x40) and self.function == 0

    @property
    def is_data(self) -> bool:
        return bool(self.control & 0x40) and self.function == 3

    @property
    def is_ack(self) -> bool:
        return not self.control & 0x40 and self.function == 0

    @property
    def is_nak(self) -> bool:
        return not self.control & 0x40 and self.function == 1


ACK = Frame(0, 2).encode()
NORM = Frame(0x40, 2).encode()


def decode_records(payload: bytes) -> tuple[Record, ...]:
    result = []
    offset = 0
    fixed = {0x02: 5, 0x0C: 6, 0x10: 5, 0x11: 2, 0x50: 7, 0x56: 6}
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise ProtocolError("Truncated record header")
        length, kind = payload[offset:offset + 2]
        end = offset + length + 2
        if end > len(payload):
            raise ProtocolError("Record crosses frame boundary")
        if kind in fixed and length != fixed[kind]:
            raise ProtocolError(f"Invalid length for record {kind:02X}")
        if kind == 0x24 and length < 4:
            raise ProtocolError("Missing block header")
        result.append(Record(kind, payload[offset + 2:end]))
        offset = end
    return tuple(result)


class FrameParser:
    """Incremental stream parser. Never split on an end marker in the payload."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.invalid_frames = 0

    def reset(self) -> None:
        self.discarded_bytes += len(self.buffer)
        self.buffer.clear()

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 4:
            length = self.buffer[1]
            if (self.buffer[0] != 0x68 or self.buffer[3] != 0x68
                    or length < 2 or length != self.buffer[2]):
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            total = length + 6
            if len(self.buffer) < total:
                break
            raw = bytes(self.buffer[:total])
            if raw[-1] != 0x16 or sum(raw[4:-2]) & 255 != raw[-2]:
                self.invalid_frames += 1
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            del self.buffer[:total]
            try:
                result.append(Frame(raw[4], raw[5], decode_records(raw[6:-2])))
            except ProtocolError:
                self.invalid_frames += 1
        return result


def decode_identity(body: bytes) -> str:
    if len(body) != 6:
        raise ProtocolError("Invalid identity length")
    digits = []
    padding = False
    for byte in body:
        for digit in (byte & 15, byte >> 4):
            if digit == 15:
                padding = True
            elif digit > 9 or padding:
                raise ProtocolError("Invalid decimal identity")
            else:
                digits.append(str(digit))
    if not digits:
        raise ProtocolError("Empty identity")
    return "".join(digits)


def decode_text(raw: bytes) -> str:
    """Observed 0xF5=ü and 0xEF=ö; leave other non-ASCII bytes visibly unknown.

    The raw bytes are retained on Metadata. Do not assume a full legacy codepage.
    Normalize the observed padded two-line display text only for the entity name.
    """
    observed = {0xF5: "ü", 0xEF: "ö"}
    text = "".join(observed.get(b, chr(b) if 32 <= b <= 126 else " " if b == 0 else "�") for b in raw)
    return " ".join(text.split())


def decode_time(body: bytes) -> str | None:
    """Panel-local civil time without an invented timezone or UTC offset."""
    if len(body) != 7:
        return None
    try:
        return datetime(body[1] * 100 + body[0], *body[2:]).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class Block:
    base: int
    extension: int
    data: bytes

    @classmethod
    def from_record(cls, record: Record) -> Block:
        if record.kind != 0x24 or len(record.body) < 4:
            raise ProtocolError("Not a status block")
        body = record.body
        base = int.from_bytes(body[1:3], "big")
        if base + 8 * len(body[4:]) > 65536:
            raise ProtocolError("Block exceeds address space")
        return cls(base, body[3], body[4:])

    def value(self, address: int) -> bool | None:
        index = address - self.base
        if index < 0 or index >= 8 * len(self.data):
            return None
        return not bool(self.data[index // 8] & (1 << (index % 8)))

    def active_addresses(self) -> set[int]:
        return {self.base + 8*i + b for i, byte in enumerate(self.data)
                for b in range(8) if not byte & (1 << b)}


@dataclass(frozen=True)
class Metadata:
    address: int
    device_area: int
    areas_mask: int
    reporting_area: int
    text: bytes

    @property
    def areas(self) -> list[int]:
        return [b + 1 for b in range(8) if not self.areas_mask & (1 << b)]

    @property
    def name(self) -> str:
        return decode_text(self.text)


def metadata_from_frame(frame: Frame) -> Metadata | None:
    info = [r.body for r in frame.records if r.kind == 0x0C]
    texts = [r.body for r in frame.records if r.kind == 0x54]
    if len(info) != 1 or len(texts) != 1 or info[0][3] != 0x73:
        return None
    body = info[0]
    return Metadata(int.from_bytes(body[1:3], "big"), body[0], body[4], body[5], texts[0])


def occupancy_query() -> bytes:
    return Frame(0x73, 2, (Record(0x10, bytes.fromhex("00 00 00 71 24")),)).encode()


def metadata_query(address: int) -> bytes:
    if not 0 <= address <= 65535:
        raise ValueError("Invalid address")
    return Frame(0x73, 2, (Record(0x10, bytes((0, address >> 8, address & 255, 0x73, 0x0C))),)).encode()


MODES = {"disarmed": (0, 0xE1), "armed_home": (1, 0x62), "armed_away": (2, 0x61)}


def arm_command(area: int, mode: str) -> tuple[bytes, int]:
    if not 1 <= area <= 8 or mode not in MODES:
        raise ValueError("Invalid area or mode")
    offset, code = MODES[mode]
    address = 0x530 + 8*(area - 1) + offset
    body = bytes((0, address >> 8, address & 255, 2, code))
    return Frame(0x73, 1, (Record(2, body),)).encode(), address


ERRORS = {
    0x00: "General/frame error", 0x01: "Unknown control function",
    0x02: "Unknown message code", 0x10: "Address out of range",
    0x18: "Function unavailable at address", 0x19: "Point not configured",
    0x80: "Checksum error", 0xFF: "Unknown record type",
}
