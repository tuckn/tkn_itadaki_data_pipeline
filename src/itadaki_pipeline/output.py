"""CLI-only result views. Saved JSON and Python API results retain their own contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config_display import config_lines


def emit(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print("\n".join(config_lines(result)))


def plan_result(plan: Any, *, full: bool) -> dict[str, Any]:
    result = {
        "profile": plan.source.name,
        "device_id": plan.source.device_id,
        "source_path": str(plan.source.itadaki_root),
        "destination_path": str(plan.source.archive_root),
        "delete_after_success": plan.source.delete_after_success,
        "cutoff_date": plan.cutoff_date.isoformat(),
        "date_count": len(plan.dates),
        "date_range": [plan.dates[0].isoformat(), plan.dates[-1].isoformat()]
        if plan.dates
        else None,
        "source_files_present": len(plan.source_files),
        "source_bytes": plan.total_bytes,
        "expected_events_from_present_key_files": plan.expected_events,
        "validation_warnings": list(plan.validation_warnings),
    }
    if full:
        result["source_files"] = [
            {"date": date.isoformat(), "series": series, "path": str(path)}
            for (date, series), path in sorted(plan.source_files.items())
        ]
    elif plan.source_files:
        result["omitted_detail_count"] = len(plan.source_files)
        result["details_hint"] = "Use --full-output for all planned source file paths"
    return result


def source_result(result: Any) -> dict[str, Any]:
    return {
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


def ingest_result(plans: list, results: list, *, full: bool, dry_run: bool) -> dict[str, Any]:
    result = {
        "dry_run": dry_run,
        "counts": {
            "profiles": len(plans),
            "dates": sum(len(plan.dates) for plan in plans),
            "source_files": sum(len(plan.source_files) for plan in plans),
            **{
                key: sum(getattr(result, key) for result in results)
                for key in ("copied_files", "output_updates", "output_unchanged", "deleted_files")
            },
            "warnings": sum(len(plan.validation_warnings) for plan in plans)
            + sum(len(result.warnings) for result in results),
        },
        "plans": [plan_result(plan, full=full) for plan in plans],
        "results": [source_result(result) for result in results],
        "attention": [
            {
                "profile": plan.source.name,
                "source_path": str(plan.source.itadaki_root),
                "destination_path": str(plan.source.archive_root),
                "reason": reason,
            }
            for plan in plans
            for reason in plan.validation_warnings
        ]
        + [
            {
                "profile": result.profile_name,
                "manifest_path": str(result.manifest_path) if result.manifest_path else None,
                "reason": reason,
            }
            for result in results
            for reason in result.warnings
        ],
    }

    attention = {}
    for item in result["attention"]:
        key = (item["profile"], item["reason"])
        attention[key] = {**attention.get(key, {}), **item}
    result["attention"] = list(attention.values())
    result["counts"]["warnings"] = len(attention)
    return result


def verification_result(items: list[dict], config: Any, *, full: bool) -> dict[str, Any]:
    result = {
        "months": len(items),
        "input_events": sum(item["input_events"] for item in items),
        "daily_usage": sum(item["daily_usage"] for item in items),
    }
    if full:
        result["details"] = []
        for item in items:
            year, month = item["month"].split("-")
            base = config.processed_data_root / item["device_id"]
            result["details"].append(
                {
                    **item,
                    "input_events_path": str(base / "InputEvents" / year / f"{month}.csv"),
                    "daily_usage_path": str(base / "DailyUsage" / year / f"{month}.csv"),
                }
            )
    elif items:
        result["omitted_detail_count"] = len(items)
        result["details_hint"] = "Use --details or --full-output for each verified device-month"
    return result


def weekly_result(payload: dict, *, full: bool) -> dict[str, Any]:
    # Never mutate the API payload or the object written to the saved result JSON.
    result = dict(payload)
    root = Path(payload["mart_path"])
    result["dry_run"] = not payload["apply"]
    result["generated_week_count"] = len(payload["generated_weeks"])
    result["report_path"] = str(root / "index.html")
    result["weeks_to_generate"] = [
        {**item, "path": str(root / "weeks" / item["week_id"] / "report.html")}
        for item in payload["weeks_to_generate"]
    ]
    if not full:
        result.pop("generated_weeks")
        if payload["generated_weeks"]:
            result["omitted_detail_count"] = len(payload["generated_weeks"])
            result["details_hint"] = "Use --full-output for every generated week ID"
    return result


def failure_result(
    error: BaseException,
    *,
    command: str | None,
    stage: str,
    targets: dict,
    interrupted: bool = False,
) -> dict[str, Any]:
    context = {**targets, **getattr(error, "cli_context", {})}
    context_path = context.pop("path", None)
    path = getattr(error, "filename", None) or context_path
    if getattr(error, "filename2", None):
        context["destination_path"] = error.filename2
    completed = dict(context.pop("completed", {}))
    errors = context.pop("errors", None)
    reason = "interrupted" if interrupted else str(error)
    # YAML diagnostics can quote an entire source line, including edited private settings.
    cause = error
    while cause is not None:
        if type(cause).__module__.startswith("yaml"):
            reason = "Invalid YAML configuration; check syntax in the indicated file"
            break
        cause = cause.__cause__
    if errors is None:
        errors = [
            {
                "path": str(path) if path is not None else None,
                "reason": reason,
                "error_type": type(error).__name__,
            }
        ]
    return {
        "status": "interrupted" if interrupted else "failed",
        "command": command,
        "stage": stage,
        "error_count": len(errors),
        "errors": errors,
        "targets": context,
        "completed": completed,
    }
