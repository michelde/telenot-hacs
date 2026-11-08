"""Minimal independent gateway simulator, bound exclusively to loopback."""

import asyncio
from contextlib import suppress


def wire(control, payload=b""):
    body = bytes([control, 2]) + payload
    return bytes([104, len(body), len(body), 104]) + body + bytes([sum(body) % 256, 22])


def record(kind, data):
    return bytes([len(data), kind]) + data


ID = bytes.fromhex("06 56 00 80 51 FF FF FF")
ACK = bytes.fromhex("68 02 02 68 00 02 02 16")
NORM = bytes.fromhex("68 02 02 68 40 02 42 16")


class Gateway:
    def __init__(self):
        self.server = None
        self.tasks = set()
        self.writers = set()
        self.input_data = bytearray([255] * 131)
        self.output_data = bytearray([255] * 80)
        self.output_data[6] = 0x9E
        self.occupied_inputs = {0, 3}
        self.occupied_outputs = {0x515, *range(0x530, 0x538), 0x570, 0x5F0}
        self.queries = []
        self.commands = []
        self.connections = 0
        self.collide_once = False
        self.drop_command_ack = False
        self.reject_command = False
        self.inject_unrelated = False
        self.reject_metadata = set()
        self.names = {0: b"Test point", 3: b"T\xf5r Test", 0x530: b"Security area",
                      0x570: b"Message area", 0x515: b"Output test"}
        self.ident = ID
        self.enable_polls = asyncio.Event()
        self.enable_polls.set()

    async def start(self):
        self.server = await asyncio.start_server(self.accept, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    def accept(self, reader, writer):
        task = asyncio.create_task(self.session(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def disconnect_clients(self):
        for writer in list(self.writers):
            writer.close()
            await writer.wait_closed()

    async def receive(self, reader):
        header = await reader.readexactly(4)
        assert header[0] == header[3] == 104
        assert header[1] == header[2] and header[1] >= 2
        rest = await reader.readexactly(header[1] + 2)
        assert rest[-1] == 22
        assert sum(rest[:-2]) % 256 == rest[-2]
        return header + rest

    async def transmit(self, reader, writer, payload):
        writer.write(NORM)
        await writer.drain()
        assert await self.receive(reader) == ACK
        data = wire(0x73, payload + self.ident)
        # Force TCP fragmentation independently of protocol boundaries.
        writer.write(data[:3])
        await writer.drain()
        await asyncio.sleep(0)
        writer.write(data[3:19])
        await writer.drain()
        await asyncio.sleep(0)
        writer.write(data[19:])
        await writer.drain()
        assert await self.receive(reader) == ACK

    def block(self, extension, occupied=False):
        is_input = extension in (1, 0x71)
        base = 0 if is_input else 0x500
        if occupied:
            data = bytearray([255] * (131 if is_input else 80))
            for address in self.occupied_inputs if is_input else self.occupied_outputs:
                index = address - base
                data[index // 8] &= ~(1 << (index % 8))
        else:
            data = self.input_data if is_input else self.output_data
        return record(0x24, bytes([0, base >> 8, base & 255, extension]) + data)

    def metadata(self, address):
        text = self.names.get(address, b"Test point")
        text = text.ljust(32 if address < 0x500 else 16, b" ")
        body = bytes([1, address >> 8, address & 255, 0x73, 0xFE, 1])
        return record(0x0C, body) + record(0x54, text)

    async def session(self, reader, writer):
        self.writers.add(writer)
        self.connections += 1
        try:
            while True:
                await self.enable_polls.wait()
                await self.transmit(reader, writer, self.block(1))
                await self.transmit(reader, writer, self.block(2))
                try:
                    incoming = await asyncio.wait_for(self.receive(reader), 0.04)
                except TimeoutError:
                    continue
                assert incoming == NORM
                if self.collide_once:
                    self.collide_once = False
                    await self.transmit(reader, writer, record(2, bytes.fromhex("00 FF FF 01 53")))
                    continue
                writer.write(ACK)
                await writer.drain()
                request = await self.receive(reader)
                assert request[4] == 0x73
                payload = request[6:-2]
                assert payload[0] == 5 and len(payload) == 7
                kind = payload[1]
                address = int.from_bytes(payload[3:5], "big")
                if kind == 2:
                    self.commands.append(payload)
                    if self.drop_command_ack:
                        await asyncio.sleep(0.5)
                        continue
                    if self.reject_command:
                        writer.write(wire(0, record(0x11, bytes([0, 0x18]))))
                    else:
                        writer.write(ACK)
                        area_index = (address - 0x530)//8
                        offset = (address - 0x530) % 8
                        self.output_data[6 + area_index] = 0xFF & ~(1 << offset)
                    await writer.drain()
                    continue
                assert kind == 0x10, "Unexpected actuator command in discovery"
                self.queries.append(payload)
                if payload[-1] == 0x0C and address in self.reject_metadata:
                    writer.write(wire(0, record(0x11, bytes([0, 0x19]))))
                    await writer.drain()
                    continue
                writer.write(ACK)
                await writer.drain()
                if payload[-1] == 0x24:
                    assert payload == bytes.fromhex("05 10 00 00 00 71 24")
                    await self.transmit(reader, writer, self.block(0x71, True))
                    # Interleaved normal status must not satisfy output occupancy.
                    await self.transmit(reader, writer, self.block(2))
                    await self.transmit(reader, writer, self.block(0x72, True))
                else:
                    assert payload[5:] == bytes.fromhex("73 0C")
                    if self.inject_unrelated:
                        await self.transmit(reader, writer, self.metadata(0x5555))
                        await self.transmit(reader, writer, record(2, bytes.fromhex("01 00 00 01 22"))
                                            + record(0x54, b"Operator text"))
                    await self.transmit(reader, writer, self.metadata(address))
        except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()


async def until(predicate, timeout=4):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)
