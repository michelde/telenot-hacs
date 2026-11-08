"""Run a full read-only discovery using the actual integration core.

python -m custom_components.telenot.protocol --host GATEWAY_IP
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from .client import ConnectionLost, GMSClient


async def scan(args) -> int:
    done = asyncio.Event()
    previous = None

    def update(reason):
        nonlocal previous
        progress = (client.discovery_status, client.discovery_done, client.discovery_total)
        if progress != previous:
            print(f"Discovery: {progress[0]} {progress[1]}/{progress[2]}", flush=True)
            previous = progress
        if client.discovery_status in ("complete", "partial", "failed"):
            done.set()

    client = GMSClient(args.host, args.port, allow_control=False, on_update=update)
    success = False
    try:
        await client.start()
        print(f"Panel ID: {client.state.identity}", flush=True)
        await asyncio.wait_for(done.wait(), args.timeout)
        success = client.discovery_status == "complete"
    except (ConnectionLost, TimeoutError) as error:
        print(f"Discovery incomplete: {error}", flush=True)
    finally:
        await client.stop()
        args.output.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = args.output / f"core-discovery-{stamp}.json"
        with target.open("x", encoding="utf-8") as output:
            json.dump({"state": client.state.export(), "status": client.discovery_status,
                       "failures": client.discovery_failures, "counters": dict(client.counters)},
                      output, ensure_ascii=False, indent=2)
        print(f"Connection closed. Local report: {target}", flush=True)
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8234)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", type=Path, default=Path("diagnostics"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.timeout <= 0:
        parser.error("Invalid port or timeout")
    try:
        return asyncio.run(scan(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
