from __future__ import annotations

import csv
import datetime as dt
import shutil
import tempfile
import unittest
from pathlib import Path

from itadaki_pipeline.config import PipelineConfig, SourceConfig
from itadaki_pipeline.parser import ParseError, iter_key_file, parse_moc_file
from itadaki_pipeline.pipeline import (
    DAILY_USAGE_HEADER,
    INPUT_EVENTS_HEADER,
    archive_path,
    discover_source,
    run_pipeline,
    verify_config,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REC = ROOT / "tests" / "fixtures" / "itadaki_rec"


class ParserTests(unittest.TestCase):
    def test_known_fixture_values(self) -> None:
        events = list(
            iter_key_file(
                FIXTURE_REC / "Key" / "20260404.rec",
                expected_date=dt.date(2026, 4, 4),
            )
        )
        self.assertEqual(26, len(events))
        self.assertEqual(100, events[0].code)
        self.assertEqual("(LClick)", events[0].key_name)
        self.assertEqual(
            "2026-04-04T17:10:55.529+09:00",
            events[0].datetime_local.isoformat(timespec="milliseconds"),
        )
        self.assertEqual(26, parse_moc_file(FIXTURE_REC / "MoC" / "20260404.rec"))

    def test_filename_date_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ParseError):
            list(
                iter_key_file(
                    FIXTURE_REC / "Key" / "20260404.rec",
                    expected_date=dt.date(2026, 4, 3),
                )
            )

    def test_filename_date_validation_can_be_deferred_to_pipeline(self) -> None:
        events = list(iter_key_file(FIXTURE_REC / "Key" / "20260404.rec"))
        self.assertEqual(26, len(events))


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rec_dir = self.root / "source" / "Rec"
        shutil.copytree(FIXTURE_REC, self.rec_dir)
        self.archive = self.root / "archive"
        self.processed_data = self.root / "processed-data"
        self.source = SourceConfig(
            name="fixture",
            device_id="Example PC",
            rec_dir=self.rec_dir,
            archive_root=self.archive,
            modes=("backfill", "run"),
            delete_after_success=True,
        )
        self.config = PipelineConfig(
            config_path=self.root / "config.yaml",
            timezone_name="Asia/Tokyo",
            timezone=dt.timezone(dt.timedelta(hours=9), name="Asia/Tokyo"),
            processed_data_root=self.processed_data,
            log_dir=self.root / "logs",
            sources=(self.source,),
        )
        self.now = dt.datetime(
            2026, 4, 5, 3, 0, tzinfo=self.config.timezone
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_dry_run_apply_verify_and_rerun(self) -> None:
        plans, results = run_pipeline(
            self.config, "backfill", apply=False, now=self.now
        )
        self.assertEqual(1, len(plans[0].dates))
        self.assertEqual([], results)
        self.assertTrue((self.rec_dir / "Key" / "20260404.rec").exists())
        self.assertFalse(self.archive.exists())

        _, results = run_pipeline(
            self.config, "backfill", apply=True, now=self.now
        )
        result = results[0]
        self.assertEqual(4, result.copied_files)
        self.assertEqual(2, result.output_updates)
        self.assertEqual(4, result.deleted_files)
        self.assertTrue(result.manifest_path and result.manifest_path.exists())
        self.assertFalse((self.rec_dir / "Key" / "20260404.rec").exists())

        event_csv = (
            self.processed_data / "Example PC" / "InputEvents" / "2026" / "04.csv"
        )
        daily_csv = (
            self.processed_data / "Example PC" / "DailyUsage" / "2026" / "04.csv"
        )
        with event_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(INPUT_EVENTS_HEADER, rows[0])
        self.assertEqual(27, len(rows))
        self.assertEqual("2026-04-04T17:10:55.529+09:00", rows[1][2])
        with daily_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(DAILY_USAGE_HEADER, rows[0])
        self.assertEqual(
            [
                "Example PC", "2026-04-04", "2026", "4", "4", "5",
                "0", "26", "26", "26", "19073.4804", "117",
            ],
            rows[1],
        )

        verified = verify_config(self.config)
        self.assertEqual(26, verified[0]["input_events"])
        self.assertEqual(1, verified[0]["daily_usage"])

        plans, results = run_pipeline(
            self.config, "run", apply=True, now=self.now
        )
        self.assertEqual(0, len(plans[0].dates))
        self.assertIsNone(results[0].manifest_path)

    def test_preserved_source_rerun_does_not_rewrite_outputs(self) -> None:
        preserved = SourceConfig(
            **{
                **self.source.__dict__,
                "delete_after_success": False,
            }
        )
        config = PipelineConfig(
            **{**self.config.__dict__, "sources": (preserved,)}
        )
        run_pipeline(config, "backfill", apply=True, now=self.now)
        _, results = run_pipeline(config, "backfill", apply=True, now=self.now)
        self.assertEqual(0, results[0].copied_files)
        self.assertEqual(0, results[0].output_updates)
        self.assertEqual(2, results[0].output_unchanged)
        self.assertTrue((self.rec_dir / "Key" / "20260404.rec").exists())

    def test_current_day_is_excluded(self) -> None:
        plan = discover_source(self.source, dt.date(2026, 4, 3))
        self.assertEqual((), plan.dates)

    def test_missing_series_is_rejected(self) -> None:
        (self.rec_dir / "Pow" / "20260404.rec").unlink()
        with self.assertRaises(ParseError):
            discover_source(self.source, dt.date(2026, 4, 4))

    def test_invalid_size_is_rejected(self) -> None:
        key_path = self.rec_dir / "Key" / "20260404.rec"
        key_path.write_bytes(key_path.read_bytes() + b"x")
        with self.assertRaises(ParseError):
            discover_source(self.source, dt.date(2026, 4, 4))

    def test_archive_hash_collision_preserves_source(self) -> None:
        destination = archive_path(
            self.source, dt.date(2026, 4, 4), "MoC"
        )
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"\x00\x00\x00\x00")
        with self.assertRaises(ParseError):
            run_pipeline(self.config, "backfill", apply=True, now=self.now)
        self.assertTrue((self.rec_dir / "MoC" / "20260404.rec").exists())

if __name__ == "__main__":
    unittest.main()
