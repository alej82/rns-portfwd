"""Send deliberately malformed Channel packets to a running server."""

from __future__ import annotations

import struct
import sys
import time

import RNS


def main() -> int:
    config_dir, server_hash_text = sys.argv[1], sys.argv[2]
    RNS.Reticulum(configdir=config_dir, loglevel=RNS.LOG_CRITICAL, require_shared_instance=True)
    try:
        destination_hash = bytes.fromhex(server_hash_text)
        if not RNS.Transport.has_path(destination_hash):
            RNS.Transport.request_path(destination_hash)
        deadline = time.monotonic() + 15
        identity = None
        while time.monotonic() < deadline:
            identity = RNS.Identity.recall(destination_hash)
            if identity is not None:
                break
            time.sleep(0.1)
        if identity is None:
            print("could not resolve server", file=sys.stderr)
            return 1
        destination = RNS.Destination(
            identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "rnsportfwd"
        )
        link = RNS.Link(destination)
        deadline = time.monotonic() + 15
        while link.status != RNS.Link.ACTIVE and time.monotonic() < deadline:
            time.sleep(0.05)
        if link.status != RNS.Link.ACTIVE:
            print("link did not establish", file=sys.stderr)
            return 1

        unknown = struct.pack(">HHH", 0xACFF, 0, 0)
        bad_stream = struct.pack(">HHH", 0xAC04, 1, 3) + struct.pack(">HB", 0x4000, 0)
        bad_flags = struct.pack(">HHH", 0xAC04, 2, 3) + struct.pack(">HB", 1, 0x80)
        for raw in (unknown, bad_stream, bad_flags):
            packet = RNS.Packet(link, raw, context=RNS.Packet.CHANNEL)
            packet.send()
            time.sleep(0.1)
        time.sleep(0.5)
        link.teardown()
        return 0
    finally:
        RNS.Reticulum.exit_handler()


if __name__ == "__main__":
    sys.exit(main())
