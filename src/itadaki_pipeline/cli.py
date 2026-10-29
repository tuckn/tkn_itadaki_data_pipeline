"""Command-line interface for the Itadaki pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    cwd_config_path,
    global_config_path,
    initialize_user_config,
    public_config,
    resolve_config,
)
from .output import (
    emit,
    failure_result,
    ingest_result,
    source_result,
    verification_result,
    weekly_result,
)
from .pipeline import run_pipeline, verify_config
from .weekly import build_weekly

LOG = logging.getLogger("itadaki_pipeline")


class ArgumentFailure(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(2)
        self.message = message


class Parser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        raise ArgumentFailure(message)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Write one JSON result instead of the default key=value list (also on errors).",
    )
    parser.add_argument(
        "--full-output",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Include every result detail in either text or JSON; does not change processing.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Only errors on stderr; always print the final result.",
    )
    group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Include debug diagnostics on stderr; keep the same final result.",
    )


def _parser() -> argparse.ArgumentParser:
    parser = Parser(
        allow_abbrev=False,
        prog="tkn-itadaki-pipeline",
        description=(
            "Archive Itadaki Rec logs and build idempotent processed CSVs. "
            "Results on stdout default to a copyable key=value list; --json selects JSON. "
            "Progress, warnings, and diagnostics go to stderr."
        ),
        epilog=(
            "Exit codes: 0 success, 1 configuration/validation/runtime or partial failure, "
            "2 invalid arguments, 130 interrupted. "
            "Output options may appear before or after commands, including config subcommands."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _common(parser)
    parser.set_defaults(json=False, full_output=False, quiet=False, verbose=False)
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Explicit YAML config. Precedence: global, CWD, explicit. "
            "May also be placed after ingest, verify, build-weekly, or config list."
        ),
    )
    parser.add_argument(
        "--profile",
        help=(
            "Profile name from config. Uses default_profile when omitted. "
            "May also be placed after ingest, verify, build-weekly, or config list."
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
            allow_abbrev=False,
        )
        _common(subparser)
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
                help="Include one result per verified device-month (same as --full-output).",
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
        if command == "build-weekly":
            subparser.add_argument(
                "--no-open",
                action="store_true",
                help=(
                    "Do not open the generated mart index in the OS default browser. "
                    "Dry-run never opens a browser."
                ),
            )
    config_parser = subparsers.add_parser("config", help="Configuration operations.")
    _common(config_parser)
    config_subparsers = config_parser.add_subparsers(
        dest="config_command",
        required=True,
    )
    init_parser = config_subparsers.add_parser(
        "init",
        help="Create the user-global config from the packaged template.",
        description=(
            "Create ~/.tkn/itadaki_data_pipeline/config.yaml. An identical file is "
            "left unchanged; an edited file is never overwritten."
        ),
    )
    _common(init_parser)
    config_list_parser = config_subparsers.add_parser(
        "list",
        help="List resolved configuration and winning sources.",
        description=(
            "Read and resolve configuration without creating or updating config, "
            "state, cache, logs, or reports. Prints one key=value per line by default."
        ),
    )
    _common(config_list_parser)
    config_list_parser.add_argument(
        "--config",
        type=Path,
        dest="command_config",
        help="Explicit YAML configuration path.",
    )
    config_list_parser.add_argument(
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
    handler.setLevel(logging.INFO)
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


def _open_report(path: Path) -> bool:
    """Open an HTML report in the operating system's default browser."""
    return webbrowser.open(path.resolve().as_uri(), new=2)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return message.replace("\x1b", "\\u001b")


def _configure_console(*, quiet: bool, verbose: bool) -> None:
    for handler in list(LOG.handlers):
        if not isinstance(handler, logging.FileHandler):
            LOG.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.ERROR if quiet else logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(ConsoleFormatter("[%(levelname)s] %(message)s"))
    LOG.setLevel(logging.DEBUG)
    LOG.propagate = False
    LOG.addHandler(handler)


def _execute(args: argparse.Namespace, context: dict[str, Any]) -> dict[str, Any]:
    full = args.full_output
    command = args.command
    if command == "config" and args.config_command == "init":
        context["stage"] = "config init"
        context["targets"] = {"path": global_config_path()}
        if args.config is not None:
            raise ValueError("--config cannot be combined with config init")
        if args.profile is not None:
            raise ValueError("--profile cannot be combined with config init")
        path, status = initialize_user_config()
        LOG.info("Configuration %s", status)
        return {"status": status, "command": "config init", "config_path": str(path)}

    explicit_config = getattr(args, "command_config", None) or args.config
    profile_name = getattr(args, "command_profile", None) or args.profile
    context["stage"] = "configuration"
    context["targets"] = {"config_paths": [global_config_path(), cwd_config_path(Path.cwd())]}
    if explicit_config is not None:
        context["targets"]["config_paths"].append(explicit_config.expanduser().absolute())
    resolved = resolve_config(explicit_config=explicit_config, profile_name=profile_name)
    config = resolved.config
    if command == "config":
        _progress("Showing resolved configuration")
        return {
            "status": "success",
            "command": "config list",
            "config_sources": list(resolved.config_sources),
            "values": public_config(config, resolved.paths),
            "winning_sources": resolved.winning_sources,
        }

    context["stage"] = command
    context["targets"] = {
        "config_path": config.config_path,
        "source_path": config.profile.itadaki_root,
        "archive_path": config.profile.archive_root,
        "processed_data_path": config.processed_data_root,
    }
    if command == "verify":
        _progress("Verifying processed CSV files against the Raw archive")
        items = verify_config(config)
        return {
            "status": "success",
            "command": command,
            **verification_result(items, config, full=full or args.details),
        }

    apply = not args.dry_run
    context["dry_run"] = args.dry_run
    if command == "build-weekly":
        context["targets"]["mart_path"] = config.weekly_mart_root
    if apply:
        context["targets"]["path"] = config.log_dir
        log_path = _configure_file_log(config.log_dir, command)
        context["targets"].pop("path")
        context["targets"]["log_path"] = log_path
    else:
        log_path = None
    _progress(f"Starting {command} ({'write' if apply else 'dry-run'})")
    LOG.debug("Selected profile: %s; config: %s", config.selected_profile_name, config.config_path)
    if command == "ingest":
        plans, results = run_pipeline(config, apply=apply)
        result = {
            "status": "success",
            "command": command,
            **ingest_result(plans, results, full=full, dry_run=args.dry_run),
            "log_path": str(log_path) if log_path else None,
        }
        for item in result["attention"]:
            LOG.warning("%s", item["reason"])
        LOG.info(
            "Completed ingest: %d dates, %d copied files",
            result["counts"]["dates"],
            result["counts"]["copied_files"],
        )
        return result

    payload = build_weekly(config, apply=apply, progress=_progress)
    context["targets"]["completed"] = {
        "generated_weeks": list(payload["generated_weeks"]),
        "generated_week_count": len(payload["generated_weeks"]),
    }
    result_path = None
    if log_path is not None:
        payload["log_path"] = str(log_path)
        # Preserve the existing saved payload, independently of --json/--full-output.
        context["stage"] = "save result"
        context["targets"]["path"] = log_path.with_name(f"{log_path.stem}_result.json")
        result_path = _write_result_json(log_path, payload)
        context["targets"].pop("path")
    result = {
        "status": "success",
        "command": command,
        **weekly_result(payload, full=full),
        "result_path": str(result_path) if result_path else None,
        "log_path": str(log_path) if log_path else None,
        "browser_status": "not-requested",
        "attention": [],
    }
    if apply:
        report_path = Path(payload["mart_path"]) / "index.html"
        if args.no_open:
            result["browser_status"] = "suppressed"
        else:
            opened = False
            reason = "Could not open the report in the default browser"
            try:
                opened = _open_report(report_path)
            except (OSError, webbrowser.Error) as error:
                LOG.debug("Browser launch failed", exc_info=True)
                reason = f"{reason}: {error}"
            result["browser_status"] = "opened" if opened else "failed"
            if not opened:
                result["attention"].append({"path": str(report_path), "reason": reason})
                LOG.warning("%s: %s", reason, report_path)
    LOG.info(
        "Completed build-weekly: %d generated, %d unchanged",
        result["generated_week_count"],
        result["unchanged_week_count"],
    )
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tokens = argv[: argv.index("--")] if "--" in argv else argv
    json_output = "--json" in tokens
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    _close_file_logs()
    _configure_console(
        quiet="--quiet" in tokens or "-q" in tokens, verbose="--verbose" in tokens or "-v" in tokens
    )
    context = {"stage": "arguments", "targets": {}}
    command = None
    try:
        args = _parser().parse_args(argv)
        command = args.command
        if command == "config":
            command = f"config {args.config_command}"
        if args.quiet and args.verbose:
            raise ArgumentFailure("--quiet and --verbose cannot be combined")
        _configure_console(quiet=args.quiet, verbose=args.verbose)
        result = _execute(args, context)
        emit(result, json_output=args.json)
        return 0
    except ArgumentFailure as error:
        LOG.error("%s", error.message)
        result = failure_result(
            ValueError(error.message), command=command, stage="arguments", targets={}
        )
        emit(result, json_output=json_output)
        return 2
    except (Exception, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        result = failure_result(
            error,
            command=command,
            stage=context["stage"],
            targets=context["targets"],
            interrupted=interrupted,
        )
        for item in result["errors"]:
            LOG.error("%s", item["reason"])
        LOG.debug("Failure type: %s", type(error).__name__)
        if "dry_run" in context:
            result["dry_run"] = context["dry_run"]
        completed_results = getattr(error, "cli_context", {}).get("completed_results")
        if completed_results is not None:
            result["completed"]["results"] = [source_result(item) for item in completed_results]
            result["targets"].pop("completed_results", None)
        emit(result, json_output=json_output)
        return 130 if interrupted else 1
    finally:
        _close_file_logs()


if __name__ == "__main__":
    raise SystemExit(main())
