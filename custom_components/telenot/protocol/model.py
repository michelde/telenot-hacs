"""Live and persisted state; cached data never restores live availability."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict

from .catalog import describe, metadata_address
from .codec import Block, Frame, Metadata, decode_text, decode_time


class PanelState:
    def __init__(self, identity: str | None = None) -> None:
        self.identity = identity
        self.blocks: dict[int, Block] = {}
        self.received_at: dict[int, float] = {}
        self.live_at: dict[int, float] = {}
        self.changed_at: dict[str, float] = {}
        self.occupied: dict[int, set[int]] = {0x71: set(), 0x72: set()}
        self.occupancy_confirmed = False
        self.metadata: dict[int, Metadata] = {}
        self.metadata_at: dict[int, float] = {}
        self.events: deque[dict] = deque(maxlen=100)

    def disconnected(self) -> None:
        self.live_at.clear()
        self.occupancy_confirmed = False

    def update_block(self, block: Block) -> bool:
        if block.extension not in (1, 2):
            return False
        ext = block.extension
        old = self.blocks.get(ext)
        now = time.time()
        self.blocks[ext] = block
        self.live_at[ext] = time.monotonic()
        self.received_at[ext] = now
        for address in range(block.base, block.base + 8 * len(block.data)):
            if old is None or old.value(address) != block.value(address):
                self.changed_at[f"{ext}:{address}"] = now
        return old != block

    def fresh(self, extension: int, max_age: float = 15) -> bool:
        last = self.live_at.get(extension)
        return last is not None and time.monotonic() - last <= max_age

    def value(self, extension: int, address: int) -> bool | None:
        block = self.blocks.get(extension)
        return block.value(address) if block else None

    @property
    def areas(self) -> list[int]:
        return sorted({(a - 0x530)//8 + 1 for a in self.occupied[0x72] if 0x530 <= a <= 0x56F})

    def area_mode(self, area: int) -> str | None:
        base = 0x530 + 8 * (area - 1)
        if self.value(2, base + 3):
            return "triggered"
        flags = [self.value(2, base + i) for i in range(3)]
        if any(flag is None for flag in flags) or flags.count(True) != 1:
            return None
        return ("disarmed", "armed_home", "armed_away")[flags.index(True)]

    def metadata_for(self, address: int) -> Metadata | None:
        return self.metadata.get(metadata_address(address))

    def record_events(self, frame: Frame) -> list[dict]:
        """Keep events separate from raw input state, including alarm restores."""
        result = []
        current = None
        for record in frame.records:
            if record.kind == 2:
                if current:
                    result.append(current)
                body = record.body
                current = {
                    "address": int.from_bytes(body[1:3], "big"), "device_area": body[0],
                    "extension": body[3], "code": body[4], "active": not bool(body[4] & 128),
                    "received_at": time.time(), "panel_time": None, "text": None,
                }
            elif current and record.kind == 0x50:
                current["panel_time"] = decode_time(record.body)
            elif current and record.kind == 0x54:
                current["text"] = decode_text(record.body)
        if current:
            result.append(current)
        self.events.extend(result)
        return result

    def export(self) -> dict:
        return {
            "schema": 1, "identity": self.identity,
            "blocks": {str(ext): {"base": b.base, "hex": b.data.hex(),
                        "received_at": self.received_at.get(ext)} for ext, b in self.blocks.items()},
            "occupied": {str(ext): sorted(values) for ext, values in self.occupied.items()},
            "metadata": {str(a): {**asdict(m), "text": m.text.hex(),
                         "address_hex": f"0x{a:04X}", "name": m.name,
                         "updated_at": self.metadata_at.get(a, 0)} for a, m in self.metadata.items()},
            "changed_at": dict(self.changed_at), "events": list(self.events),
            "inventory": self.inventory(),
        }

    def inventory(self) -> dict[str, list[dict]]:
        """Readable snapshot with independent point, message-area and area names.

        Name fields are derived from preserved wire text. They are not trusted
        when restoring a cache, and do not imply live availability.
        """
        groups: dict[str, list[dict]] = {
            "input_points": [], "message_areas": [], "security_areas": [], "outputs": [],
        }

        def item(address: int, *, number: int | None = None) -> dict:
            info = self.metadata_for(address)
            configured_name = info.name if info else None
            row = {
                "address_hex": f"0x{address:04X}", "address": address,
                "name": configured_name or describe(address).label,
                "configured_name": configured_name,
                "name_source": "panel" if configured_name else "fallback" if info else "pending",
            }
            if number is not None:
                row["number"] = number
            if info and address < 0x500:
                row["security_areas"] = info.areas
                row["message_area"] = info.reporting_area or None
            return row

        groups["input_points"] = [item(a) for a in sorted(self.occupied[0x71])]
        groups["security_areas"] = [item(0x530 + (area - 1)*8, number=area) for area in self.areas]
        for address in sorted(self.occupied[0x72]):
            if 0x570 <= address <= 0x5EF:
                row = item(address, number=address - 0x570 + 1)
                row["bypass_address_hex"] = f"0x{address + 0x80:04X}"
                row["bypass_configured"] = address + 0x80 in self.occupied[0x72]
                groups["message_areas"].append(row)
            elif describe(address).kind == "output":
                groups["outputs"].append(item(address))
        return groups

    def restore(self, data: dict) -> bool:
        """Load only a matching, versioned snapshot, atomically after validation."""
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("identity") != self.identity:
            return False
        try:
            restored = PanelState(self.identity)
            for ext, item in data.get("blocks", {}).items():
                extension = int(ext)
                if extension not in (1, 2):
                    raise ValueError("Invalid extension")
                raw = bytes.fromhex(item["hex"])
                base = int(item["base"])
                if not 0 <= base <= 65535 or len(raw) > 249 or base + len(raw)*8 > 65536:
                    raise ValueError("Invalid block")
                restored.blocks[extension] = Block(base, extension, raw)
                restored.received_at[extension] = float(item["received_at"])
            for ext in (0x71, 0x72):
                values = {int(a) for a in data.get("occupied", {}).get(str(ext), [])}
                if any(not 0 <= a <= 65535 for a in values):
                    raise ValueError("Invalid address")
                restored.occupied[ext] = values
            for address, item in data.get("metadata", {}).items():
                a = int(address)
                if a != item["address"] or not 0 <= a <= 65535:
                    raise ValueError("Invalid metadata address")
                raw = bytes.fromhex(item["text"])
                mask, zone = int(item["areas_mask"]), int(item["reporting_area"])
                if not 0 <= mask <= 255 or not 0 <= zone <= 128 or len(raw) > 255:
                    raise ValueError("Invalid metadata")
                restored.metadata[a] = Metadata(a, int(item["device_area"]), mask, zone, raw)
                restored.metadata_at[a] = float(item["updated_at"])
            restored.changed_at = {str(k): float(v) for k, v in data.get("changed_at", {}).items()}
            restored.events.extend(e for e in data.get("events", [])[-100:] if isinstance(e, dict))
        except (KeyError, ValueError, TypeError, OverflowError):
            return False
        self.blocks = restored.blocks
        self.received_at = restored.received_at
        self.occupied = restored.occupied
        self.metadata = restored.metadata
        self.metadata_at = restored.metadata_at
        self.changed_at = restored.changed_at
        self.events = restored.events
        self.disconnected()
        return True
