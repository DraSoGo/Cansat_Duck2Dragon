import csv
import importlib.util
import pathlib
import queue
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


class LogWriterTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def packet(self):
        line = (
            "128518,8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, "port1", -61, 11.75, 1000.0)

    def test_log_writer_creates_four_session_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="2026-06-05_12-00-00")
            writer.close()

            names = sorted(path.name for path in pathlib.Path(tmp).iterdir())

        self.assertEqual(
            names,
            [
                "2026-06-05_12-00-00_events.csv",
                "2026-06-05_12-00-00_merged.csv",
                "2026-06-05_12-00-00_port1.csv",
                "2026-06-05_12-00-00_port2.csv",
            ],
        )

    def test_raw_log_preserves_malformed_and_comment_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="session")
            writer.write_raw("port1", "# RSSI=-61 SNR=11.75")
            writer.write_raw("port1", "malformed,line")
            writer.close()

            text = (pathlib.Path(tmp) / "session_port1.csv").read_text()

        self.assertIn("# RSSI=-61 SNR=11.75", text)
        self.assertIn("malformed,line", text)

    def test_merged_log_includes_source_and_link_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="session")
            writer.write_merged(self.packet())
            writer.close()

            text = (pathlib.Path(tmp) / "session_merged.csv").read_text()

        self.assertIn("source,rssi,snr", text)
        self.assertIn("port1,-61,11.75", text)

    def test_events_log_includes_session_start_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="session")
            writer.close()

            lines = (pathlib.Path(tmp) / "session_events.csv").read_text().splitlines()

        self.assertTrue(lines[0].startswith("# session start "))
        self.assertEqual(lines[1], "timestamp,event,note")

    def test_event_log_writes_valid_csv_for_special_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="session")
            writer.write_event('drop,\n"line"\rbreak', 'note,\n"line"\rbreak')
            writer.close()

            with (pathlib.Path(tmp) / "session_events.csv").open(newline="") as file_obj:
                rows = list(csv.reader(file_obj))

        self.assertEqual(rows[1], ["timestamp", "event", "note"])
        self.assertEqual(len(rows[2]), 3)
        self.assertNotEqual(rows[2][0], "")
        self.assertEqual(rows[2][1], 'drop, "line" break')
        self.assertEqual(rows[2][2], 'note, "line" break')


class ReplayReaderTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def test_replay_reader_emits_line_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.csv"
            path.write_text("# RSSI=-61 SNR=11.75\n1,2,3\n")
            events = queue.Queue()

            reader = self.monitor.ReplayReader(path, "port1", events, speed=99.0)
            reader.run_once_for_test()

            first = events.get_nowait()
            second = events.get_nowait()

        self.assertEqual(first["type"], "line")
        self.assertEqual(first["source"], "port1")
        self.assertEqual(first["line"], "# RSSI=-61 SNR=11.75")
        self.assertEqual(second["line"], "1,2,3")

    def test_replay_reader_skip_empty_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.csv"
            path.write_text("\n\n# boot\n")
            events = queue.Queue()

            reader = self.monitor.ReplayReader(path, "port2", events, speed=99.0)
            reader.run_once_for_test()

            event = events.get_nowait()

        self.assertEqual(event["line"], "# boot")
        self.assertTrue(events.empty())
