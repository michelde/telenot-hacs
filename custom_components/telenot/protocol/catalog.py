"""Address semantics; occupancy alone never proves physical device type."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    address: int
    kind: str
    label: str
    status_supported: bool = True
    diagnostic: bool = False
    device_class: str | None = None


def describe(address: int) -> Point:
    if 0x530 <= address <= 0x56F:
        return Point(address, "area", f"Area {(address - 0x530)//8 + 1}")
    if 0x570 <= address <= 0x5EF:
        return Point(address, "zone", f"Message area {address - 0x570 + 1}")
    if 0x5F0 <= address <= 0x66F:
        return Point(address, "bypass", f"Message area {address - 0x5F0 + 1} bypassed")
    if 0x500 <= address <= 0x52F or 0x670 <= address <= 0x77F:
        return Point(address, "output", f"Output 0x{address:04X}", diagnostic=True)
    if 0xB0 <= address <= 0xEF:
        offset = (address - 0xB0) % 4
        return Point(address, "input", f"Keypad {(address - 0xB0)//4}: " +
                     ("cover", "display cover", "no response", "special key")[offset],
                     offset != 3, True, "problem" if offset != 3 else None)
    if 0x380 <= address <= 0x397:
        return Point(address, "input", f"Mobile keypad {address - 0x380 + 1}", False, True)
    if 0xF0 <= address <= 0x16F or 0x398 <= address <= 0x417:
        base = 0xF0 if address < 0x170 else 0x398
        offset = (address - base) % 16
        return Point(address, "input", f"comlock input 0x{address:04X}", offset != 13,
                     offset >= 5, "problem" if offset in (5, 6, 7, 8, 9, 10, 12) else None)
    for base in (0, 0x170, 0x220, 0x2D0):
        if base <= address <= base + 0xAF:
            offset = address - base
            # Expansion slots may be radio mappings: do not impose comslave types.
            if base:
                return Point(address, "input", f"Input 0x{address:04X}",
                             diagnostic=offset >= 0xA7)
            if offset < 16 or 0x18 <= offset <= 0x27:
                number = offset + 1 if offset < 16 else offset - 7
                return Point(address, "input", f"Input group {number}")
            if 0x28 <= offset <= 0x66 or 0x68 <= offset <= 0xA6:
                return Point(address, "input", f"Bus input 0x{address:04X}")
            labels = {0x10: "Panel cover", 0x11: "Sounder 1 supervision",
                      0x12: "Sounder 2 supervision", 0x13: "Optical sounder supervision",
                      0x14: "Battery fault", 0x15: "Power fault", 0x17: "Communicator fault"}
            return Point(address, "input", labels.get(offset, f"Input 0x{address:04X}"),
                         offset < 0xA8, True, "problem" if offset in labels else None)
    return Point(address, "unknown", f"Point 0x{address:04X}", diagnostic=True)


def metadata_address(address: int) -> int:
    if 0x530 <= address <= 0x56F:
        return 0x530 + ((address - 0x530)//8)*8
    if 0x5F0 <= address <= 0x66F:
        return address - 0x80
    return address
