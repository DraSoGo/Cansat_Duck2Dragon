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
