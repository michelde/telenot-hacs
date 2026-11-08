#!/usr/bin/env python3
"""Bounded read-only GMS probe. Standard library only; no actuator commands.

Run with --self-test before connecting. Raw captures may contain installation
and operator information and are stored in the git-ignored diagnostics folder.
This diagnostic is not yet the production integration's session engine.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def frame(control: int, payload: bytes = b"") -> bytes:
    body = bytes([control, 2]) + payload
    return bytes([0x68, len(body), len(body), 0x68]) + body + bytes([sum(body) & 255, 0x16])


ACK = frame(0)
NORM = frame(0x40)


def query(address: int | None) -> bytes:
    """None requests occupancy; other addresses request metadata, never control."""
    if address is None:
        return frame(0x73, bytes.fromhex("05 10 00 00 00 71 24"))
    return frame(0x73, bytes([5, 0x10, 0, address >> 8, address & 255, 0x73, 0x0C]))


def records(payload: bytes) -> list[tuple[int, bytes]]:
    result = []
    offset = 0
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise ValueError("incomplete record header")
        length, kind = payload[offset:offset + 2]
        end = offset + length + 2
        if end > len(payload):
            raise ValueError("record exceeds payload")
        result.append((kind, payload[offset + 2:end]))
        offset = end
    return result


def identity(data: bytes) -> str:
    if len(data) != 6:
        raise ValueError("identification must contain six bytes")
    digits = []
    padded = False
    for byte in data:
        for nibble in (byte & 15, byte >> 4):
            if nibble == 15:
                padded = True
            elif padded or nibble > 9:
                raise ValueError("invalid identification digit/padding")
            else:
                digits.append(str(nibble))
    if not digits:
        raise ValueError("empty identification")
    return "".join(digits)


class Stream:
    def __init__(self):
        self.buffer = bytearray()
        self.rejected_bytes = 0
        self.bad_checksums = 0

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 4:
            length = self.buffer[1]
            if (self.buffer[0] != 0x68 or self.buffer[3] != 0x68
                    or length < 2 or length != self.buffer[2]):
                del self.buffer[0]
                self.rejected_bytes += 1
                continue
            total = length + 6
            if len(self.buffer) < total:
                break
            candidate = bytes(self.buffer[:total])
            if candidate[-1] != 0x16 or (sum(candidate[4:-2]) & 255) != candidate[-2]:
                self.bad_checksums += 1
                self.rejected_bytes += 1
                del self.buffer[0]
                continue
            result.append(candidate)
            del self.buffer[:total]
        return result


def self_test():
    assert ACK.hex() == "6802026800020216"
    assert NORM.hex() == "6802026840024216"
    assert query(None).hex() == "680909687302051000000071241f16"
    assert query(7).hex() == "6809096873020510000007730c1016"
    assert query(0x530).hex() == "6809096873020510000530730c3e16"
    assert identity(bytes.fromhex("00 80 51 FF FF FF")) == "000815"
    assert identity(bytes.fromhex("21 43 F5 FF FF FF")) == "12345"
    restart = bytes.fromhex(
        "68 1A 1A 68 73 02 05 02 00 FF FF 01 53 "
        "07 50 05 14 03 0A 0B 10 39 06 56 00 80 51 FF FF FF C9 16"
    )
    parsed = records(restart[6:-2])
    assert [kind for kind, _ in parsed] == [2, 0x50, 0x56]
    assert [len(body) for _, body in parsed] == [5, 7, 6]
    for boundary in range(len(restart) + 1):
        stream = Stream()
        assert stream.feed(restart[:boundary]) + stream.feed(restart[boundary:]) == [restart]
    stream = Stream()
    joined = b"noise" + NORM + restart + ACK
    received = []
    for byte in joined:
        received.extend(stream.feed(bytes([byte])))
    assert received == [NORM, restart, ACK]
    broken = bytearray(restart)
    broken[-2] ^= 1
    assert Stream().feed(bytes(broken) + restart) == [restart]
    embedded = frame(0x73, bytes.fromhex("02 54 68 16"))
    assert Stream().feed(embedded + ACK) == [embedded, ACK]
    try:
        records(bytes.fromhex("06 56 00"))
    except ValueError:
        pass
    else:
        raise AssertionError("truncated record accepted")
    assert records(bytes.fromhex("05 24 00 05 00 02 FF 06 56 00 80 51 FF FF FF"))[0] == (
        0x24, bytes.fromhex("00 05 00 02 FF")
    )
    print("Offline reference checks passed.")


def run(args) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = args.output / stamp
    folder.mkdir(parents=True, exist_ok=False)
    summary = {
        "host": args.host, "port": args.port, "started_utc": stamp,
        "identities": [], "status": {}, "occupancy": {}, "metadata": {},
        "queries": [], "issues": [], "duration_limit_seconds": args.seconds,
    }
    stream = Stream()
    counts = Counter()
    pending = [None] + args.address
    active = None
    phase = "idle"
    deadline = 0.0
    seen_inputs = False
    quiet_since = time.monotonic()
    start = quiet_since
    end = start + args.seconds
    completed_at = None
    sock = None

    with (folder / "traffic.jsonl").open("x", encoding="utf-8") as capture:
        def log(direction, raw=b"", **extra):
            capture.write(json.dumps({
                "utc": datetime.now(timezone.utc).isoformat(),
                "elapsed": round(time.monotonic() - start, 6),
                "direction": direction, "hex": raw.hex(" "), **extra,
            }, ensure_ascii=False) + "\n")
            capture.flush()

        def send(raw):
            # This is the complete TX allowlist. No arbitrary data/0x02/0x50 allowed.
            allowed_query = (
                len(raw) == 15 and raw[4:9] == bytes.fromhex("73 02 05 10 00")
                and (raw[11:13] == bytes.fromhex("73 0C")
                     or raw[9:13] == bytes.fromhex("00 00 71 24"))
            )
            if raw not in (ACK, NORM) and not allowed_query:
                raise ValueError("read-only TX allowlist rejected telegram")
            sock.sendall(raw)
            counts["tx"] += 1
            log("tx", raw)

        def finish(outcome):
            nonlocal active, phase, completed_at
            if active is not None:
                active["outcome"] = outcome
                active["finished_seconds"] = round(time.monotonic() - start, 3)
                summary["queries"].append(active)
                print(f"Query {active['target']}: {outcome}", flush=True)
            active = None
            phase = "idle"
            if not pending:
                completed_at = time.monotonic()

        try:
            print(f"Connecting to {args.host}:{args.port}; capture: {folder}", flush=True)
            sock = socket.create_connection((args.host, args.port), timeout=8)
            sock.settimeout(0.15)
            log("connected")
            while time.monotonic() < end:
                now = time.monotonic()
                if completed_at is not None and now - completed_at >= 4:
                    break
                if phase != "idle" and now >= deadline:
                    finish(f"timeout_{phase}")
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    if stream.buffer and now - quiet_since > 3:
                        log("incomplete_frame_timeout", bytes(stream.buffer))
                        summary["issues"].append("incomplete frame discarded after 3s without bytes")
                        stream.rejected_bytes += len(stream.buffer)
                        stream.buffer.clear()
                    continue
                if not chunk:
                    raise ConnectionError("gateway closed connection")
                quiet_since = time.monotonic()
                log("rx_chunk", chunk)
                batch = stream.feed(chunk)
                send_window = False
                for raw in batch:
                    counts["rx"] += 1
                    control = raw[4]
                    log("rx_frame", raw)
                    if control == 0x40:
                        send_window = False
                        if phase == "norm_ack":
                            # The panel wins arbitration. Defer this request to a later pair.
                            log("collision")
                            pending.insert(0, active["address"])
                            finish("deferred_panel_priority")
                        send(ACK)
                        continue
                    try:
                        items = records(raw[6:-2])
                    except ValueError as error:
                        summary["issues"].append(str(error))
                        log("invalid_records", raw, error=str(error))
                        continue
                    errors = [body[1] for kind, body in items if kind == 0x11 and len(body) == 2]
                    for kind, body in items:
                        counts[f"record_{kind:02X}"] += 1
                        if kind == 0x56:
                            try:
                                ident = identity(body)
                                if ident not in summary["identities"]:
                                    summary["identities"].append(ident)
                                    print(f"Panel identification: {ident}", flush=True)
                            except ValueError as error:
                                summary["issues"].append(str(error))
                        elif kind == 0x24 and len(body) >= 4:
                            base = int.from_bytes(body[1:3], "big")
                            extension = body[3]
                            zeros = [base + i * 8 + b for i, byte in enumerate(body[4:])
                                     for b in range(8) if not (byte & (1 << b))]
                            entry = {
                                "base": f"0x{base:04X}", "byte_count": len(body) - 4,
                                "raw": body[4:].hex(" "),
                                "zero_bit_addresses": [f"0x{x:04X}" for x in zeros],
                                "received_seconds": round(time.monotonic() - start, 3),
                            }
                            if extension in (1, 2):
                                key = f"0x{extension:02X}"
                                first = key not in summary["status"]
                                summary["status"][key] = entry
                                if first:
                                    print(f"Status {key}: {len(body)-4} bytes, base 0x{base:04X}", flush=True)
                                if extension == 1:
                                    seen_inputs = True
                                elif seen_inputs:
                                    send_window = True
                                    seen_inputs = False
                            elif extension in (0x71, 0x72):
                                key = f"0x{extension:02X}"
                                summary["occupancy"][key] = entry
                                print(f"Occupancy {key}: {len(body)-4} bytes, {len(zeros)} used bits", flush=True)
                        elif kind == 0x0C and len(body) == 6:
                            address = int.from_bytes(body[1:3], "big")
                            texts = [text for typ, text in items if typ == 0x54]
                            text = texts[0] if len(texts) == 1 else b""
                            try:
                                decoded = text.decode("ascii").rstrip(" \x00")
                            except UnicodeDecodeError:
                                decoded = None  # Preserve unknown encoding, do not guess.
                            summary["metadata"][f"0x{address:04X}"] = {
                                "address": address, "device_area_raw": body[0],
                                "extension": body[3], "areas_mask": body[4],
                                "areas": [b + 1 for b in range(8) if not body[4] & (1 << b)],
                                "reporting_area": body[5], "text_hex": text.hex(" "),
                                "text_length": len(text), "ascii_text": decoded,
                            }
                    if control == 0x73:
                        send(ACK)
                    elif control == 0:
                        if errors:
                            log("error_ack", raw, errors=errors)
                            if active:
                                active["errors"] = errors
                                finish("rejected")
                        elif phase == "norm_ack":
                            send(query(active["address"]))
                            phase = "query_ack"
                            deadline = time.monotonic() + 3
                        elif phase == "query_ack":
                            phase = "response"
                            deadline = time.monotonic() + 10
                    elif control == 1 and active:
                        finish("nak")
                    if active and phase in ("query_ack", "response"):
                        address = active["address"]
                        if address is None:
                            ready = all(key in summary["occupancy"] for key in ("0x71", "0x72"))
                        else:
                            key = f"0x{address:04X}"
                            ready = key in summary["metadata"] and summary["metadata"][key]["extension"] == 0x73
                        if ready:
                            finish("response_received")
                # Only after a full status pair, with no partial following telegram.
                if send_window and phase == "idle" and pending and not stream.buffer:
                    address = pending.pop(0)
                    active = {"address": address, "target": "occupancy" if address is None else f"0x{address:04X}",
                              "started_seconds": round(time.monotonic() - start, 3)}
                    send(NORM)
                    phase = "norm_ack"
                    deadline = time.monotonic() + 3
        except (OSError, ValueError, KeyboardInterrupt) as error:
            summary["issues"].append(f"{type(error).__name__}: {error}")
            print(f"Probe stopped: {type(error).__name__}: {error}", flush=True)
        finally:
            if sock:
                sock.close()
            if active:
                finish("probe_ended")
            summary["elapsed_seconds"] = round(time.monotonic() - start, 3)
            summary["counts"] = dict(counts)
            summary["rejected_bytes"] = stream.rejected_bytes
            summary["bad_checksums"] = stream.bad_checksums
            summary["unrequested_addresses"] = pending
            summary["connection_closed"] = True
            (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"Connection closed. Summary: {folder / 'summary.json'}", flush=True)
    return 0 if (summary["identities"] and len(summary["occupancy"]) == 2
                 and not summary["issues"] and not pending
                 and all(q["outcome"] in ("response_received", "deferred_panel_priority")
                         for q in summary["queries"])) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=8234)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--address", action="append", type=lambda value: int(value, 0), default=[])
    parser.add_argument("--output", type=Path, default=Path("diagnostics"))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.host:
        parser.error("--host is required for a live probe")
    if args.seconds < 1 or any(not 0 <= address <= 0xFFFF for address in args.address):
        parser.error("invalid duration or address")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
