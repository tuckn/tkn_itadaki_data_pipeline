# Itadaki pipeline

Itadaki の完了済み日別 `.rec` を発生PC別のRaw archiveへ保存し、分析用の
月次 Bronze CSVへ変換するWindows向けCLIです。当日分と `Total.ini` は移動
せず、dry-run、SHA-256照合、manifest、原子的なCSV置換を行います。

## Setup with uv

開発・実行環境は `uv` で再現できます。

```powershell
uv sync
uv run itadaki-pipeline --help
uv run itadaki-pipeline config show
```

CLIを独立したツールとして導入する場合は次を実行します。

```powershell
uv tool install .
itadaki-pipeline --help
```

## Configuration

YAML設定は次の順で読み込まれ、後の値が前の値を上書きします。

1. `~/.tkn/itadaki_pipeline/config.yaml`
2. CWDの `.tkn/config.yaml`
3. `--config` で明示したYAMLまたは従来のTOML

相対パスは、どの設定ファイルに書かれていてもCWDを基準に解決します。
公開用の書式は [`config.example.yaml`](config.example.yaml) を参照してください。
CWDの `.tkn/config.yaml` は実パスを含むためGit対象外です。

```yaml
timezone: Asia/Tokyo
bronze_path: C:/path/to/bronze/Itadaki
log_path: .local/logs

sources:
  - name: current-pc
    device_id: Example Current PC
    source_path: C:/path/to/active/Itadaki/Rec
    destination_path: C:/path/to/archive/Example Current PC/var/log/Itadaki
    modes:
      - backfill
      - run
    delete_after_success: true
```

- `source_path`: Itadakiの `Rec` フォルダ
- `destination_path`: 検証済み `.rec` を保存する端末別Raw archive
- `bronze_path`: `InputEvents` と `DailyUsage` の出力ルート
- `delete_after_success`: archive、CSV、manifestの確定後に完了日分を
  `source_path` から削除するか

`device_id` はItadakiから取得せず、PCごとに明示します。別PCの履歴を現在の
PCへ誤帰属させないため、端末ごとのsource設定を残してください。

## Commands

```powershell
uv run itadaki-pipeline config show
uv run itadaki-pipeline plan
uv run itadaki-pipeline backfill
uv run itadaki-pipeline backfill --apply
uv run itadaki-pipeline run --apply
uv run itadaki-pipeline verify
```

明示設定を使う場合、`--config` はコマンドの前後どちらにも置けます。

```powershell
uv run itadaki-pipeline --config C:/path/to/config.yaml plan
uv run itadaki-pipeline plan --config C:/path/to/config.yaml
```

`plan` は常にdry-runです。`backfill` と `run` も `--apply` がない限り
ファイルを変更しません。

## Safety model

完了日の `Key`、`MoC`、`MoM`、`Pow` を検証し、Raw archiveへコピーして
SHA-256を照合します。archiveから対象月のCSV全体を一時生成・検証し、同じ
フォルダ内で置換します。manifest確定後、設定で許可されたsourceだけを
削除します。既存archiveとハッシュが異なる場合はsourceを残して停止します。

内部イベント日時とファイル名の日付が異なる実データも破棄しません。
ファイル名の日付をpartition日として維持し、manifestへ警告を記録します。
`Key`由来のマウス件数と `MoC` の日次値も独立して保存し、不一致は警告に
します。

CSVは次の場所へ出力されます。

```text
<bronze-path>/<device-id>/InputEvents/yyyy/MM.csv
<bronze-path>/<device-id>/DailyUsage/yyyy/MM.csv
```

`InputEvents` は `Key` の1レコードを1行として保存します。主な列は
`device_id`、`event_date`、`event_datetime_local`、`event_type`、
`key_code`、`key_name`、`is_mouse` です。

`DailyUsage` は4系列を日単位で統合します。主な列は `key_count`、
`mouse_clicks`、`moc_clicks`、`mouse_move_cm`、`power_on_sec` です。
右クリックは `InputEvents.key_code = 101` で集計できます。

## Repository layout

```text
.
├── src/itadaki_pipeline/       # 配布するCLIパッケージ
├── tests/
│   └── fixtures/
│       ├── itadaki_rec/        # 1日分×4系列の最小バイナリfixture
│       └── legacy_output/      # 互換出力の期待CSV
├── scripts/                    # 運用・検証補助
├── config.example.yaml
├── pyproject.toml
└── uv.lock
```

実際のItadaki本体、`.chm`、大量の `.rec`、生成済みCSVは配布物やsampleに
含めません。テストに必要なバイナリだけを `tests/fixtures/` に置きます。

旧形式の3 CSVが必要な場合は、互換スクリプトをリポジトリ内で実行できます。

```powershell
uv run itadaki-legacy-export C:/path/to/Rec C:/path/to/output
```

`uv run python itadaki_parse.py ...` も互換wrapperとして残しています。

## Scheduled task

`scripts/register_scheduled_task.ps1` は、CWD設定を使って次のコマンドを
毎週実行するWindowsタスクを登録します。

```text
uv run --frozen itadaki-pipeline run --apply
```

```powershell
.\scripts\register_scheduled_task.ps1 `
  -WorkspacePath (Get-Location).Path
```

別の設定を固定したい場合だけ `-ConfigPath` を追加します。登録内容を変更した
後はスクリプトを再実行してください。

## Quality checks

```powershell
uv run pytest
uv run ruff check .
uv build
```
