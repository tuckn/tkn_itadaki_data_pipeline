# CLI output contract (0.9.0)

This contract covers console results only. Python return values, exceptions,
configuration, ingest manifests, saved build-weekly result JSON, CSVs, and HTML
reports have contracts independent of console formatting. No output flag selects
artifacts or changes write/delete behavior. This CLI makes no AI or network requests.

## Selection and streams

Every command defaults to UTF-8 `key=value` lines. Nested mappings use `.`, lists
use `[0]`, empty containers use `[]`/`{}`, booleans use `true`/`false`, and unset
values use `null`. Strings have no JSON quotes; Windows backslashes, Unicode,
spaces, and `=` remain copyable. Control characters are escaped, including ANSI
escape characters. Paths are never shortened. These lines are for humans; use
JSON for automation.

`--json` selects one valid JSON object on stdout. `--full-output` independently
selects additional details in either format. Both flags and `--quiet`/`--verbose`
work before or after pipeline commands and at any config subcommand level.
Repeated flags do not disappear when a deeper subcommand is parsed. `--` ends
option recognition. Long option abbreviations are not accepted.

Progress, warnings, and diagnostics go to stderr. `--quiet`/`-q` shows errors only;
`--verbose`/`-v` includes debug diagnostics. They cannot be combined and never
change final result presence or format. Pipe, redirect, and TTY state never select
the format. `--help` and `--version` keep their standard text behavior.

## Result fields

| Command | Fields and types |
| --- | --- |
| `config init` | `status`: `created` or `unchanged`; `command`: string; `config_path`: absolute path string |
| `config list` | `status`: `success`; `command`: string; `config_sources`: string array, `values`: object, `winning_sources`: object |
| `verify` | `status`: `success`; `command`: string; `months`, `input_events`, `daily_usage`: integer counts; `details`: array when `--details` or `--full-output` is specified |
| `ingest` | `status`: `success`; `command`: string; `dry_run`: boolean; `counts`: object of integer counts; `plans`, `results`, `attention`: arrays; `log_path`: absolute path string or null |
| `build-weekly` | `status`: `success`; `command`: string; API planning/count/index/watermark fields; `dry_run`: boolean; `generated_week_count`: integer; `report_path`: absolute path string; `log_path`, `result_path`: absolute path strings or null; `browser_status`: string; `attention`: array |

Ingest plans include profile/device, source/archive paths, cutoff/date counts and
range, present source file count/bytes, expected events, validation warnings, and
the deletion setting. `--full-output` adds `source_files[]` objects with `date`,
`series`, and absolute `path`. The default includes `omitted_detail_count` and
`details_hint` when these per-file entries are omitted. Run result entries include
profile/device/date/copy/output/delete counts, manifest path, and warnings.
Warnings are counted once per profile/reason and appear in `attention` with source
or manifest paths separately. No ingest result report file is added; manifests
remain the detailed per-file and per-output record.

Verify details include each API item and absolute `input_events_path` and
`daily_usage_path`. Without details, `omitted_detail_count` and `details_hint` tell
how many device-month entries were omitted and how to retrieve them.

Build-weekly keeps all planned changed weeks and their reasons visible and adds
an absolute report `path` to each `weeks_to_generate[]` item in the CLI view only.
`--full-output` includes all `generated_weeks` IDs; the default replaces that list
with `generated_week_count`, an omitted count and a hint when applicable. The
saved result JSON contains the API fields and
`log_path`, regardless of stdout format/detail flags. Browser status is
`not-requested` on dry-run, `suppressed` with `--no-open`, or `opened`/`failed`.
Browser launch failure leaves the successful build's exit code at 0 and reports
an attention path and reason.

## Failure and interruption results

| Field | Type and meaning |
| --- | --- |
| `status` | `failed` or `interrupted` |
| `command` | command string, or null if parsing did not complete |
| `stage` | `arguments`, `configuration`, `config init`, `ingest`, `verify`, `build-weekly`, or `save result` |
| `error_count` | number of error entries |
| `errors[]` | objects with `path` (string or null), `reason` (string), `error_type` (string) |
| `targets` | available named configuration/source/archive/processed/mart/log/manifest/destination paths; may include `config_paths` as an array |
| `completed` | available partial completion only; an empty object when unknown |
| `dry_run` | boolean when processing mode has been determined |

Python callers receive API exception types/messages and fail-fast behavior
independently of console formatting. CLI error metadata adds file targets without parsing paths back
out of prose. Errors whose exact file is unavailable use null and include the
known target roots. YAML syntax diagnostics avoid quoting source contents.

For partial cleanup failure, all failed source paths are listed separately;
known copy/delete/output counts and the resumable manifest path are reported.
`completed.verified_months` contains earlier verified months if a later one fails;
`completed.generated_weeks` contains completed weeks if later generation fails.
These fields do not imply rollback or resumability beyond existing manifests.
Interruptions use the same available context. A terminated process, broken output
stream, or machine failure can prevent a final document from being delivered.

Exit codes are independent of format: 0 success (including `created`/`unchanged`),
1 configuration/validation/runtime or partial operation failure, 2 invalid
arguments, 130 handled keyboard interruption. Attention warnings alone do not
make an operation fail.

## JSON usage example

```powershell
$result = tkn-itadaki-pipeline --json ingest --dry-run | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Ingest validation failed" }
$result.counts.source_files
```
