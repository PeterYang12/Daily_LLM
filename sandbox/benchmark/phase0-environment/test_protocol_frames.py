"""Offline fragmented/coalesced Connect framing contract for frozen runner."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest

path = Path(__file__).resolve().parents[1] / "phase1-service/code/runner.py"
spec = importlib.util.spec_from_file_location("phase1_frames", path)
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


def envelope(flag, data):
    body = json.dumps(data).encode()
    return struct.pack(">BI", flag, len(body)) + body


class FramingContract(unittest.TestCase):
    def test_one_byte_chunks_and_coalesced_frames_are_equivalent(self):
        wire = envelope(0, {"event": {"end": {"exitCode": 0}}}) + envelope(2, {})
        chunked = runner.Frames(); a = []
        for byte in wire:
            a.extend(chunked.feed(bytes([byte])))
        chunked.finish()
        whole = runner.Frames(); b = whole.feed(wire); whole.finish()
        self.assertEqual(a, b)
        self.assertEqual([x[0] for x in b], [0, 2])

    def test_missing_terminal_and_partial_payload_are_rejected(self):
        for wire in (envelope(0, {"event": {"end": {}}}), envelope(2, {})[:-1]):
            with self.subTest(wire_length=len(wire)):
                decoder = runner.Frames(); decoder.feed(wire)
                with self.assertRaises(runner.InvalidResult): decoder.finish()

    def test_unknown_compressed_and_post_terminal_frames_are_rejected(self):
        for wire in (envelope(1, {}), envelope(4, {}), envelope(2, {}) + envelope(0, {}), envelope(2, {}) + envelope(2, {})):
            with self.subTest(wire_length=len(wire)):
                with self.assertRaises(runner.InvalidResult): runner.Frames().feed(wire)

    def test_oversized_declared_frame_is_rejected_before_body_allocation(self):
        with self.assertRaises(runner.InvalidResult):
            runner.Frames().feed(struct.pack(">BI", 0, 32 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
