# Tkn Itadaki Data Pipeline

WindVoice氏作成のフリーソフト[『あの頂をめざせ！ぐれいと』](https://www.vector.co.jp/soft/win95/util/se263388.html) （以後、Itadaki）が記録したキーボードとマウスの操作ログである日別の`.rec`ファイルを安全に保管し、分析に使える月次CSVへ
変換し、Itadaki単独の観測値をISO週で集計・可視化するCLIです。

当日分の記録は処理せず、完了した日だけを対象にします。変更を行うコマンドは
オプションなしで実行され、`--dry-run`を明示すると、永続ファイルを変更せずに
対象を確認できます。実行時はSHA-256照合、manifest、CSVの原子的な置換を行います。

## 必要なもの

- Windowsで記録されたItadakiフォルダ内の`Rec`データ
- Python 3.11以上
- [uv](https://docs.astral.sh/uv/)

## インストール

次のコマンドでインストールします。例示している
`C:\path\to\tkn_itadaki_data_pipeline`は、このリポジトリの実際のフォルダパスに
置き換えてください。

```console
cd "C:\path\to\tkn_itadaki_data_pipeline"
uv tool install .
tkn-itadaki-pipeline --help
```

通常のインストールでは、インストール時点のcode、package resource、dependencyが
tool環境へ反映されます。`git pull`などでリポジトリを更新した後は、更新内容を
反映するため再インストールします。

```console
cd "C:\path\to\tkn_itadaki_data_pipeline"
uv tool install . --reinstall
tkn-itadaki-pipeline --help
```

`--force`は、実行ファイルのentry point競合を解消する場合など、tool自体の
強制インストールが必要な場合に限って使用します。

### 開発用のeditable installation

開発時にsource codeの変更をすぐCLIへ反映したい場合は、editable installationを
使用します。

```console
cd "C:\path\to\tkn_itadaki_data_pipeline"
uv tool install -e . --reinstall
```

editable installationでは、通常のsource code変更に再インストールは不要です。
dependency、package metadata、CLI entry pointを変更した場合、またはリポジトリを
移動・renameした場合は、同じcommandを再実行します。

### インストールされるコマンド

インストールすると、次のコマンドが使用できるようになります。

- `tkn-itadaki-pipeline`: Raw archiveの作成と処理済み月次CSVへの変換に使うコマンド

## 初期設定

ユーザー単位の設定ファイルを作成します。

```console
tkn-itadaki-pipeline config init
```

このコマンドは、packageに含まれる公開用テンプレートから
`~/.tkn/itadaki_data_pipeline/config.yaml`を作成し、`created`と絶対パスをJSONで
表示します。同じ内容で再実行した場合は`unchanged`となり、ファイルを書き換えません。
既存ファイルがテンプレートと異なる場合は、編集済み設定を保護するためエラーで停止します。
network access、データ移動、ログ作成は行いません。

作成した`config.yaml`を開き、使用環境に合わせてパスと端末名を変更します。

```yaml
default_profile: current-pc
processed_data_path: C:/path/to/processed-data/Itadaki
weekly_mart_path: C:/path/to/marts/activities/computerActivityWeekly

profiles:
  - name: current-pc
    device_id: Example Current PC
    timezone: Asia/Tokyo
    source_path: C:/path/to/active/Itadaki
    destination_path: C:/path/to/archive/Example Current PC/var/log/Itadaki
    delete_after_success: true
```

- `default_profile`: `--profile`を省略したときに使用するprofile名
- `profiles`: PCごとの入力、archive、timezone、削除設定
- `name`: `--profile`で指定する一意なprofile名
- `source_path`: Itadakiフォルダ。配下の`Rec`はpipelineが自動的に参照
- `destination_path`: 検証済み`.rec`を保存する端末別Raw archiveの親フォルダ。
  pipelineが配下に`Raw`を作成
- `processed_data_path`: `InputEvents`と`DailyUsage`の出力ルート
- `device_id`: 記録元のPCを識別する名前
- `timezone`: 当日分を除外するために使う記録元PCのtimezone。現在は`Asia/Tokyo`
  のみ対応
- `weekly_mart_path`: `build-weekly`が週次martを出力するルート。ほかのコマンド
  では省略可能
- `delete_after_success`: archive、CSV、manifestの確定後に、完了日分を
  `source_path/Rec`から削除するか
- `log_path`: 省略時は`~/.tkn/itadaki_data_pipeline/state/logs`

別PCの履歴を現在のPCへ誤帰属させないため、`device_id`はprofileごとに明示します。
`processed_data_path`と`weekly_mart_path`はprofile間で共有し、処理済みデータは
`device_id`別に保存します。
`config init`が作成するファイルには、公開用の全設定例が含まれています。

以前の`source`選択用設定である`sources`と`modes`は使用できません。各`sources`
entryを`profiles`へ移し、`timezone`をprofile内へ移動して、`default_profile`を
指定してください。`ingest --backfill`も廃止され、過去データは対象profileを
`--profile`で指定して取り込みます。

設定を確認します。

```console
tkn-itadaki-pipeline config list
```

`git config --list`のように、1行に1つの`key=value`を表示します。
`config_sources`は読み込んだ設定ファイル、`values`は解決後の設定値と保存先、
`winning_sources`は各設定値の決定元です。決定元は設定ファイルの絶対パス、
`built-in`（省略時の既定値）、`CLI`（`--profile`指定）で表示します。

nested mappingは`.`、配列要素は`[0]`などのindexで表し、空の配列・mappingは
`[]`・`{}`、booleanは`true`/`false`、未設定値は`null`と表示します。
文字列を引用符で囲まず、Windows pathのbackslashを二重にescapeしないため、
パスをそのままコピーできます。改行などの制御文字はescapeします。

```text
values.selected_profile=current-pc
values.profiles[0].timezone=Asia/Tokyo
values.profiles[0].delete_after_success=true
```

設定file、state、cache、log、reportは作成・更新しません。入力や出力のパスが
まだ存在しなくても設定を確認できます。表示を知らせる`[INFO]`は標準エラーへ、
設定一覧は標準出力へ出します。

JSONを使う自動処理では、`--json`を指定します。

```console
tkn-itadaki-pipeline config list --json
```

0.8.0では旧`config show`を廃止しました。従来のJSONを読み取る処理は
`config list --json`へ変更してください。`config_sources`と`values`は維持し、
`winning_sources`を追加しています。設定ファイルの移行は不要です。
変更履歴は[CHANGELOG.md](CHANGELOG.md)に記載しています。

別のprofileを確認する場合は、`--profile`で`profiles.name`を指定します。

```console
tkn-itadaki-pipeline config list --profile historical-pc
```

## 基本的な使用方法

### 1. 日常的なデータを取り込む

```console
tkn-itadaki-pipeline ingest --dry-run
```

処理対象の日付、ファイル数、容量、検証警告を確認してから、実際に処理します。

```console
tkn-itadaki-pipeline ingest
```

`--profile`を省略すると`default_profile`が対象です。当日分と`Total.ini`は処理
しません。定期実行にはこのコマンドを使用します。

### 2. 初回または過去データを取り込む

まずdry-runで確認します。

```console
tkn-itadaki-pipeline ingest --profile historical-pc --dry-run
```

内容を確認してから、実際に処理します。

```console
tkn-itadaki-pipeline ingest --profile historical-pc
```

指定したprofileだけが対象です。完了済みの`.rec`をRaw archiveへ保存し、対象月の
処理済みCSVとmanifestを更新します。過去ログを残すprofileでは
`delete_after_success: false`を指定します。

### 3. 完全週の活動martを作る

`ingest`が確定したwatermarkまでを対象に、ISO 8601の月曜～日曜で
週次出力を作ります。最初にdry-runでmissing/stale週を確認します。

```console
tkn-itadaki-pipeline build-weekly --dry-run
tkn-itadaki-pipeline build-weekly
```

通常実行が正常に終了すると、生成済みの`index.html`をOS既定のブラウザーで
開きます。ブラウザーを開かない場合は`--no-open`を指定します。`--dry-run`では
ブラウザーを開きません。

```console
tkn-itadaki-pipeline build-weekly --no-open
```

初回は最古のsource rowを含む週から処理し、以後は入力変更、出力欠損、hash
不一致がある完全週だけを再生成します。記録のない週も出力し、活動0とは解釈せず
`observed_date_count: 0`、`source rowなし`と表示します。

`--profile`を省略した場合、`default_profile`の最新の正常な`ingest`をwatermarkに
使用し、共有の`processed_data_path`にある全`device_id`のデータを集計します。

通常実行では処理状況をコンソールへ順次表示し、終了時に対象週数、生成週数、
月次・年次レポート数、mart pathを要約します。入力fingerprintや週ごとの詳細を
含むJSON結果はコンソールへ展開せず、終了時に表示する`Result JSON`のパスへ
保存します。テキストログのパスも`Log`として表示します。

### 4. 作成済みデータを検証する

```console
tkn-itadaki-pipeline verify
```

Raw archiveから月次CSVを再計算して内容の一致を検証し、確認した月数と行数を
表示します。省略時は`default_profile`、`--profile`指定時はそのprofileの
Raw archiveを検証します。月ごとの詳細も表示する場合:

```console
tkn-itadaki-pipeline verify --details
```

`ingest`と`build-weekly`は、オプションなしで名前どおりの変更を行います。
`--dry-run`を指定した場合だけ模擬実行になります。read-onlyの`verify`と
`config list`には`--dry-run`はありません。

### dry-runの境界

`--dry-run`でも通常実行と同じ設定解決、入力読取、`.rec`の形式検証、既存stateの
確認を行い、対象日・件数・path、作成または再生成予定の週、削除設定、skip理由や
警告を表示します。このCLIはnetwork access、外部service、生成AIを使用しません。

dry-runではRaw archive、処理済みCSV、mart、設定、state、cache、log、result JSONを
作成・更新・削除せず、sourceファイルも削除しません。一時的な計算が必要な場合は
platform標準の一時領域だけを使用し、終了時に残しません。dry-runから通常実行までに
入力が変わる可能性があるため、通常実行ではコピー、置換、削除の直前にもhashと
保護条件を再検証します。

## 出力

検証済みの元データは、設定した`destination_path`の下へ端末別Raw archiveとして
保存されます。Itadaki自身が使う`Rec`と区別するため、整理済みarchiveのフォルダ名は
`Raw`です。

```text
<destination-path>/Raw/yyyy/MM/<series>/yyyyMMdd.rec
```

0.7.0より前のpipelineで作成した`<destination-path>/Rec`がある場合は、実行前に
そのフォルダを`Raw`へ一度だけ名前変更してください。`Rec`と`Raw`の両方が存在する
状態での自動統合は行いません。

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

活動martは`weekly_mart_path`へ、UTF-8 BOM付きCSV、UTF-8 JSON、自己完結HTMLで
出力します。Parquetや外部CDNは使用しません。週次レポートに加え、カレンダー月・
年ごとの集計とヒートマップも生成します。月次レポートには、日付×時刻と
曜日×時刻のkeyboard／clickヒートマップを収録します。月次・年次レポートにも
mouseを除く論理キー頻度の上位20キーグラフと全キーの表を収録します。

```text
<weekly-mart-path>/
├── index.html
├── weekly_history.csv
├── manifest.json
├── months/
│   └── 2026-07/
│       └── report.html
├── years/
│   └── 2026/
│       └── report.html
└── weeks/
    └── 2026-W30/
        ├── report.html
        ├── weekly_summary.csv
        ├── daily_activity.csv
        ├── hourly_input.csv
        ├── key_frequency.csv
        └── manifest.json
```

HTMLは件数、分布、時系列、直前週との差分のみを中立に表示します。評価、推奨、
改善案、良否判定は生成しません。棒グラフ、時系列、ヒートマップには軸名を表示し、
各グラフに同じ値を確認できる表を付けます。ヒートマップと月次・年次の論理キー
頻度の表は既定で折りたたみ、必要なときに展開できます。論理キーはAutoHotkey
変換後の値です。

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
tkn-itadaki-pipeline --config C:/path/to/config.yaml ingest --dry-run
tkn-itadaki-pipeline ingest --dry-run --config C:/path/to/config.yaml
```

profileもコマンドの前後どちらにも指定できます。省略時は`default_profile`を
使用します。

```console
tkn-itadaki-pipeline --profile historical-pc ingest --dry-run
tkn-itadaki-pipeline ingest --dry-run --profile historical-pc
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

現在の登録スクリプトは、毎週月曜日の03:00に次のコマンドを実行します。

```text
uv run --frozen tkn-itadaki-pipeline ingest
```

通常はユーザー単位の設定ファイルと、その`default_profile`が自動的に使用されます。
別の設定を固定する場合だけ`-ConfigPath`を追加します。登録内容を変更した後は、
スクリプトを再実行してください。

週次martは取り込みとは別のTaskとして、毎週月曜日の03:10に次のactionを
登録することを推奨します。このリポジトリの補助スクリプトは、そのTaskを
自動登録しません。

```text
uv run --frozen tkn-itadaki-pipeline build-weekly --no-open
```

定期実行ではブラウザーを起動しないよう`--no-open`を指定しています。対話的に
実行する場合は、オプションを省略すると生成後の索引ページが開きます。

分析上の1週間はISO 8601の月曜日～日曜日です。日曜日の03:00では当日分が
watermarkに含まれず、直前のISO週がまだ完全週にならないことがあります。
月曜日に`ingest`を完了してから`build-weekly`を実行することで、直前の日曜日
までを含む完全週を生成できます。両Taskでは「スケジュールされた時刻に開始
できなかった場合、すぐにタスクを実行する」を有効にしてください。登録補助
スクリプトが作る`ingest` Taskでは、この設定が既定で有効です。

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
│   └── resources/
│       └── config.example.yaml
├── tests/
│   └── fixtures/
├── scripts/
├── pyproject.toml
└── uv.lock
```
