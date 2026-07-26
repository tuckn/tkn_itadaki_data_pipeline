"""Command-line interface for the Itadaki pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

from .config import public_config, resolve_config
from .pipeline import run_pipeline, verify_config

LOG = logging.getLogger("itadaki_pipeline")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="itadaki-pipeline",
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
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "backfill", "run", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--config",
            type=Path,
            dest="command_config",
            help="Explicit YAML configuration path.",
        )
        if command == "verify":
            subparser.add_argument(
                "--details",
                action="store_true",
                help="Include one result item per verified device-month.",
            )
        if command in ("backfill", "run"):
            subparser.add_argument(
                "--apply",
                action="store_true",
                help="Apply changes. Without this flag the command is a dry-run.",
            )
    config_parser = subparsers.add_parser("config", help="Configuration operations.")
    config_subparsers = config_parser.add_subparsers(
        dest="config_command",
        required=True,
    )
    config_subparsers.add_parser(
        "show",
        help="Show resolved configuration and files used.",
    )
    return parser


def _configure_file_log(log_dir: Path, command: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = log_dir / f"{timestamp}_{command}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)
    return path


def _print_plans(plans: object) -> None:
    payload = []
    for plan in plans:
        payload.append(
            {
                "source": plan.source.name,
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
            "source": result.source_name,
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
    try:
        explicit_config = getattr(args, "command_config", None) or args.config
        resolved = resolve_config(explicit_config=explicit_config)
        config = resolved.config
        if args.command == "config":
            print(
                json.dumps(
                    {
                        "sources": list(resolved.sources),
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

        mode = "backfill" if args.command in ("plan", "backfill") else "run"
        apply = bool(getattr(args, "apply", False)) and args.command != "plan"
        log_path = None
        if apply:
            log_path = _configure_file_log(config.log_dir, args.command)
            LOG.info("Starting %s with %s", args.command, config.config_path)

        plans, results = run_pipeline(config, mode, apply=apply)
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
