"""Command-line interface for the Itadaki pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from .config import public_config, resolve_config
from .pipeline import run_pipeline, verify_config
from .weekly import build_weekly

LOG = logging.getLogger("itadaki_pipeline")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tkn-itadaki-pipeline",
        description="Archive Itadaki Rec logs and build idempotent processed CSVs.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Explicit YAML config. Precedence: global, CWD, explicit. "
            "May also be placed after a pipeline command."
        ),
    )
    parser.add_argument(
        "--profile",
        help=(
            "Profile name from config. Uses default_profile when omitted. "
            "May also be placed after a pipeline command."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    command_help = {
        "ingest": "Archive completed records and rebuild monthly CSV files.",
        "verify": "Verify processed CSV files against the Raw archive.",
        "build-weekly": "Build weekly, monthly, and yearly activity reports.",
    }
    for command in ("ingest", "verify", "build-weekly"):
        subparser = subparsers.add_parser(
            command,
            help=command_help[command],
            description=command_help[command],
        )
        subparser.add_argument(
            "--config",
            type=Path,
            dest="command_config",
            help="Explicit YAML configuration path.",
        )
        subparser.add_argument(
            "--profile",
            dest="command_profile",
            help="Profile name from config. Uses default_profile when omitted.",
        )
        if command == "verify":
            subparser.add_argument(
                "--details",
                action="store_true",
                help="Include one result item per verified device-month.",
            )
        if command in ("ingest", "build-weekly"):
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help=(
                    "Validate inputs and preview changes without creating, updating, "
                    "or deleting persistent files, including logs and result reports. "
                    "This pipeline does not use network services or generative AI."
                ),
            )
    config_parser = subparsers.add_parser("config", help="Configuration operations.")
    config_subparsers = config_parser.add_subparsers(
        dest="config_command",
        required=True,
    )
    config_show_parser = config_subparsers.add_parser(
        "show",
        help="Show resolved configuration and files used.",
    )
    config_show_parser.add_argument(
        "--profile",
        dest="command_profile",
        help="Profile name from config. Uses default_profile when omitted.",
    )
    return parser


def _configure_file_log(log_dir: Path, command: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    path = log_dir / f"{timestamp}_{command}.log"
    for existing in list(LOG.handlers):
        if isinstance(existing, logging.FileHandler):
            existing.close()
            LOG.removeHandler(existing)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)
    return path


def _close_file_logs() -> None:
    """Detach file handlers so an in-process dry-run cannot append to an old log."""
    for existing in list(LOG.handlers):
        if isinstance(existing, logging.FileHandler):
            existing.close()
            LOG.removeHandler(existing)


def _write_result_json(log_path: Path, payload: dict) -> Path:
    path = log_path.with_name(f"{log_path.stem}_result.json")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _progress(message: str) -> None:
    LOG.info(message)
    print(f"[INFO] {message}", file=sys.stderr)


def _print_plans(plans: object) -> None:
    payload = []
    for plan in plans:
        payload.append(
            {
                "profile": plan.source.name,
                "device_id": plan.source.device_id,
                "source_path": str(plan.source.rec_dir),
                "destination_path": str(plan.source.archive_root),
                "delete_after_success": plan.source.delete_after_success,
                "cutoff_date": plan.cutoff_date.isoformat(),
                "date_count": len(plan.dates),
                "date_range": (
                    [plan.dates[0].isoformat(), plan.dates[-1].isoformat()]
                    if plan.dates
                    else None
                ),
                "source_files_present": len(plan.source_files),
                "source_bytes": plan.total_bytes,
                "expected_events_from_present_key_files": plan.expected_events,
                "validation_warnings": list(plan.validation_warnings),
            }
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_results(results: object) -> None:
    payload = [
        {
            "profile": result.profile_name,
            "device_id": result.device_id,
            "dates": result.dates,
            "copied_files": result.copied_files,
            "output_updates": result.output_updates,
            "output_unchanged": result.output_unchanged,
            "deleted_files": result.deleted_files,
            "manifest": str(result.manifest_path) if result.manifest_path else None,
            "warnings": list(result.warnings),
        }
        for result in results
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _close_file_logs()
    try:
        explicit_config = getattr(args, "command_config", None) or args.config
        profile_name = getattr(args, "command_profile", None) or args.profile
        resolved = resolve_config(
            explicit_config=explicit_config,
            profile_name=profile_name,
        )
        config = resolved.config
        if args.command == "config":
            print(
                json.dumps(
                    {
                        "config_sources": list(resolved.config_sources),
                        "values": public_config(config, resolved.paths),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "verify":
            results = verify_config(config)
            totals = {
                "months": len(results),
                "input_events": sum(item["input_events"] for item in results),
                "daily_usage": sum(item["daily_usage"] for item in results),
            }
            if args.details:
                totals["details"] = results
            print(json.dumps(totals, ensure_ascii=False, indent=2))
            return 0

        if args.command == "build-weekly":
            apply = not args.dry_run
            log_path = (
                _configure_file_log(config.log_dir, args.command) if apply else None
            )
            _progress(
                f"Starting build-weekly ({'write' if apply else 'dry-run'})"
            )
            payload = build_weekly(config, apply=apply, progress=_progress)
            result_path = None
            if log_path is not None:
                payload["log_path"] = str(log_path)
                result_path = _write_result_json(log_path, payload)
                LOG.info("Result JSON: %s", result_path)
            LOG.info("Completed build-weekly")
            print(
                f"[SUCCESS] build-weekly "
                f"{'completed' if apply else 'dry-run completed'}."
            )
            print(f"[INFO] Mart: {payload['mart_path']}")
            print(
                f"[INFO] Weeks: {payload['candidate_week_count']} candidate, "
                f"{len(payload['generated_weeks'])} generated, "
                f"{payload['unchanged_week_count']} unchanged, "
                f"{payload['stale_week_count']} stale."
            )
            if not apply:
                print(
                    f"[INFO] Planned actions: "
                    f"{payload['missing_week_count']} create, "
                    f"{payload['stale_week_count']} replace, "
                    f"0 update, 0 delete, "
                    f"{payload['unchanged_week_count']} skip."
                )
                for target in payload["weeks_to_generate"]:
                    print(
                        f"[INFO] Would {target['status']}: {target['week_id']} "
                        f"({target['reason']})"
                    )
            print(
                f"[INFO] Calendar reports: "
                f"{payload['index']['month_report_count']} monthly, "
                f"{payload['index']['year_report_count']} yearly."
            )
            if result_path is not None and log_path is not None:
                print(f"[INFO] Result JSON: {result_path}")
                print(f"[INFO] Log: {log_path}")
            else:
                print("[INFO] Dry-run only. No persistent files were changed.")
            return 0

        apply = not args.dry_run
        log_path = None
        if apply:
            log_path = _configure_file_log(config.log_dir, args.command)
            LOG.info(
                "Starting ingest with profile %s from %s",
                config.selected_profile_name,
                config.config_path,
            )

        plans, results = run_pipeline(
            config,
            apply=apply,
        )
        _print_plans(plans)
        if apply:
            _print_results(results)
            LOG.info("Completed %s", args.command)
            print(f"Log: {log_path}")
        else:
            print("Dry-run only. No files were changed.")
        return 0
    except Exception as exc:
        LOG.exception("Pipeline failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
