# Changelog

## 0.9.0 - 2026-10-29

### CLI output

- Apply default copyable `key=value` output to ingest, verify, build-weekly, config
  init, and config list. Select one machine-readable result with `--json` before,
  between, or after command levels. stdout never includes progress or log prose.
- Emit one ingest result object containing plans, results, counts, and the log
  path in either the default list format or explicit JSON output.
- Add `--full-output` independently of format selection: planned source paths,
  verified device-months, and generated week IDs. Keep `verify --details` as the
  existing detailed view. Omitted details include counts and a retrieval hint.
- Keep the final result visible under `--quiet`/`--verbose`; send progress,
  warnings, and diagnostics only to stderr. No format switch for pipes or TTYs.
- Emit structured failures and target paths for argument, configuration, runtime,
  validation, cleanup, and interruption paths; retain known partial completion.
  Exit codes remain 0 for success, 1 for operation failures, and 2 for invalid
  arguments; handled interruptions return 130. Browser-only launch failures
  remain successful builds with attention details.
- Add ordinary `--version` help and document the contract in
  [docs/cli-output.md](docs/cli-output.md).

Python API return values and exception types/messages, persisted JSON/manifests,
CSV/HTML content, profile/config resolution, writes and dry-run behavior remain
unchanged. `--json` and detail flags only affect console results.

## 0.8.0 - 2026-10-02

- Replace `config show` with the read-only `config list` command.
- Print one `key=value` per line by default, with dotted mapping keys and indexed
  list items. Preserve copyable Windows paths and escape control characters.
- Add `config list --json` for automation, retaining `config_sources` and `values`
  and adding `winning_sources` for file, built-in, and CLI origins.
- Send configuration display notices to stderr without creating persistent logs
  or reports. Allow `--config` and `--profile` before the command or after `list`.

The configuration file format and pipeline behavior are unchanged.
