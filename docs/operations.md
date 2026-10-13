# 設定・運用ガイド

環境別設定、Renderでのデプロイ手順、既存データ・Gmail連携の引き継ぎをまとめています。
アプリの概要・開発背景・設計上の工夫は [README](../README.md) を参照してください。

開発・本番は `APP_ENV` で明示的に切り替えます。
設定が未指定・空・不正なら、DB初期化やスケジューラ起動前にエラーになります。

## ローカル開発

既存の `.env` を使用している場合は上書きせず、不足項目を追記してください。
新規セットアップの場合のみ `.env.example` をコピーして `.env` を作成します。

必須設定:

| 変数 | 設定内容 |
| --- | --- |
| `FLASK_SECRET_KEY` | `openssl rand -hex 32` で生成したランダム値。32文字以上。Renderと別の値 |
| `APP_USERNAME` | Basic認証のユーザー名 |
| `APP_PASSWORD` | 15文字以上。`openssl rand -hex 32` で生成した、このアプリ専用のランダム値を推奨 |
| `DATABASE_URL` | 既存データを利用する場合は**現在の接続先を維持**。既定のローカルDBは `sqlite:///household.db` |
| `GMAIL_REDIRECT_URI` | `http://localhost:8080/gmail/oauth/callback` |
| `CREDENTIALS_FILE` | `credentials.json` など。相対パスはプロジェクトルートから解決 |

```bash
APP_ENV=development uv run alembic upgrade head
APP_ENV=development uv run flask --app app:create_app seed-categories
APP_ENV=development uv run flask --app app:create_app run --debug --port 8080
```

`APP_ENV=development uv run python run.py` も使用できます。
`APP_ENV` は `.env` の読込より先に必要なので、起動コマンドで指定してください。
開発のみ `.env` を読み込み、同名のOS環境変数を優先します。
ローカルHTTP OAuthはlocalhostのコールバックに限りアプリが許可します。
Google Cloud側にも同じリダイレクトURIを登録してください。
開発でもBasic認証が必須です。

## Render本番

**コードのデプロイより先に**、RenderのEnvironmentに以下を設定してください。
現在の値を使用できるものはそのまま維持し、特にDBの接続先を変更しないでください。

| 変数 | 設定内容 |
| --- | --- |
| `APP_ENV` | `production` |
| `FLASK_SECRET_KEY` | 既存の本番用ランダム値。32文字以上 |
| `APP_USERNAME` / `APP_PASSWORD` | Basic認証情報。パスワードは15文字以上のランダムな専用値（開発環境・他サービスと使い回さない） |
| `DATABASE_URL` | **既存の本番PostgreSQLへの接続URL**。`postgres://` 形式も対応 |
| `GMAIL_REDIRECT_URI` | `https://対象サービスのホスト名/gmail/oauth/callback` |
| `GOOGLE_CREDENTIALS_JSON` | 既存Google OAuthクライアントのJSON全体 |

`OAUTHLIB_INSECURE_TRANSPORT` は削除してください（`0` でもHTTP許可になるため使用不可）。
`FLASK_DEBUG` は未設定または `0` にします。
本番は `.env` を読み込まず、セッションCookieはSecure / HttpOnly / SameSite=Laxです。

Build Command:

```sh
pip install uv && uv sync --frozen
```

Pre-Deploy Command:

```sh
FLASK_SKIP_DOTENV=1 uv run alembic upgrade head && FLASK_SKIP_DOTENV=1 uv run flask --app app:create_app seed-categories
```

DBマイグレーションと初期カテゴリ登録が成功した場合だけ、新しいアプリを起動します。
`seed-categories` は不足している既定カテゴリだけを追加し、既存カテゴリは変更しません。

Start Command（既存設定を維持できます）:

```sh
uv run gunicorn run:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

スケジューラ・スキャン進捗・認証失敗のレート制限はプロセス内で管理しているため、worker数・サービスのインスタンス数は1にしてください。
開発リローダーの親プロセスやFlask CLIの保守コマンドではスケジューラを起動しません。
本番の起動・保守コマンドにFlask CLIを使う場合は、CLI自体のdotenv読込を防ぐため `FLASK_SKIP_DOTENV=1` も指定します。

## 既存データ・Gmail連携の維持

DBスキーマはAlembicで管理します。`create_app()` はテーブル作成・カラム追加・初期データ登録を行いません。
新規DBと、この変更より前から使用しているDBのどちらも、最初に `alembic upgrade head` を実行してください。
初回マイグレーションは既存テーブルを検出し、不足している従来カラムだけを追加するため、
取引・カテゴリ・設定・DB保存済みのGmailトークンを削除しません。
マイグレーションが失敗した場合はエラーで停止します。
既存DBへの接続を維持し、デプロイ前には通常のDBバックアップも取得してください。

Alembicの履歴だけを変更する `alembic stamp` は、DB構造と履歴を食い違わせる可能性があるため使用しないでください。
初回マイグレーションは既存DBの引継ぎも担当するため、`downgrade` には対応していません。

トークンの読込・保存先は両環境でDBに統一しました。
DB障害・一時的なGoogle通信エラーでトークンを削除したり、ファイル保存へ切り替えたりしません。
Googleが `invalid_grant` として失効を確認した場合のみDBトークンを削除します。
初回未連携（DBにトークンがない）は正常な状態です。

従来 `token.json` / `TOKEN_FILE_PATH` のファイルだけにトークンを保存していた場合は、対象DBへ明示的に取り込めます。
DBにトークンが既にある場合は上書きせず終了し、元ファイルは削除・変更しません。

```bash
APP_ENV=development uv run flask --app app:create_app import-gmail-token --file token.json
```

本番の旧ファイルを取り込む場合は、Render側の実行環境で、本番設定と `FLASK_SKIP_DOTENV=1` を使用して同じコマンドを実行してください。
`TOKEN_FILE_PATH` は通常稼働では使いません。古いファイルのパスは `--file` で指定します。

デプロイ前から進行中だったOAuthは `state` 検証の追加により失敗することがあります。
その場合のみGmail連携を最初からやり直してください。既存DBトークンは影響を受けません。

## Gmailの取込日付と差分検索

メールのDateヘッダー全体を解析し、メールに記載された地域での日付を取引日に使用します。
解析できない日付は現在日で補完せずスキップし、スキャン履歴の内訳・詳細に「日付解析失敗」として記録します。
Gmailのスキャン履歴日時は、正常終了したスキャンの開始時刻（UTC）です。
次回の差分検索はその時刻から5分戻し、重なる期間のメールはGmailメッセージIDで重複排除します。
スキャン失敗時は検索基準を更新しません。初回の差分検索は過去90日、一括取り込みは全件が対象です。

スキャン開始・実行の失敗時は、画面と進捗APIに固定メッセージを返します。
手動スキャンのスレッドと定期スキャンは例外を捕捉し、ログには処理名と例外型だけを記録します。
例外本文・tracebackにはトークンやメール本文、SQLが含まれる可能性があるため記録しません。
SQLAlchemyは `hide_parameters=True`、SQLのechoは無効です。
起動時に設定するログhandlerでは、Google/OAuth・HTTP・SQLAlchemyライブラリのログ詳細も
固定文言へ置き換えます（logger名・時刻・ログレベルは維持）。
外部のログhandlerを追加・置換する場合は、追加後に `configure_logging(app)` を呼び、同じフィルタを適用してください。

## CSRF対策

更新・削除などのPOSTリクエストはFlask-WTFのCSRF保護で検証します。
HTMLフォームには `csrf_token` の隠し入力を、JavaScriptのJSON送信には
`X-CSRFToken` ヘッダーを付けてください。`FormData` 送信にはフォーム内の隠し入力も含まれます。
トークンが未送信・不正・期限切れの場合は処理を実行せずHTTP 400を返します。
JSON送信や `Accept: application/json` を指定したリクエストにはJSONでエラーを返します。
画面を再読み込みしてから操作をやり直してください。
OAuthの `state` 検証は引き続き認証コールバック専用に行います。

## Basic認証の総当たり対策

[OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)に沿って、
`APP_PASSWORD` が15文字未満なら開発・本番ともDB初期化前に起動を拒否します。
既存パスワードが短い場合は、**デプロイ・再起動前に**長いランダムな値へ更新してください。
パスワードマネージャーや次のコマンドで生成した64文字の値を使用し、他のサービス・開発環境との使い回しを避けてください。
ランダム性や使い回しの有無はアプリ側では判定できないため、設定時に確認してください。

```bash
openssl rand -hex 32
```

生成値はローカルの `APP_PASSWORD` またはRenderのEnvironmentへ設定し、Gitに保存しないでください。
本番のアクセスにはHTTPSを使用してください。

認証情報を送信したリクエストが5分間に5回失敗すると、5回目から5分間、保護対象の全URLでHTTP 429を返します。
`Retry-After` ヘッダーは再試行までの残り秒数です。制限中のリクエストで制限時間を延長しません。
失敗履歴はこのアプリの単一アカウント全体で共有し、送信元IP・入力ユーザー名・URLの変更では回避できません。
正常なBasic認証リクエストは失敗回数を増やさず、他の失敗履歴も消しません。
初回アクセスの認証ヘッダー未送信は通常のHTTP 401チャレンジとして扱い、失敗回数には含めません。
不正なAuthorizationヘッダーは失敗として数えます。
`/ping` と既存の公開アイコンURLは制限中も利用できます。

任意の環境変数で調整できます。すべて1以上の整数が必須で、不正値なら起動を拒否します。

| 変数 | 既定値 | 設定内容 |
| --- | --- | --- |
| `BASIC_AUTH_MAX_FAILURES` | `5` | 制限を開始する認証失敗回数 |
| `BASIC_AUTH_WINDOW_SECONDS` | `300` | 認証失敗を数える期間（秒） |
| `BASIC_AUTH_LOCKOUT_SECONDS` | `300` | 制限を継続する時間（秒） |

制限中は正しい認証情報でもアクセスできません。時間が経過すると自動解除されます。
失敗履歴・制限時間はプロセスの再起動でもリセットされます。
複数worker・複数インスタンスへ変更する場合は、認証の制限状態も共有ストレージへ移す必要があります。

## テスト

```bash
uv run python -m unittest discover -s tests -v
```

テストは一時ディレクトリのDB・ダミー認証情報を使用します。
実際の `.env`、Google OAuthファイル、家計簿DB、外部Google APIは使用しません。
