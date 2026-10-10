"""Offer only a loopback ICE candidate (SPEC §4).

aioice skips 127.0.0.1 and advertises LAN and IPv6 addresses; the macOS firewall can drop
inbound UDP to Python on those, and ICE then hangs at "checking". The browser is always on
this machine, so loopback is enough and keeps media off the network.
"""

from __future__ import annotations

import aioice.ice


def _loopback_only(use_ipv4: bool, use_ipv6: bool) -> list[str]:
    return ["127.0.0.1"]


def apply() -> None:
    aioice.ice.get_host_addresses = _loopback_only


apply()
