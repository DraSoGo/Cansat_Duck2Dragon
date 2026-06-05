import importlib.util
import pathlib
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Data" / "ground_station_monitor.py"


def load_monitor_module():
    spec = importlib.util.spec_from_file_location("ground_station_monitor", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def test_parse_valid_24_field_packet(self):
        line = (
            "128518,8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )

        packet = self.monitor.TelemetryParser.parse_packet(
            line,
            source="port1",
            rssi=-61,
            snr=11.75,
            arrival_time=1000.0,
        )

        self.assertEqual(packet.millis, 128518)
        self.assertEqual(packet.source, "port1")
        self.assertAlmostEqual(packet.lat, 8.367500)
        self.assertAlmostEqual(packet.lon, 100.043922)
        self.assertEqual(packet.sats, 12)
        self.assertAlmostEqual(packet.voltage, 3.684)
        self.assertAlmostEqual(packet.current, -90.500)
        self.assertAlmostEqual(packet.watt, -0.333)
        self.assertEqual(packet.rssi, -61)
        self.assertAlmostEqual(packet.snr, 11.75)

    def test_rejects_wrong_field_count(self):
        with self.assertRaises(ValueError) as ctx:
            self.monitor.TelemetryParser.parse_packet(
                "1,2,3",
                source="port1",
                rssi=None,
                snr=None,
                arrival_time=1000.0,
            )

        self.assertIn("expected 24 fields", str(ctx.exception))

    def test_parse_rssi_snr_comment(self):
        result = self.monitor.TelemetryParser.parse_link_comment("# RSSI=-67 SNR=10.25")

        self.assertEqual(result, (-67, 10.25))

    def test_ignores_non_link_comment(self):
        result = self.monitor.TelemetryParser.parse_link_comment("# Ground Station ready")

        self.assertEqual(result, (None, None))


class MergeBufferTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def packet(self, millis, source, rssi, arrival=1000.0):
        line = (
            f"{millis},8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, source, rssi, 11.0, arrival)

    def test_keeps_first_unique_packet(self):
        buffer = self.monitor.MergeBuffer()
        packet = self.packet(100, "port1", -70)

        selected = buffer.add(packet)

        self.assertIs(selected, packet)
        self.assertEqual(buffer.selected[100].source, "port1")

    def test_duplicate_with_higher_rssi_replaces_selected(self):
        buffer = self.monitor.MergeBuffer()
        weak = self.packet(100, "port1", -80, arrival=1000.0)
        strong = self.packet(100, "port2", -55, arrival=1000.1)

        buffer.add(weak)
        selected = buffer.add(strong)

        self.assertIs(selected, strong)
        self.assertEqual(buffer.selected[100].source, "port2")

    def test_duplicate_with_lower_rssi_does_not_replace_selected(self):
        buffer = self.monitor.MergeBuffer()
        strong = self.packet(100, "port1", -55)
        weak = self.packet(100, "port2", -80)

        buffer.add(strong)
        selected = buffer.add(weak)

        self.assertIs(selected, strong)
        self.assertEqual(buffer.selected[100].source, "port1")

    def test_rssi_missing_loses_to_available_rssi(self):
        buffer = self.monitor.MergeBuffer()
        missing = self.packet(100, "port1", None)
        known = self.packet(100, "port2", -90)

        buffer.add(missing)
        selected = buffer.add(known)

        self.assertIs(selected, known)

    def test_both_rssi_missing_keeps_first(self):
        buffer = self.monitor.MergeBuffer()
        first = self.packet(100, "port1", None, arrival=1000.0)
        second = self.packet(100, "port2", None, arrival=1000.1)

        buffer.add(first)
        selected = buffer.add(second)

        self.assertIs(selected, first)
