# Itadaki Data Pipeline

WindVoice氏作成のフリーソフト[『あの頂をめざせ！ぐれいと』](https://www.vector.co.jp/soft/win95/util/se263388.html) （以後、Itadaki）が記録したキーボードとマウスの操作ログである日別の`.rec`ファイルを安全に保管し、分析に使える月次CSVへ
変換するCLIです。

当日分の記録は処理せず、完了した日だけを対象にします。ファイルを変更する前に
dry-runで対象を確認でき、実行時はSHA-256照合、manifest、CSVの原子的な置換を
行います。

## 必要なもの

- Windowsで記録されたItadakiの`Rec`フォルダ
- Python 3.11以上
- [uv](https://docs.astral.sh/uv/)

## インストール

リポジトリのソースコードの変更を、再インストールなしで反映するeditable
installation:

```console
uv tool install -e "C:\path\to\tkn_itadaki_data_pipeline"
itadaki-pipeline --help
```

例示したパスは、このリポジトリの実際のフォルダパスに置き換えてください。
リポジトリ内で実行する場合は、次の短い形式も使用できます。

```console
uv tool install -e .
```

editable installationでは、通常のPythonソースの変更は自動的に反映されます。
依存関係や`pyproject.toml`の`[project.scripts]`を変更した場合、または
リポジトリを移動した場合は、インストールコマンドを再実行してください。

ソースコードの変更を追従しない独立したinstallationに切り替える場合:

```console
uv tool install "C:\path\to\tkn_itadaki_data_pipeline" --force
```

### インストールされるコマンド

インストールすると、次のコマンドが使用できるようになります。

- `itadaki-pipeline`: Raw archiveの作成と処理済み月次CSVへの変換に使うコマンド

## 初期設定

ユーザー単位の設定ファイルを作成します。

```console
New-Item -ItemType Directory -Force "$HOME\.tkn\itadaki_data_pipeline"
Copy-Item ".\config.example.yaml" "$HOME\.tkn\itadaki_data_pipeline\config.yaml"
```

作成した`config.yaml`を開き、使用環境に合わせてパスと端末名を変更します。

```yaml
timezone: Asia/Tokyo
processed_data_path: C:/path/to/processed-data/Itadaki

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

- `source_path`: Itadakiの`Rec`フォルダ
- `destination_path`: 検証済み`.rec`を保存する端末別Raw archive
- `processed_data_path`: `InputEvents`と`DailyUsage`の出力ルート
- `device_id`: 記録元のPCを識別する名前
- `modes`: このsourceを`backfill`、`run`のどちらで処理するか
- `delete_after_success`: archive、CSV、manifestの確定後に、完了日分を
  `source_path`から削除するか
- `log_path`: 省略時は`~/.tkn/itadaki_data_pipeline/state/logs`

別PCの履歴を現在のPCへ誤帰属させないため、`device_id`はsourceごとに明示します。
公開用の全設定例は[`config.example.yaml`](config.example.yaml)を参照してください。

設定を確認します。

```console
itadaki-pipeline config show
```

実際に読み込まれた設定ファイルと、解決後の設定値がJSONで表示されます。
ファイルの移動やCSVの作成は行いません。

## 基本的な使用方法

### 1. 処理対象を確認する

```console
itadaki-pipeline plan
```

`modes`に`backfill`を含むsourceについて、処理対象の日付、ファイル数、容量、
検証警告を表示します。`plan`は常にdry-runで、ファイルを変更しません。

### 2. 初回または過去データを処理する

まずdry-runで確認します。

```console
itadaki-pipeline backfill
```

内容を確認してから、実際に処理します。

```console
itadaki-pipeline backfill --apply
```

`modes`に`backfill`を含むsourceが対象です。完了済みの`.rec`をRaw archiveへ
保存し、対象月の処理済みCSVとmanifestを更新します。

### 3. 日常的なデータを処理する

```console
itadaki-pipeline run
```

これはdry-runです。内容を確認してから、次を実行します。

```console
itadaki-pipeline run --apply
```

`modes`に`run`を含むsourceだけが対象です。当日分と`Total.ini`は処理しません。
定期実行にはこのコマンドを使用します。

### 4. 作成済みデータを検証する

```console
itadaki-pipeline verify
```

Raw archiveから月次CSVを再計算して内容の一致を検証し、確認した月数と行数を
表示します。月ごとの詳細も表示する場合:

```console
itadaki-pipeline verify --details
```

`backfill`と`run`は、`--apply`を付けない限りファイルを変更しません。

## 出力

検証済みの元データは、設定した`destination_path`の下へ端末別Raw archiveとして
保存されます。

分析用CSVは次の場所へ出力されます。

```text
<processed-data-path>/<device-id>/InputEvents/yyyy/MM.csv
<processed-data-path>/<device-id>/DailyUsage/yyyy/MM.csv
```

`InputEvents`は、`Key`の1レコードを1行として保存します。主な列は
`device_id`、`event_date`、`event_datetime_local`、`event_type`、
`key_code`、`key_name`、`is_mouse`です。

`DailyUsage`は`Key`、`MoC`、`MoM`、`Pow`の4系列を日単位で統合します。
主な列は`key_count`、`mouse_clicks`、`moc_clicks`、`mouse_move_cm`、
`power_on_sec`です。

## 安全性

処理時には次の保護を行います。

- 当日分と`Total.ini`を処理対象から除外
- 完了日の`Key`、`MoC`、`MoM`、`Pow`を検証
- Raw archiveへのコピー後にSHA-256を照合
- 対象月のCSV全体を一時生成し、検証後に原子的に置換
- manifest確定後に限り、設定で許可されたsourceファイルを削除
- 既存archiveとsourceのハッシュが異なる場合は、sourceを残して停止

内部イベント日時とファイル名の日付が異なるデータも破棄しません。ファイル名の
日付をpartition日として維持し、manifestへ警告を記録します。`Key`由来の
マウス件数と`MoC`の日次値も独立して保存し、不一致は警告にします。

## 設定ファイルの探索順

設定は次の順で読み込まれ、後の値が前の値を上書きします。

1. `~/.tkn/itadaki_data_pipeline/config.yaml`
2. current working directoryの`.tkn/config.yaml`
3. `--config`で明示したYAML

通常はユーザー単位の`~/.tkn/itadaki_data_pipeline/config.yaml`だけで
使用できます。特定の作業フォルダだけ設定を上書きする場合は
`.tkn/config.yaml`を使用します。

明示した設定ファイルを使う場合、`--config`はコマンドの前後どちらにも置けます。

```console
itadaki-pipeline --config C:/path/to/config.yaml plan
itadaki-pipeline plan --config C:/path/to/config.yaml
```

相対パスは、設定ファイルの場所ではなくcurrent working directoryを基準に
解決します。実パスを含む`.tkn/config.yaml`はGitへcommitしないでください。

## ユーザーディレクトリ

永続的なユーザー設定とアプリデータは、次の場所を使用します。

```text
~/.tkn/itadaki_data_pipeline/
├── config.yaml
├── data/
└── state/
    └── logs/
```

- `config.yaml`: ユーザー単位の設定
- `data/`: 今後のアプリ管理データ
- `state/`: ログや実行履歴など、再起動後も必要な状態
- `~/.cache/itadaki_data_pipeline/`: 再生成できるキャッシュ
- `%TMP%`などのplatform標準一時領域: 実行中だけ必要なscratch

この配置は、設定、永続データ、状態、キャッシュ、一時ファイルを分ける
XDG Base Directory Specificationの考え方を尊重しています。WindowsとLinuxの
パスはPythonの`Path.home()`と`tempfile`で解決します。

## 定期実行

Windows Task Schedulerへ毎週の処理を登録する補助スクリプトがあります。

```powershell
.\scripts\register_scheduled_task.ps1 `
  -WorkspacePath (Get-Location).Path
```

現在の登録スクリプトは、毎週日曜日の03:00に次のコマンドを実行します。

```text
uv run --frozen itadaki-pipeline run --apply
```

通常はユーザー単位の設定ファイルが自動的に読み込まれます。別の設定を固定する
場合だけ`-ConfigPath`を追加します。登録内容を変更した後は、スクリプトを
再実行してください。

## 開発

リポジトリ内で開発・検証する場合:

```console
uv sync --locked
uv run pytest
uv run ruff check .
uv build
```

リポジトリにはテストに必要な1日分の最小fixtureだけを含めています。実際の
Itadaki本体、大量の`.rec`、生成済みCSVは含めません。

```text
.
├── src/itadaki_pipeline/
├── tests/
│   └── fixtures/
├── scripts/
├── config.example.yaml
├── pyproject.toml
└── uv.lock
```
