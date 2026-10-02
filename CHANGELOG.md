# Changelog

## 0.8.0 - 2026-10-02

- Replace `config show` with the read-only `config list` command.
- Print one `key=value` per line by default, with dotted mapping keys and indexed
  list items. Preserve copyable Windows paths and escape control characters.
- Add `config list --json` for automation, retaining `config_sources` and `values`
  and adding `winning_sources` for file, built-in, and CLI origins.
- Send configuration display notices to stderr without creating persistent logs
  or reports. Allow `--config` and `--profile` before the command or after `list`.

Update calls to `config show` to `config list --json` when JSON is required.
The configuration file format and pipeline behavior are unchanged.
