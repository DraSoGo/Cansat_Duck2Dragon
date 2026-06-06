import csv
import importlib.util
import math
import pathlib
import queue
import tempfile
import threading
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

    def test_parse_legacy_23_field_packet_computes_watt(self):
        line = (
            "692167,8.384282,100.062355,2427.30,5,22.25,48.67,1010.58,"
            "0.0000,0.0000,0.0000,0.0000,0.0000,0.0000,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.968,-116.500"
        )

        packet = self.monitor.TelemetryParser.parse_packet(
            line,
            source="port1",
            rssi=-92,
            snr=11.75,
            arrival_time=1000.0,
        )

        self.assertEqual(packet.millis, 692167)
        self.assertAlmostEqual(packet.voltage, 3.968)
        self.assertAlmostEqual(packet.current, -116.500)
        self.assertAlmostEqual(packet.watt, 3.968 * -116.500 / 1000.0)

    def test_parse_legacy_trailing_empty_watt_packet_computes_watt(self):
        line = (
            "692167,8.384282,100.062355,2427.30,5,22.25,48.67,1010.58,"
            "0.0000,0.0000,0.0000,0.0000,0.0000,0.0000,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.968,-116.500,"
        )

        packet = self.monitor.TelemetryParser.parse_packet(
            line,
            source="port1",
            rssi=-92,
            snr=11.75,
            arrival_time=1000.0,
        )

        self.assertAlmostEqual(packet.watt, 3.968 * -116.500 / 1000.0)

    def test_short_packet_pads_missing_fields_with_nan(self):
        packet = self.monitor.TelemetryParser.parse_packet(
            "1,2,3",
            source="port1",
            rssi=None,
            snr=None,
            arrival_time=1000.0,
        )

        self.assertEqual(packet.millis, 1)
        self.assertAlmostEqual(packet.lat, 2.0)
        self.assertAlmostEqual(packet.lon, 3.0)
        self.assertTrue(math.isnan(packet.alt_gps))
        self.assertTrue(math.isnan(packet.voltage))
        self.assertTrue(math.isnan(packet.current))
        self.assertTrue(math.isnan(packet.watt))

    def test_bad_numeric_fields_become_nan(self):
        line = (
            "128518,bad-lat,100.043922,-1.80,not-sats,56.02,24.98,1006.54,"
            "bad-ax,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,bad-voltage,-90.500,bad-watt"
        )

        packet = self.monitor.TelemetryParser.parse_packet(
            line,
            source="port1",
            rssi=None,
            snr=None,
            arrival_time=1000.0,
        )

        self.assertEqual(packet.millis, 128518)
        self.assertTrue(math.isnan(packet.lat))
        self.assertTrue(math.isnan(packet.sats))
        self.assertTrue(math.isnan(packet.ax))
        self.assertTrue(math.isnan(packet.voltage))
        self.assertAlmostEqual(packet.current, -90.500)
        self.assertTrue(math.isnan(packet.watt))
        self.assertFalse(packet.gps_valid)

    def test_inline_comment_packet_strips_link_metadata_before_csv_parse(self):
        line = (
            "324091,8.375336,100.080513,-557.60,5,40.96,32.81,1008.34,"
            "0.0000,0.0000,0.0000,-0.0117,0.0059,0.0000,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.436,-116.700"
            "# RSSI=-101 SNR=12.75"
        )

        packet = self.monitor.TelemetryParser.parse_packet(
            line,
            source="port1",
            rssi=-101,
            snr=12.75,
            arrival_time=1000.0,
        )

        self.assertEqual(packet.millis, 324091)
        self.assertAlmostEqual(packet.current, -116.700)
        self.assertAlmostEqual(packet.watt, 3.436 * -116.700 / 1000.0)
        self.assertEqual(packet.rssi, -101)
        self.assertAlmostEqual(packet.snr, 12.75)

    def test_corrupt_millis_uses_leading_digits_when_available(self):
        packet = self.monitor.TelemetryParser.parse_packet(
            "104671V,8.369947",
            source="port1",
            rssi=None,
            snr=None,
            arrival_time=1000.0,
        )

        self.assertEqual(packet.millis, 104671)
        self.assertAlmostEqual(packet.lat, 8.369947)
        self.assertTrue(math.isnan(packet.lon))

    def test_rejects_packet_without_usable_millis(self):
        with self.assertRaises(ValueError) as ctx:
            self.monitor.TelemetryParser.parse_packet(
                "bad,line",
                source="port1",
                rssi=None,
                snr=None,
                arrival_time=1000.0,
            )

        self.assertIn("invalid millis field", str(ctx.exception))

    def test_parse_rssi_snr_comment(self):
        result = self.monitor.TelemetryParser.parse_link_comment("# RSSI=-67 SNR=10.25")

        self.assertEqual(result, (-67, 10.25))

    def test_ignores_non_link_comment(self):
        result = self.monitor.TelemetryParser.parse_link_comment("# Ground Station ready")

        self.assertEqual(result, (None, None))


class DocumentationTests(unittest.TestCase):
    def test_readme_documents_current_24_field_schema(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("fixed 24-field schema", readme)
        self.assertIn("voltage,current,watt", readme)
        self.assertIn("| 23  | watt", readme)


class WindowIconTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def test_configure_window_icon_returns_false_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = object()
            missing_path = pathlib.Path(tmp) / "missing.png"

            configured = self.monitor.configure_window_icon(root, missing_path)

        self.assertFalse(configured)

    def test_configure_window_icon_sets_icon_and_keeps_photo_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            icon_path = pathlib.Path(tmp) / "logo.png"
            icon_path.write_bytes(b"fake")

            class FakeRoot:
                def __init__(self):
                    self.icon_calls = []

                def iconphoto(self, default, image):
                    self.icon_calls.append((default, image))

            class FakePhotoImage:
                def __init__(self, file):
                    self.file = file

            original_photo = self.monitor.tk.PhotoImage
            self.addCleanup(setattr, self.monitor.tk, "PhotoImage", original_photo)
            self.monitor.tk.PhotoImage = FakePhotoImage
            root = FakeRoot()

            configured = self.monitor.configure_window_icon(root, icon_path)

        self.assertTrue(configured)
        self.assertEqual(len(root.icon_calls), 1)
        self.assertTrue(root.icon_calls[0][0])
        self.assertIs(root.icon_calls[0][1], root._app_icon_image)
        self.assertEqual(root._app_icon_image.file, str(icon_path))


class GpsMapTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def packet(self, millis=1, lat=8.367500, lon=100.043922, sats=12, alt_gps=-1.8):
        line = (
            f"{millis},{lat:.6f},{lon:.6f},{alt_gps:.2f},{sats},56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, "port1", -61, 11.75, 1000.0)

    def test_valid_gps_packets_filters_zero_and_nan_positions(self):
        valid = self.packet()
        zero = self.packet(millis=2, lat=0.0, lon=0.0)
        missing = self.monitor.TelemetryParser.parse_packet(
            "3,,,0,8",
            source="port1",
            rssi=None,
            snr=None,
            arrival_time=1000.0,
        )

        packets = self.monitor.valid_gps_packets([valid, zero, missing])

        self.assertEqual(packets, [valid])

    def test_osm_tile_projection_and_bounds_are_consistent(self):
        x, y = self.monitor.osm_tile_xy(0.0, 0.0, 1)

        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 1.0)

        lon_left, lon_right, lat_bottom, lat_top = self.monitor.osm_tile_bounds(1, 1, 1)

        self.assertLess(lon_left, 0.1)
        self.assertGreater(lon_right, 179.0)
        self.assertLess(lat_bottom, -80.0)
        self.assertGreater(lat_top, -0.1)

    def test_osm_tile_layers_returns_cached_tile_images(self):
        original_loader = self.monitor.load_osm_tile
        self.addCleanup(setattr, self.monitor, "load_osm_tile", original_loader)
        tile = self.monitor.np.zeros((2, 2, 3), dtype=self.monitor.np.uint8)
        self.monitor.load_osm_tile = lambda z, x, y, cache_dir: tile

        layers = self.monitor.osm_tile_layers([100.043922], [8.367500])

        self.assertEqual(len(layers), 1)
        self.assertIs(layers[0][0], tile)
        self.assertEqual(len(layers[0][1]), 4)


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

    def packet(self, arrival_time=1000.0):
        line = (
            "128518,8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, "port1", -61, 11.75, arrival_time)

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
            writer.write_raw("port1", "# RSSI=-61 SNR=11.75", arrival_time=1000.0)
            writer.write_raw("port1", "malformed,line", arrival_time=1001.0)
            writer.close()

            with (pathlib.Path(tmp) / "session_port1.csv").open(newline="") as file_obj:
                rows = list(csv.reader(file_obj))

        self.assertEqual(rows[1], ["time", "raw_line"])
        self.assertEqual(rows[2][1], "# RSSI=-61 SNR=11.75")
        self.assertEqual(rows[3][1], "malformed,line")
        self.assertNotEqual(rows[2][0], "")

    def test_merged_log_includes_source_and_link_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = self.monitor.LogWriter(pathlib.Path(tmp), session_id="session")
            writer.write_merged(self.packet(arrival_time=1000.0))
            writer.close()

            with (pathlib.Path(tmp) / "session_merged.csv").open(newline="") as file_obj:
                rows = list(csv.DictReader(line for line in file_obj if not line.startswith("#")))

        self.assertEqual(rows[0]["source"], "port1")
        self.assertEqual(rows[0]["rssi"], "-61")
        self.assertEqual(rows[0]["snr"], "11.75")
        self.assertEqual(rows[0]["time"], "1970-01-01T07:16:40")

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

    def test_replay_reader_strips_saved_log_time_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "saved.csv"
            path.write_text(
                "# session start 2026-06-06T16:12:00\n"
                "time,raw_line\n"
                "2026-06-06T16:12:01,# RSSI=-61 SNR=11.75\n"
                "2026-06-06T16:12:02,\"1,2,3\"\n"
            )
            events = queue.Queue()

            reader = self.monitor.ReplayReader(path, "port1", events, speed=99.0)
            reader.run_once_for_test()

            first = events.get_nowait()
            second = events.get_nowait()

        self.assertEqual(first["line"], "# RSSI=-61 SNR=11.75")
        self.assertEqual(second["line"], "1,2,3")

    def test_replay_reader_strips_merged_log_time_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "merged.csv"
            telemetry = (
                "128518,8.367500,100.043922,-1.80,12,56.02,24.98,1006.54,"
                "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
                "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.684,-90.500,-0.333"
            )
            path.write_text(
                "# session start 2026-06-06T16:12:00\n"
                f"time,{self.monitor.CSV_HEADER},source,rssi,snr\n"
                f"2026-06-06T16:12:02,{telemetry},port1,-61,11.75\n"
            )
            events = queue.Queue()

            reader = self.monitor.ReplayReader(path, "port1", events, speed=99.0)
            reader.run_once_for_test()

            event = events.get_nowait()

        self.assertEqual(event["line"], telemetry)

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

    def test_replay_reader_stop_while_paused_emits_no_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.csv"
            path.write_text("first\nsecond\n")
            events = queue.Queue()

            reader = self.monitor.ReplayReader(path, "port1", events)
            reader.pause()
            thread = reader.start()
            status = events.get(timeout=1.0)
            reader.stop()
            thread.join(timeout=1.0)

            remaining = [status]
            while not events.empty():
                remaining.append(events.get_nowait())

        self.assertFalse(thread.is_alive())
        self.assertEqual(status["type"], "status")
        self.assertEqual(status["status"], "replay")
        self.assertEqual([event for event in remaining if event["type"] == "line"], [])

    def test_replay_reader_speed_controls_requested_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.csv"
            path.write_text("first\nsecond\nthird\n")

            slow_sleeps = []
            slow_events = queue.Queue()
            slow_reader = self.monitor.ReplayReader(
                path,
                "port1",
                slow_events,
                speed=1.0,
                sleep_func=slow_sleeps.append,
            )
            slow_reader.run()

            fast_sleeps = []
            fast_events = queue.Queue()
            fast_reader = self.monitor.ReplayReader(
                path,
                "port1",
                fast_events,
                speed=5.0,
                sleep_func=fast_sleeps.append,
            )
            fast_reader.run()

        self.assertEqual(len(slow_sleeps), 2)
        self.assertEqual(len(fast_sleeps), 2)
        self.assertAlmostEqual(slow_sleeps[0], 0.05)
        self.assertAlmostEqual(fast_sleeps[0], 0.01)
        self.assertLess(fast_sleeps[0], slow_sleeps[0])

    def test_replay_reader_pause_during_delay_holds_next_line_until_resume(self):
        delay_seen = threading.Event()
        pause_wait_entered = threading.Event()
        release_pause_sleep = threading.Event()

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.csv"
            path.write_text("first\nsecond\n")
            events = queue.Queue()

            def sleep_func(duration):
                if duration < 0.05:
                    reader.pause()
                    delay_seen.set()
                    return
                if reader.pause_event.is_set():
                    pause_wait_entered.set()
                    release_pause_sleep.wait(timeout=1.0)

            reader = self.monitor.ReplayReader(
                path,
                "port1",
                events,
                speed=2.0,
                sleep_func=sleep_func,
            )
            thread = reader.start()

            status = events.get(timeout=1.0)
            first = events.get(timeout=1.0)
            self.assertTrue(delay_seen.wait(timeout=1.0))
            self.assertTrue(pause_wait_entered.wait(timeout=1.0))
            self.assertTrue(events.empty())

            reader.resume()
            release_pause_sleep.set()
            second = events.get(timeout=1.0)
            thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(status["status"], "replay")
        self.assertEqual(first["line"], "first")
        self.assertEqual(second["line"], "second")


class FakeSerial:
    def __init__(self, lines, close_error=None):
        self.lines = list(lines)
        self.close_error = close_error
        self.closed = False

    def readline(self):
        if not self.lines:
            return b""
        item = self.lines.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class StopThenRaiseSerial(FakeSerial):
    def __init__(self, stop_event):
        super().__init__([])
        self.stop_event = stop_event

    def readline(self):
        self.stop_event.set()
        raise OSError("closed during stop")


class SerialReaderTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def test_serial_reader_emits_decoded_line(self):
        events = queue.Queue()
        fake = FakeSerial([b"# RSSI=-61 SNR=11.75\r\n"])
        reader = self.monitor.SerialReader(
            source="port1",
            port="/dev/fake",
            baud=115200,
            event_queue=events,
            serial_factory=lambda port, baud, timeout: fake,
            reconnect_delay=0.01,
        )

        reader.run_once_for_test()

        status = events.get_nowait()
        line = events.get_nowait()
        self.assertEqual(status["status"], "connected")
        self.assertEqual(line["line"], "# RSSI=-61 SNR=11.75")

    def test_serial_reader_reports_reconnecting_on_factory_error(self):
        events = queue.Queue()

        def factory(port, baud, timeout):
            raise OSError("missing device")

        reader = self.monitor.SerialReader(
            source="port2",
            port="/dev/missing",
            baud=115200,
            event_queue=events,
            serial_factory=factory,
            reconnect_delay=0.01,
        )

        reader.try_connect_once_for_test()

        event = events.get_nowait()
        self.assertEqual(event["type"], "status")
        self.assertEqual(event["source"], "port2")
        self.assertEqual(event["status"], "reconnecting")

    def test_serial_reader_suppresses_reconnecting_after_stop(self):
        events = queue.Queue()
        reader = self.monitor.SerialReader(
            source="port1",
            port="/dev/fake",
            baud=115200,
            event_queue=events,
            serial_factory=lambda port, baud, timeout: None,
            reconnect_delay=0.01,
        )
        fake = StopThenRaiseSerial(reader.stop_event)
        reader.serial_obj = fake

        reader._read_loop()

        self.assertTrue(fake.closed)
        self.assertTrue(events.empty())

    def test_serial_reader_close_errors_do_not_escape_stop_or_cleanup(self):
        events = queue.Queue()
        stop_fake = FakeSerial([], close_error=OSError("close failed"))
        reader = self.monitor.SerialReader(
            source="port1",
            port="/dev/fake",
            baud=115200,
            event_queue=events,
            serial_factory=lambda port, baud, timeout: stop_fake,
            reconnect_delay=0.01,
        )
        reader.serial_obj = stop_fake

        reader.stop()

        cleanup_fake = FakeSerial([b"\r\n"], close_error=OSError("close failed"))
        cleanup_reader = self.monitor.SerialReader(
            source="port1",
            port="/dev/fake",
            baud=115200,
            event_queue=events,
            serial_factory=lambda port, baud, timeout: cleanup_fake,
            reconnect_delay=0.01,
        )

        cleanup_reader.run_once_for_test()

        self.assertTrue(stop_fake.closed)
        self.assertTrue(cleanup_fake.closed)

    def test_serial_reader_suppresses_reconnecting_when_stopped_during_connect_failure(self):
        events = queue.Queue()
        reader = self.monitor.SerialReader(
            source="port1",
            port="/dev/fake",
            baud=115200,
            event_queue=events,
            serial_factory=lambda port, baud, timeout: None,
            reconnect_delay=0.01,
        )

        def factory(port, baud, timeout):
            reader.stop_event.set()
            raise OSError("missing after stop")

        reader.serial_factory = factory

        connected = reader._connect()

        self.assertFalse(connected)
        self.assertTrue(events.empty())

    def test_serial_reader_closes_without_connected_when_stopped_during_connect_success(self):
        events = queue.Queue()
        fake = FakeSerial([])
        reader = self.monitor.SerialReader(
            source="port1",
            port="/dev/fake",
            baud=115200,
            event_queue=events,
            serial_factory=lambda port, baud, timeout: None,
            reconnect_delay=0.01,
        )

        def factory(port, baud, timeout):
            reader.stop_event.set()
            return fake

        reader.serial_factory = factory

        connected = reader._connect()

        self.assertFalse(connected)
        self.assertTrue(fake.closed)
        self.assertIsNone(reader.serial_obj)
        self.assertTrue(events.empty())


class StateAndAlertTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def packet(self, voltage=3.8, sats=8, rssi=-60, arrival=1000.0):
        line = (
            f"128518,8.367500,100.043922,-1.80,{sats},56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            f"1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,{voltage:.3f},-90.500,-0.333"
        )
        return self.monitor.TelemetryParser.parse_packet(line, "port1", rssi, 11.75, arrival)

    def test_port_state_counts_packets_and_malformed_lines(self):
        state = self.monitor.PortState("port1")
        state.record_packet(self.packet())
        state.record_malformed("bad,line")

        self.assertEqual(state.packet_count, 1)
        self.assertEqual(state.malformed_count, 1)
        self.assertEqual(state.latest_packet.millis, 128518)

    def test_alerts_flag_low_voltage_weak_rssi_no_gps_and_stale(self):
        packet = self.packet(voltage=3.3, sats=0, rssi=-120, arrival=900.0)
        alerts = self.monitor.evaluate_alerts(packet, now=1005.0)

        self.assertIn("low_voltage", alerts)
        self.assertIn("weak_rssi", alerts)
        self.assertIn("no_gps_lock", alerts)
        self.assertIn("stale_packet", alerts)

    def test_alerts_none_packet_reports_no_packet(self):
        self.assertEqual(self.monitor.evaluate_alerts(None), {"no_packet"})

    def test_alerts_healthy_packet_returns_empty_set(self):
        alerts = self.monitor.evaluate_alerts(self.packet(), now=1002.0)

        self.assertEqual(alerts, set())

    def test_alerts_missing_rssi_does_not_flag_weak_rssi(self):
        alerts = self.monitor.evaluate_alerts(self.packet(rssi=None), now=1002.0)

        self.assertNotIn("weak_rssi", alerts)

    def test_port_alerts_flag_malformed_burst(self):
        state = self.monitor.PortState("port1")
        state.record_packet(self.packet())
        for _ in range(self.monitor.MALFORMED_BURST_THRESHOLD):
            state.record_malformed("bad,line", arrival_time=1001.0)

        alerts = self.monitor.evaluate_port_alerts(state, now=1002.0)

        self.assertIn("malformed_burst", alerts)
        self.assertEqual(state.last_seen_time, 1001.0)


class GroundStationMonitorAppTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor_module()

    def make_app(self, charts=False):
        try:
            app = self.monitor.GroundStationMonitorApp(use_interactive_map=False)
        except self.monitor.tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        app._request_osm_tiles = lambda tile_key: app.osm_tile_requests.add(tile_key)
        if not charts:
            app._refresh_merge_charts = lambda: None
            app._refresh_port_charts = lambda source: None
        self.addCleanup(self.destroy_app, app)
        return app

    def destroy_app(self, app):
        try:
            if app.winfo_exists():
                app.destroy()
        except self.monitor.tk.TclError:
            pass

    def packet_line(self, millis=128518, voltage=3.684, lat=8.367500, lon=100.043922):
        return (
            f"{millis},{lat:.6f},{lon:.6f},-1.80,12,56.02,24.98,1006.54,"
            "0.1000,0.2000,0.3000,-0.1445,0.2070,0.2324,"
            f"1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,{voltage:.3f},-90.500,-0.333"
        )

    def merged_log_rows(self, path):
        lines = [
            line
            for line in path.read_text().splitlines()
            if line and not line.startswith("#")
        ]
        return list(csv.DictReader(lines))

    def test_connecting_same_source_twice_stops_previous_reader(self):
        app = self.make_app()

        class DummyLogWriter:
            def write_raw(self, source, line, arrival_time=None):
                pass

            def write_merged(self, packet):
                pass

            def close(self):
                pass

        class FakeThread:
            def __init__(self):
                self.joined = False

            def is_alive(self):
                return True

            def join(self, timeout=None):
                self.joined = True

        class FakeSerialReader:
            instances = []

            def __init__(self, source, port, baud, event_queue):
                self.source = source
                self.port = port
                self.baud = baud
                self.event_queue = event_queue
                self.started = False
                self.stopped = False
                self.thread = FakeThread()
                self.instances.append(self)

            def start(self):
                self.started = True
                return self.thread

            def stop(self):
                self.stopped = True

        original_reader = self.monitor.SerialReader
        self.addCleanup(setattr, self.monitor, "SerialReader", original_reader)
        self.monitor.SerialReader = FakeSerialReader
        app.log_writer = DummyLogWriter()

        app.port_vars["port1"].set("/dev/first")
        app._connect_port("port1")
        first = FakeSerialReader.instances[0]

        app.port_vars["port1"].set("/dev/second")
        app._connect_port("port1")
        second = FakeSerialReader.instances[1]

        self.assertTrue(first.started)
        self.assertTrue(first.stopped)
        self.assertTrue(first.thread.joined)
        self.assertTrue(second.started)
        self.assertFalse(second.stopped)
        self.assertIs(app.readers["port1"], second)
        self.assertIs(app.reader_threads["port1"], second.thread)
        self.assertTrue(app.disconnect1_button.winfo_exists())
        self.assertTrue(app.disconnect2_button.winfo_exists())

        app._disconnect_port("port1")
        self.assertTrue(second.stopped)
        self.assertNotIn("port1", app.readers)
        self.assertEqual(app.status_vars["port1"].get(), "offline: disconnected")

    def test_stop_logging_control_closes_active_logger(self):
        app = self.make_app()

        class DummyLogWriter:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        writer = DummyLogWriter()
        app.log_writer = writer

        self.assertTrue(app.stop_logging_button.winfo_exists())
        app._stop_logging()

        self.assertTrue(writer.closed)
        self.assertIsNone(app.log_writer)
        self.assertIn("Logging stopped", app.summary_var.get())

    def test_chart_figures_are_created_and_refreshed_from_packets(self):
        app = self.make_app(charts=True)

        self.assertEqual(app.gps_ax.get_title(), "GPS Track")
        self.assertEqual(app.alt_ax.get_title(), "Altitude")
        self.assertEqual(app.link_ax.get_title(), "RSSI/SNR")
        for source in ("port1", "port2"):
            figures = getattr(app, f"{source}_figures")
            self.assertEqual(set(figures), {"altitude", "voltage", "rssi"})

        app.port_states["port1"].record_link(-60, 9.0)
        app._handle_line("port1", self.packet_line(millis=10), arrival_time=1000.0)
        app.port_states["port2"].record_link(-70, 8.0)
        app._handle_line("port2", self.packet_line(millis=11), arrival_time=1001.0)
        app._refresh_dirty_charts(force=True)

        self.assertGreaterEqual(len(app.gps_ax.lines), 1)
        self.assertEqual(app.gps_ax.get_xlabel(), "longitude")
        self.assertEqual(app.gps_ax.get_ylabel(), "latitude")
        self.assertGreaterEqual(len(app.gps_ax.collections), 2)
        self.assertGreaterEqual(len(app.alt_ax.lines), 2)
        self.assertEqual(len(app.link_ax.lines), 4)
        self.assertGreaterEqual(len(getattr(app, "port1_figures")["altitude"][1].lines), 1)
        self.assertGreaterEqual(len(getattr(app, "port1_figures")["voltage"][1].lines), 1)
        self.assertGreaterEqual(len(getattr(app, "port1_figures")["rssi"][1].lines), 2)

    def test_theme_toggle_switches_button_label_and_chart_colors(self):
        app = self.make_app(charts=True)

        self.assertEqual(app.theme_name, "light")
        self.assertEqual(app.theme_button_var.get(), "Dark Mode")

        app._toggle_theme()

        dark_colors = self.monitor.THEME_COLORS["dark"]
        self.assertEqual(app.theme_name, "dark")
        self.assertEqual(app.theme_button_var.get(), "Light Mode")
        self.assertEqual(app.alt_fig.get_facecolor(), self.monitor.Figure(facecolor=dark_colors["chart_bg"]).get_facecolor())

        app._toggle_theme()

        self.assertEqual(app.theme_name, "light")
        self.assertEqual(app.theme_button_var.get(), "Dark Mode")

    def test_merge_link_chart_refreshes_for_non_selected_duplicate_packets(self):
        app = self.make_app(charts=True)

        app.port_states["port1"].record_link(-50, 9.0)
        app._handle_line("port1", self.packet_line(millis=1), arrival_time=1000.0)
        app._refresh_dirty_charts(force=True)
        self.assertEqual([line.get_label() for line in app.link_ax.lines], ["Port 1 RSSI", "Port 1 SNR"])

        app.port_states["port2"].record_link(-80, 8.0)
        app._handle_line("port2", self.packet_line(millis=1), arrival_time=1001.0)
        app._refresh_dirty_charts(force=True)

        self.assertEqual(app.merged_count, 1)
        self.assertEqual(app.merge_buffer.selected[1].source, "port1")
        self.assertEqual(
            [line.get_label() for line in app.link_ax.lines],
            ["Port 1 RSSI", "Port 1 SNR", "Port 2 RSSI", "Port 2 SNR"],
        )

    def test_merge_readout_includes_current_and_watt(self):
        app = self.make_app()

        app.port_states["port1"].record_link(-50, 9.0)
        app._handle_line("port1", self.packet_line(millis=12), arrival_time=1000.0)

        readout = app.readout_var.get()
        self.assertIn("Voltage: 3.684 V", readout)
        self.assertIn("Current: -90.500 mA", readout)
        self.assertIn("Watt: -0.333 W", readout)

    def test_inline_vibration_replay_row_updates_packet_and_link_state(self):
        app = self.make_app()
        line = (
            "324091,8.375336,100.080513,-557.60,5,40.96,32.81,1008.34,"
            "0.0000,0.0000,0.0000,-0.0117,0.0059,0.0000,"
            "1.0000,0.0000,0.0000,0.0000,0.00,0.00,0.00,3.436,-116.700"
            "# RSSI=-101 SNR=12.75"
        )

        app._handle_line("port1", line, arrival_time=1000.0)

        state = app.port_states["port1"]
        packet = state.latest_packet
        self.assertEqual(state.packet_count, 1)
        self.assertEqual(state.malformed_count, 0)
        self.assertEqual(app.merged_count, 1)
        self.assertIsNotNone(packet)
        self.assertEqual(packet.rssi, -101)
        self.assertAlmostEqual(packet.snr, 12.75)
        self.assertAlmostEqual(packet.current, -116.700)
        self.assertAlmostEqual(packet.watt, 3.436 * -116.700 / 1000.0)
        self.assertIn("RSSI: -101", app.port1_detail_var.get())

    def test_gps_map_renders_osm_tile_inside_gps_chart(self):
        app = self.make_app(charts=True)
        tile = self.monitor.np.zeros((2, 2, 3), dtype=self.monitor.np.uint8)
        extent = (100.043, 100.045, 8.367, 8.368)

        app.port_states["port1"].record_link(-50, 9.0)
        app._handle_line("port1", self.packet_line(millis=14), arrival_time=1000.0)
        app._refresh_dirty_charts(force=True)

        self.assertEqual(len(app.gps_ax.images), 0)
        self.assertIn("Map: 1 GPS points, 0 OSM tiles loading", app.gps_map_status_var.get())

        tile_key = next(iter(app.osm_tile_requests))
        app._handle_event({"type": "osm_tiles", "tile_key": tile_key, "layers": [(tile, extent)]})
        app._refresh_dirty_charts(force=True)

        self.assertEqual(len(app.gps_ax.images), 1)
        self.assertIn("Map: 1 GPS points, 1 OSM tiles", app.gps_map_status_var.get())

    def test_interactive_gps_map_centers_once_and_extends_path(self):
        class FakeMarker:
            def __init__(self, lat, lon, text):
                self.positions = [(lat, lon)]
                self.text = text

            def set_position(self, lat, lon):
                self.positions.append((lat, lon))

        class FakePath:
            def __init__(self, positions):
                self.position_lists = [list(positions)]

            def set_position_list(self, positions):
                self.position_lists.append(list(positions))

        class FakeMapWidget:
            instances = []

            def __init__(self, *args, **kwargs):
                self.positions = []
                self.markers = []
                self.paths = []
                self.zooms = []
                FakeMapWidget.instances.append(self)

            def pack(self, *args, **kwargs):
                pass

            def set_zoom(self, zoom):
                self.zooms.append(zoom)

            def set_position(self, lat, lon):
                self.positions.append((lat, lon))

            def set_marker(self, lat, lon, text=None):
                marker = FakeMarker(lat, lon, text)
                self.markers.append(marker)
                return marker

            def set_path(self, positions):
                path = FakePath(positions)
                self.paths.append(path)
                return path

        class FakeTkinterMapViewModule:
            TkinterMapView = FakeMapWidget

        original_map_module = self.monitor.tkintermapview
        self.addCleanup(setattr, self.monitor, "tkintermapview", original_map_module)
        self.monitor.tkintermapview = FakeTkinterMapViewModule
        try:
            app = self.monitor.GroundStationMonitorApp(use_interactive_map=True)
        except self.monitor.tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.addCleanup(self.destroy_app, app)

        app.port_states["port1"].record_link(-50, 9.0)
        app._handle_line("port1", self.packet_line(millis=20, lat=8.367500, lon=100.043922), arrival_time=1000.0)
        app._refresh_dirty_charts(force=True)
        app._handle_line("port1", self.packet_line(millis=21, lat=8.367900, lon=100.044500), arrival_time=1001.0)
        app._refresh_dirty_charts(force=True)

        widget = FakeMapWidget.instances[-1]
        self.assertEqual(widget.positions, [(8.367500, 100.043922)])
        self.assertEqual(widget.markers[0].text, "Start")
        self.assertEqual(widget.markers[1].text, "Current")
        self.assertEqual(widget.markers[1].positions[-1], (8.367900, 100.044500))
        self.assertEqual(
            widget.paths[0].position_lists[-1],
            [(8.367500, 100.043922), (8.367900, 100.044500)],
        )
        self.assertIn("Map: 2 GPS points", app.gps_map_status_var.get())

    def test_port_detail_includes_raw_snr_and_log_path(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = self.make_app()
        app.log_writer = self.monitor.LogWriter(pathlib.Path(temp_dir.name), session_id="session")

        app._handle_line("port1", "# RSSI=-61 SNR=11.75", arrival_time=999.0)
        packet_line = self.packet_line(millis=13)
        app._handle_line("port1", packet_line, arrival_time=1000.0)

        detail = app.port1_detail_var.get()
        self.assertIn("SNR: 11.75", detail)
        self.assertIn("Latest raw: 13,8.367500", detail)
        self.assertIn(str(pathlib.Path(temp_dir.name) / "session_port1.csv"), detail)

    def test_replay_file_uses_speed_control_and_resets_invalid_speed(self):
        app = self.make_app()
        replay_path = ROOT / "Data" / "sample_replay.csv"

        class DummyLogWriter:
            def close(self):
                pass

        class FakeReplayReader:
            instances = []

            def __init__(self, path, source, event_queue, speed=1.0, delay_func=None):
                self.path = path
                self.source = source
                self.event_queue = event_queue
                self.speed = speed
                self.delay_func = delay_func
                self.paused = False
                self.stopped = False
                self.resume_count = 0
                self.instances.append(self)

            def start(self):
                return object()

            def pause(self):
                self.paused = True

            def resume(self):
                self.paused = False
                self.resume_count += 1

            def stop(self):
                self.stopped = True

        original_reader = self.monitor.ReplayReader
        original_dialog = self.monitor.filedialog.askopenfilename
        self.addCleanup(setattr, self.monitor, "ReplayReader", original_reader)
        self.addCleanup(setattr, self.monitor.filedialog, "askopenfilename", original_dialog)
        self.monitor.ReplayReader = FakeReplayReader
        self.monitor.filedialog.askopenfilename = lambda **_kwargs: str(replay_path)
        app.log_writer = DummyLogWriter()

        app.replay_speed_var.set("2.5")
        app._choose_replay_file()
        self.assertEqual(FakeReplayReader.instances[-1].path, replay_path)
        self.assertEqual(FakeReplayReader.instances[-1].speed, 2.5)

        app.replay_speed_var.set("bad")
        app._choose_replay_file()
        self.assertEqual(FakeReplayReader.instances[-1].speed, 1.0)
        self.assertEqual(app.replay_speed_var.get(), "1.0")

        self.assertTrue(app.replay_pause_button.winfo_exists())
        self.assertTrue(app.replay_stop_button.winfo_exists())

        app._toggle_replay_pause()
        self.assertTrue(app.replay_paused)
        self.assertEqual(app.replay_pause_var.get(), "Resume Replay")
        self.assertTrue(all(reader.paused for reader in FakeReplayReader.instances))

        app._toggle_replay_pause()
        self.assertFalse(app.replay_paused)
        self.assertEqual(app.replay_pause_var.get(), "Pause Replay")
        self.assertTrue(all(reader.resume_count == 1 for reader in FakeReplayReader.instances))

        app._stop_replay()
        self.assertEqual(app.replay_readers, [])
        self.assertEqual(app.replay_threads, [])
        self.assertTrue(all(reader.stopped for reader in FakeReplayReader.instances))

    def test_port_alerts_and_stale_alerts_are_visible_in_dashboard(self):
        app = self.make_app()

        app.port_states["port1"].record_link(-50, 10.0)
        app._handle_line("port1", self.packet_line(millis=10), arrival_time=1000.0)
        for index in range(self.monitor.MALFORMED_BURST_THRESHOLD):
            app._handle_line("port1", f"bad,line,{index}", arrival_time=1001.0 + index)

        app._update_port_view("port1", now=1002.0)
        self.assertIn("malformed_burst", app.port1_detail_var.get())

        app._update_summary(now=1000.0 + self.monitor.STALE_PACKET_SECONDS + 1.0)
        self.assertIn("stale_packet", app.port1_detail_var.get())
        self.assertIn("stale_packet", app.merge_status_var.get())

    def test_manual_events_are_rendered_in_timeline(self):
        app = self.make_app()

        app._record_event("Launch")

        rows = app.timeline_tree.get_children()
        self.assertEqual(len(rows), 1)
        values = app.timeline_tree.item(rows[0], "values")
        self.assertEqual(values[1], "Launch")

    def test_lower_rssi_duplicate_does_not_insert_another_merged_row(self):
        app = self.make_app()

        app.port_states["port1"].record_link(-50, 10.0)
        app._handle_line("port1", self.packet_line(millis=200), arrival_time=1000.0)

        self.assertEqual(app.merged_count, 1)
        self.assertEqual(len(app.merged_packets), 1)
        self.assertEqual(len(app.merged_tree.get_children()), 1)

        app.port_states["port2"].record_link(-80, 10.0)
        app._handle_line("port2", self.packet_line(millis=200), arrival_time=1000.1)

        self.assertEqual(app.merged_count, 1)
        self.assertEqual(len(app.merged_packets), 1)
        self.assertEqual(len(app.merged_tree.get_children()), 1)
        self.assertEqual(app.merge_buffer.selected[200].source, "port1")

    def test_higher_rssi_duplicate_replaces_existing_merged_row(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = self.make_app()
        app.log_writer = self.monitor.LogWriter(pathlib.Path(temp_dir.name), session_id="session")
        merged_path = pathlib.Path(temp_dir.name) / "session_merged.csv"

        app.port_states["port1"].record_link(-80, 10.0)
        app._handle_line("port1", self.packet_line(millis=250), arrival_time=1000.0)

        app.port_states["port2"].record_link(-50, 10.0)
        app._handle_line("port2", self.packet_line(millis=250), arrival_time=1000.1)

        selected = app.merge_buffer.selected[250]
        self.assertEqual(app.merged_count, 1)
        self.assertEqual(len(app.merged_packets), 1)
        self.assertIs(app.merged_packets[0], selected)
        self.assertEqual(selected.source, "port2")
        self.assertEqual(selected.rssi, -50)

        rows = app.merged_tree.get_children()
        self.assertEqual(len(rows), 1)
        values = app.merged_tree.item(rows[0], "values")
        self.assertEqual(values[1], "port2")
        self.assertEqual(int(values[4]), -50)

        log_rows = self.merged_log_rows(merged_path)
        rows_for_millis = [row for row in log_rows if row["millis"] == "250"]
        self.assertEqual(len(rows_for_millis), 1)
        self.assertEqual(rows_for_millis[0]["source"], "port2")
        self.assertEqual(rows_for_millis[0]["rssi"], "-50")

    def test_non_tail_higher_rssi_duplicate_updates_replacement_readout(self):
        app = self.make_app()

        app.port_states["port1"].record_link(-80, 10.0)
        app._handle_line("port1", self.packet_line(millis=100, voltage=3.7), arrival_time=1000.0)
        app.port_states["port1"].record_link(-60, 10.0)
        app._handle_line("port1", self.packet_line(millis=200, voltage=3.8), arrival_time=1000.1)

        app.port_states["port2"].record_link(-50, 10.0)
        app._handle_line("port2", self.packet_line(millis=100, voltage=3.9), arrival_time=1000.2)

        selected = app.merge_buffer.selected[100]
        self.assertEqual(app.merged_count, 2)
        self.assertEqual(len(app.merged_packets), 2)
        self.assertIs(app.merged_packets[0], selected)
        self.assertEqual(selected.source, "port2")
        self.assertEqual(selected.rssi, -50)
        self.assertIn("Voltage: 3.900 V", app.readout_var.get())
        self.assertIn("RSSI: -50", app.readout_var.get())

        rows = app.merged_tree.get_children()
        self.assertEqual(len(rows), 2)
        values_by_millis = {
            int(app.merged_tree.item(row, "values")[0]): app.merged_tree.item(row, "values")
            for row in rows
        }
        self.assertEqual(values_by_millis[100][1], "port2")
        self.assertEqual(int(values_by_millis[100][4]), -50)
        self.assertEqual(values_by_millis[200][1], "port1")

    def test_aged_out_higher_rssi_duplicate_does_not_reenter_display_list(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = self.make_app()
        app.log_writer = self.monitor.LogWriter(pathlib.Path(temp_dir.name), session_id="session")
        merged_path = pathlib.Path(temp_dir.name) / "session_merged.csv"

        app.port_states["port1"].record_link(-80, 10.0)
        app._handle_line("port1", self.packet_line(millis=10), arrival_time=1000.0)
        app.port_states["port1"].record_link(-60, 10.0)
        app._handle_line("port1", self.packet_line(millis=20), arrival_time=1000.1)

        visible_packet = app.merged_packets[-1]
        app.merged_packets = [visible_packet]
        app._update_merge_view(rebuild_tree=True)

        app.port_states["port2"].record_link(-50, 10.0)
        app._handle_line("port2", self.packet_line(millis=10), arrival_time=1000.2)

        self.assertEqual(app.merge_buffer.selected[10].source, "port2")
        self.assertEqual(app.merge_buffer.selected[10].rssi, -50)
        self.assertEqual(app.merged_count, 2)
        self.assertEqual(app.merged_packets, [visible_packet])

        rows = app.merged_tree.get_children()
        self.assertEqual(len(rows), 1)
        values = app.merged_tree.item(rows[0], "values")
        self.assertEqual(int(values[0]), 20)
        self.assertEqual(values[1], "port1")

        log_rows = self.merged_log_rows(merged_path)
        rows_by_millis = {row["millis"]: row for row in log_rows}
        self.assertEqual(rows_by_millis["10"]["source"], "port2")
        self.assertEqual(rows_by_millis["10"]["rssi"], "-50")
        self.assertEqual(rows_by_millis["20"]["source"], "port1")

    def test_replacement_after_display_cap_preserves_full_merged_log(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = self.make_app()
        app.log_writer = self.monitor.LogWriter(pathlib.Path(temp_dir.name), session_id="session")
        merged_path = pathlib.Path(temp_dir.name) / "session_merged.csv"

        app.port_states["port1"].record_link(-80, 10.0)
        for millis in range(301):
            app._handle_line("port1", self.packet_line(millis=millis), arrival_time=1000.0 + millis)

        self.assertEqual(app.merged_count, 301)
        self.assertEqual(len(app.merged_packets), 300)
        self.assertNotIn(0, [packet.millis for packet in app.merged_packets])

        app.port_states["port2"].record_link(-50, 10.0)
        app._handle_line("port2", self.packet_line(millis=1), arrival_time=1400.0)

        log_rows = self.merged_log_rows(merged_path)
        self.assertEqual(len(log_rows), 301)
        rows_by_millis = {int(row["millis"]): row for row in log_rows}
        self.assertIn(0, rows_by_millis)
        self.assertEqual(rows_by_millis[1]["source"], "port2")
        self.assertEqual(rows_by_millis[1]["rssi"], "-50")

    def test_replacement_after_merge_buffer_trim_preserves_full_deduped_log(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = self.make_app()
        app.log_writer = self.monitor.LogWriter(pathlib.Path(temp_dir.name), session_id="session")
        merged_path = pathlib.Path(temp_dir.name) / "session_merged.csv"

        app.port_states["port1"].record_link(-80, 10.0)
        for millis in range(2001):
            app._handle_line("port1", self.packet_line(millis=millis), arrival_time=1000.0 + millis)

        self.assertNotIn(0, app.merge_buffer.selected)
        self.assertEqual(app.merged_count, 2001)

        app.port_states["port2"].record_link(-50, 10.0)
        app._handle_line("port2", self.packet_line(millis=0), arrival_time=4000.0)

        self.assertEqual(app.merged_count, 2001)
        self.assertEqual(app.merge_buffer.selected[0].source, "port2")
        self.assertEqual(app.merge_buffer.selected[0].rssi, -50)

        log_rows = self.merged_log_rows(merged_path)
        self.assertEqual(len(log_rows), 2001)
        rows_for_zero = [row for row in log_rows if row["millis"] == "0"]
        self.assertEqual(len(rows_for_zero), 1)
        self.assertEqual(rows_for_zero[0]["source"], "port2")
        self.assertEqual(rows_for_zero[0]["rssi"], "-50")

    def test_stronger_duplicates_after_merge_buffer_trim_do_not_grow_selected_unbounded(self):
        app = self.make_app()

        app.port_states["port1"].record_link(-80, 10.0)
        for millis in range(3000):
            app._handle_line("port1", self.packet_line(millis=millis), arrival_time=1000.0 + millis)

        self.assertEqual(len(app.merge_buffer.selected), app.merge_buffer.max_packets)

        app.port_states["port2"].record_link(-50, 10.0)
        for millis in range(1000):
            app._handle_line("port2", self.packet_line(millis=millis), arrival_time=5000.0 + millis)

        self.assertLessEqual(len(app.merge_buffer.selected), app.merge_buffer.max_packets)
        self.assertEqual(app.merged_count, 3000)
        rows_for_trimmed_duplicates = [
            packet for packet in app.merged_log_packets if packet.millis < 1000 and packet.source == "port2"
        ]
        self.assertEqual(len(rows_for_trimmed_duplicates), 1000)

    def test_weaker_duplicate_after_merge_buffer_trim_keeps_full_log_selection(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = self.make_app()
        app.log_writer = self.monitor.LogWriter(pathlib.Path(temp_dir.name), session_id="session")
        merged_path = pathlib.Path(temp_dir.name) / "session_merged.csv"

        app.port_states["port1"].record_link(-50, 10.0)
        for millis in range(2001):
            app._handle_line("port1", self.packet_line(millis=millis), arrival_time=1000.0 + millis)

        self.assertNotIn(0, app.merge_buffer.selected)
        self.assertEqual(app.merged_count, 2001)

        app.port_states["port2"].record_link(-80, 10.0)
        app._handle_line("port2", self.packet_line(millis=0), arrival_time=4000.0)

        self.assertEqual(app.merged_count, 2001)

        log_rows = self.merged_log_rows(merged_path)
        self.assertEqual(len(log_rows), 2001)
        rows_for_zero = [row for row in log_rows if row["millis"] == "0"]
        self.assertEqual(len(rows_for_zero), 1)
        self.assertEqual(rows_for_zero[0]["source"], "port1")
        self.assertEqual(rows_for_zero[0]["rssi"], "-50")

    def test_old_generation_line_after_reconnect_is_ignored(self):
        app = self.make_app()

        class DummyLogWriter:
            def write_raw(self, source, line, arrival_time=None):
                pass

            def write_merged(self, packet):
                pass

            def close(self):
                pass

        class FakeThread:
            def is_alive(self):
                return False

        class FakeSerialReader:
            instances = []

            def __init__(self, source, port, baud, event_queue):
                self.source = source
                self.event_queue = event_queue
                self.stopped = False
                self.instances.append(self)

            def start(self):
                return FakeThread()

            def stop(self):
                self.stopped = True

        original_reader = self.monitor.SerialReader
        self.addCleanup(setattr, self.monitor, "SerialReader", original_reader)
        self.monitor.SerialReader = FakeSerialReader
        app.log_writer = DummyLogWriter()

        app._connect_port("port1")
        old_reader = FakeSerialReader.instances[0]
        app._connect_port("port1")
        new_reader = FakeSerialReader.instances[1]

        old_reader.event_queue.put({
            "type": "line",
            "source": "port1",
            "line": self.packet_line(millis=300),
            "arrival_time": 1000.0,
        })
        stale_event = app.event_queue.get_nowait()
        app._handle_event(stale_event)

        self.assertEqual(app.port_states["port1"].packet_count, 0)
        self.assertEqual(app.merged_count, 0)
        self.assertEqual(len(app.merged_tree.get_children()), 0)
        self.assertTrue(old_reader.stopped)

        new_reader.event_queue.put({
            "type": "line",
            "source": "port1",
            "line": self.packet_line(millis=300),
            "arrival_time": 1000.1,
        })
        current_event = app.event_queue.get_nowait()
        app._handle_event(current_event)

        self.assertEqual(app.port_states["port1"].packet_count, 1)
        self.assertEqual(app.merged_count, 1)
        self.assertEqual(len(app.merged_tree.get_children()), 1)

    def test_malformed_lines_do_not_duplicate_previous_port_row(self):
        app = self.make_app()

        app._handle_line("port1", self.packet_line(millis=400), arrival_time=1000.0)
        tree = app.port1_tree

        self.assertEqual(app.port_states["port1"].packet_count, 1)
        self.assertEqual(len(tree.get_children()), 1)

        app._handle_line("port1", "bad,line", arrival_time=1000.1)
        app._handle_line("port1", "still,bad", arrival_time=1000.2)

        self.assertEqual(app.port_states["port1"].packet_count, 1)
        self.assertEqual(app.port_states["port1"].malformed_count, 2)
        self.assertEqual(len(tree.get_children()), 1)
        self.assertIn("Malformed: 2", app.port1_detail_var.get())
