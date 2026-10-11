# 家計簿アプリ

Flask製の家計簿アプリ。開発・本番は `APP_ENV` で明示的に切り替えます。
設定が未指定・空・不正なら、DB初期化やスケジューラ起動前にエラーになります。

## ローカル開発

既存の `.env` を使用している場合は上書きせず、不足項目を追記してください。
新規セットアップの場合のみ `.env.example` をコピーして `.env` を作成します。

必須設定:

| 変数 | 設定内容 |
| --- | --- |
| `FLASK_SECRET_KEY` | `openssl rand -hex 32` で生成したランダム値。32文字以上。Renderと別の値 |
| `APP_USERNAME` | Basic認証のユーザー名 |
| `APP_PASSWORD` | Basic認証のパスワード |
| `DATABASE_URL` | 既存データを利用する場合は**現在の接続先を維持**。既定のローカルDBは `sqlite:///household.db` |
| `GMAIL_REDIRECT_URI` | `http://localhost:8080/gmail/oauth/callback` |
| `CREDENTIALS_FILE` | `credentials.json` など。相対パスはプロジェクトルートから解決 |

```bash
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
| `APP_USERNAME` / `APP_PASSWORD` | 既存のBasic認証情報 |
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

Start Command（既存設定を維持できます）:

```sh
uv run gunicorn run:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

スケジューラとスキャン進捗はプロセス内で管理しているため、worker数・サービスのインスタンス数は1にしてください。
開発リローダーの親プロセスやFlask CLIの保守コマンドではスケジューラを起動しません。
本番の起動・保守コマンドにFlask CLIを使う場合は、CLI自体のdotenv読込を防ぐため `FLASK_SKIP_DOTENV=1` も指定します。

## 既存データ・Gmail連携の維持

この変更ではモデル・DBスキーマ・既存の `settings.gmail_token` の保存形式を変更していません。
既存DBへの接続を維持すれば、取引・カテゴリ・設定・DB保存済みのGmailトークンは引き続き使用されます。
デプロイ前には通常のDBバックアップも取得してください。

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

## テスト

```bash
uv run python -m unittest discover -s tests -v
```

テストは一時ディレクトリのDB・ダミー認証情報を使用します。
実際の `.env`、Google OAuthファイル、家計簿DB、外部Google APIは使用しません。
