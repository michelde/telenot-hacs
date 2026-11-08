"""Redacted diagnostics: no host, identity, point names, texts or raw events."""


async def async_get_config_entry_diagnostics(hass, entry):
    client = entry.runtime_data.client
    return {
        "integration_version": "0.1.0", "connected": client.connected,
        "identity_verified": client.verified_identity, "allow_control": client.allow_control,
        "discovery_status": client.discovery_status,
        "discovery_done": client.discovery_done, "discovery_total": client.discovery_total,
        "discovery_failed_addresses": [f"0x{a:04X}" for a in client.discovery_failures],
        "block_lengths": {str(ext): len(block.data) for ext, block in client.state.blocks.items()},
        "fresh_blocks": {str(ext): client.state.fresh(ext) for ext in (1, 2)},
        "occupied_counts": {str(ext): len(values) for ext, values in client.state.occupied.items()},
        "counters": dict(client.counters),
    }
