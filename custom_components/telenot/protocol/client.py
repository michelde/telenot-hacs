"""One TCP reader, one transaction worker, and reconnecting read-only discovery."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from .catalog import metadata_address
from .codec import (
    ACK,
    ERRORS,
    NORM,
    Block,
    Frame,
    FrameParser,
    Metadata,
    ProtocolError,
    arm_command,
    decode_identity,
    metadata_from_frame,
    metadata_query,
    occupancy_query,
)
from .model import PanelState

_LOGGER = logging.getLogger(__name__)


class ConnectionLost(Exception):
    """The serial gateway session is unavailable."""


class IdentityMismatch(ConnectionLost):
    """A different panel was connected; do not adopt it automatically."""


class Rejected(Exception):
    def __init__(self, code: int | None):
        self.code = code
        super().__init__(f"Panel rejected request: {ERRORS.get(code, 'NAK/unknown error')} ({code})")


class UncertainResult(Exception):
    """An operation was sent but its final outcome cannot be confirmed."""


class PanelPriority(Exception):
    """Panel won a simultaneous SEND_NORM, before our data was sent."""


@dataclass
class Request:
    kind: str
    payload: bytes
    future: asyncio.Future
    address: int | None = None
    area: int | None = None
    mode: str | None = None


@dataclass
class Pending:
    request: Request
    norm_ack: asyncio.Future
    data_ack: asyncio.Future
    response: asyncio.Future
    sent: bool = False
    occupied: dict[int, set[int]] = field(default_factory=dict)


class GMSClient:
    def __init__(
        self, host: str, port: int = 8234, *, identity: str | None = None,
        allow_control: bool = False, discover: bool = True,
        on_update: Callable[[str], None] | None = None,
        ack_timeout: float = 3, response_timeout: float = 12,
        queue_timeout: float = 60, idle_timeout: float = 15,
        reconnect_delay: float = 2,
    ) -> None:
        self.host, self.port = host, port
        self.state = PanelState(identity)
        self.expected_identity = identity
        self.allow_control = allow_control
        self.discover = discover
        self.on_update = on_update
        self.ack_timeout, self.response_timeout = ack_timeout, response_timeout
        self.queue_timeout, self.idle_timeout = queue_timeout, idle_timeout
        self.reconnect_delay = reconnect_delay
        self.connected = False
        self.verified_identity = False
        self.discovery_status = "pending"
        self.discovery_done = 0
        self.discovery_total = 0
        self.discovery_failures: dict[int, str] = {}
        self.last_error: str | None = None
        self.last_command: dict | None = None
        self.latest_events: list[dict] = []
        self.counters: Counter = Counter()
        self._supervisor: asyncio.Task | None = None
        self._discovery_task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=256)
        self._sequence = 0
        self._pending: Pending | None = None
        self._ready = asyncio.Event()
        self._window = asyncio.Event()
        self._discovery_lock = asyncio.Lock()
        self._input_seen = False
        self._parser = FrameParser()

    def notify(self, reason: str) -> None:
        if self.on_update:
            try:
                self.on_update(reason)
            except Exception:
                _LOGGER.exception("GMS update listener failed")

    async def start(self, timeout: float = 20) -> None:
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._run(), name="telenot-connection")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError as error:
            await self.stop()
            raise ConnectionLost(self.last_error or "No complete GMS status received") from error

    async def stop(self) -> None:
        if self._supervisor:
            self._supervisor.cancel()
            with suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None

    async def _run(self) -> None:
        delay = self.reconnect_delay
        while True:
            tasks: list[asyncio.Task] = []
            fatal = False
            try:
                reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), 8
                )
                self.connected = True
                self.verified_identity = False
                self._input_seen = False
                self._ready.clear()
                self._window.clear()
                self.state.disconnected()
                self._parser = FrameParser()
                self.notify("connection")
                tasks = [asyncio.create_task(self._receive(reader), name="telenot-reader"),
                         asyncio.create_task(self._work(), name="telenot-worker")]
                if self.discover:
                    self._discovery_task = asyncio.create_task(self.rediscover(), name="telenot-discovery")
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                raise ConnectionLost("GMS session ended")
            except asyncio.CancelledError:
                raise
            except IdentityMismatch as error:
                self.last_error = str(error)
                fatal = True
            except (OSError, TimeoutError, ConnectionLost, ProtocolError) as error:
                self.last_error = str(error) or type(error).__name__
            finally:
                was_ready = self._ready.is_set()
                self.connected = False
                self.verified_identity = False
                self._ready.clear()
                self._window.clear()
                self.state.disconnected()
                if self._discovery_task:
                    tasks.append(self._discovery_task)
                    self._discovery_task = None
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._fail_queued()
                if self._writer:
                    self._writer.close()
                    with suppress(OSError, TimeoutError):
                        await asyncio.wait_for(self._writer.wait_closed(), 2)
                    self._writer = None
                self.notify("connection")
            if fatal:
                self.discovery_status = "failed"
                self.notify("discovery")
                return
            self.counters["reconnects"] += 1
            delay = self.reconnect_delay if was_ready else min(30, max(delay * 2, self.reconnect_delay))
            await asyncio.sleep(delay)

    def _fail_queued(self) -> None:
        while not self._queue.empty():
            _, _, request = self._queue.get_nowait()
            if not request.future.done():
                request.future.set_exception(ConnectionLost("Disconnected before sending"))
            self._queue.task_done()

    async def _send(self, raw: bytes) -> None:
        if not self.connected or self._writer is None:
            raise ConnectionLost("Gateway disconnected")
        self._writer.write(raw)
        await self._writer.drain()
        self.counters["tx_frames"] += 1

    async def _receive(self, reader: asyncio.StreamReader) -> None:
        last_bytes = time.monotonic()
        partial_since = None
        while True:
            try:
                data = await asyncio.wait_for(reader.read(4096), min(1, self.idle_timeout))
            except TimeoutError:
                now = time.monotonic()
                if now - last_bytes >= self.idle_timeout:
                    raise ConnectionLost("Gateway silent") from None
                if partial_since is not None and now - partial_since > 3:
                    self._parser.reset()
                    partial_since = None
                    self.counters["partial_frame_timeouts"] += 1
                continue
            if not data:
                raise ConnectionLost("Gateway closed connection")
            last_bytes = time.monotonic()
            frames = self._parser.feed(data)
            self.counters["invalid_frames"] = self._parser.invalid_frames
            self.counters["discarded_bytes"] = self._parser.discarded_bytes
            window = False
            for frame in frames:
                self.counters["rx_frames"] += 1
                if frame.is_norm:
                    window = False
                    self._window.clear()
                    pending = self._pending
                    if pending and not pending.sent and not pending.norm_ack.done():
                        pending.norm_ack.set_exception(PanelPriority())
                    await self._send(ACK)
                    continue
                if frame.is_data:
                    # Validate identity before exposing any data from this frame.
                    for record in frame.records:
                        if record.kind == 0x56:
                            ident = decode_identity(record.body)
                            if self.expected_identity is not None and ident != self.expected_identity:
                                raise IdentityMismatch("Panel identity differs from configured identity")
                            self.expected_identity = ident
                            self.state.identity = ident
                            self.verified_identity = True
                    # ACK cannot depend on Home Assistant storage/UI work.
                    await self._send(ACK)
                    if not self.verified_identity:
                        continue
                    for record in frame.records:
                        if record.kind == 0x24:
                            block = Block.from_record(record)
                            if block.extension in (1, 2):
                                self.state.update_block(block)
                                if block.extension == 1:
                                    self._input_seen = True
                                elif self._input_seen:
                                    window = True
                                    self._input_seen = False
                    if self.state.fresh(1) and self.state.fresh(2):
                        self._ready.set()
                        self.last_error = None
                    events = self.state.record_events(frame)
                    self.latest_events = events
                    self._match_response(frame)
                    self.notify("event" if events else "state")
                elif frame.is_ack or frame.is_nak:
                    self._handle_ack(frame)
                else:
                    self.counters["unknown_controls"] += 1
            partial_since = (partial_since or last_bytes) if self._parser.buffer else None
            if window and not self._parser.buffer:
                self._window.set()

    def _handle_ack(self, frame: Frame) -> None:
        pending = self._pending
        if not pending:
            self.counters["unsolicited_ack"] += 1
            return
        errors = [r.body[1] for r in frame.records if r.kind == 0x11]
        future = pending.data_ack if pending.sent else pending.norm_ack
        if future.done():
            self.counters["duplicate_ack"] += 1
        elif errors or frame.is_nak:
            future.set_exception(Rejected(errors[0] if errors else None))
        else:
            future.set_result(None)

    def _match_response(self, frame: Frame) -> None:
        pending = self._pending
        if not pending or not pending.sent or pending.response.done():
            return
        request = pending.request
        if request.kind == "metadata":
            info = metadata_from_frame(frame)
            if info and info.address == request.address:
                pending.response.set_result(info)
        else:
            for record in frame.records:
                if record.kind != 0x24:
                    continue
                block = Block.from_record(record)
                if request.kind == "occupancy" and block.extension in (0x71, 0x72):
                    pending.occupied[block.extension] = block.active_addresses()
                    if len(pending.occupied) == 2:
                        pending.response.set_result(pending.occupied)
                        break
                elif request.kind == "control" and block.extension == 2 and block.value(request.address):
                    pending.response.set_result("confirmed_by_status")
                    break

    async def _enqueue(self, kind: str, payload: bytes, *, address=None, area=None, mode=None):
        if not self.connected:
            raise ConnectionLost("Gateway disconnected")
        future = asyncio.get_running_loop().create_future()
        request = Request(kind, payload, future, address, area, mode)
        self._sequence += 1
        try:
            self._queue.put_nowait((0 if kind == "control" else 10, self._sequence, request))
        except asyncio.QueueFull as error:
            raise ConnectionLost("Request queue full") from error
        # Cancelling a caller cancels its future; the worker must never send it later.
        try:
            return await asyncio.wait_for(future, self.queue_timeout)
        except TimeoutError as error:
            raise UncertainResult("Request timed out; not automatically repeated") from error

    async def _work(self) -> None:
        while True:
            # Pick the highest priority request only once a send window is available.
            await self._window.wait()
            _, _, request = await self._queue.get()
            if request.future.done():
                self._queue.task_done()
                continue
            try:
                await self._window.wait()
                if request.future.done():
                    continue
                if request.kind == "control":
                    self._check_control(request.area)
                self._window.clear()
                await self._execute(request)
            except PanelPriority:
                self.counters["collisions"] += 1
                if not request.future.done():
                    self._sequence += 1
                    self._queue.put_nowait((0 if request.kind == "control" else 10, self._sequence, request))
            except asyncio.CancelledError:
                if not request.future.done():
                    request.future.set_exception(UncertainResult("Session ended during request"))
                raise
            except Exception as error:
                if not request.future.done():
                    request.future.set_exception(error)
            finally:
                self._queue.task_done()

    async def _execute(self, request: Request) -> None:
        loop = asyncio.get_running_loop()
        pending = Pending(request, loop.create_future(), loop.create_future(), loop.create_future())
        self._pending = pending
        try:
            await self._send(NORM)
            await asyncio.wait_for(pending.norm_ack, self.ack_timeout)
            if request.future.done():
                return
            if request.kind == "control":
                self._check_control(request.area)
            pending.sent = True
            await self._send(request.payload)
            await asyncio.wait_for(pending.data_ack, self.ack_timeout)
            if request.kind == "control":
                self.last_command = {"area": request.area, "mode": request.mode, "result": "acknowledged"}
                self.notify("command")
            result = await asyncio.wait_for(pending.response, self.response_timeout)
            if not request.future.done():
                request.future.set_result(result)
        except TimeoutError as error:
            # Close this session so a late ACK cannot acknowledge a different request.
            if self._writer:
                self._writer.close()
            raise UncertainResult("No conclusive response; request will not be repeated") from error
        finally:
            self._pending = None
            for future in (pending.norm_ack, pending.data_ack, pending.response):
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()

    def _check_control(self, area: int) -> None:
        if not self.allow_control:
            raise PermissionError("Writing is disabled in integration options")
        if (not self.connected or not self.verified_identity or not self.state.occupancy_confirmed
                or not self.state.fresh(2) or area not in self.state.areas):
            raise ConnectionLost("Area unavailable or not configured")

    async def set_mode(self, area: int, mode: str) -> str:
        self._check_control(area)
        payload, address = arm_command(area, mode)
        self.last_command = {"area": area, "mode": mode, "result": "queued"}
        self.notify("command")
        _LOGGER.info("Explicit GMS area command requested: area=%d mode=%s", area, mode)
        try:
            result = await self._enqueue("control", payload, address=address, area=area, mode=mode)
        except asyncio.CancelledError:
            self.last_command = {"area": area, "mode": mode, "result": "cancelled_or_unknown"}
            self.notify("command")
            raise
        except (ConnectionLost, UncertainResult, Rejected, PermissionError) as error:
            result = "rejected" if isinstance(error, (Rejected, PermissionError)) else "unknown"
            self.last_command = {"area": area, "mode": mode, "result": result}
            self.notify("command")
            _LOGGER.warning("GMS area command result: area=%d mode=%s result=%s", area, mode, result)
            raise
        self.last_command = {"area": area, "mode": mode, "result": result}
        self.notify("command")
        _LOGGER.info("GMS area command result: area=%d mode=%s result=%s", area, mode, result)
        return result

    async def rediscover(self, force: bool = False) -> None:
        async with self._discovery_lock:
            self.discovery_status = "running"
            self.discovery_done = self.discovery_total = 0
            self.discovery_failures = {}
            self.notify("discovery")
            try:
                await asyncio.wait_for(self._ready.wait(), 20)
                occupied = await self._enqueue("occupancy", occupancy_query())
                self.state.occupied = occupied
                self.state.occupancy_confirmed = True
                addresses = sorted({metadata_address(a) for values in occupied.values() for a in values})
                # Remove obsolete metadata only after a complete new occupancy response.
                self.state.metadata = {a: m for a, m in self.state.metadata.items() if a in addresses}
                self.state.metadata_at = {a: t for a, t in self.state.metadata_at.items() if a in addresses}
                self.discovery_total = len(addresses)
                self.notify("discovery")
                for address in addresses:
                    age = time.time() - self.state.metadata_at.get(address, 0)
                    if force or address not in self.state.metadata or not 0 <= age < 86400:
                        try:
                            info: Metadata = await self._enqueue("metadata", metadata_query(address), address=address)
                            self.state.metadata[address] = info
                            self.state.metadata_at[address] = time.time()
                        except (Rejected, UncertainResult, ConnectionLost) as error:
                            self.discovery_failures[address] = str(error)
                            if not self.connected or isinstance(error, (UncertainResult, ConnectionLost)):
                                raise
                    self.discovery_done += 1
                    self.notify("discovery")
                self.discovery_status = "partial" if self.discovery_failures else "complete"
            except (TimeoutError, Rejected, UncertainResult, ConnectionLost) as error:
                self.last_error = str(error)
                self.discovery_status = "partial" if self.state.occupancy_confirmed else "failed"
            finally:
                self.notify("discovery")
