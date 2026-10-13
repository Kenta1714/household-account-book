# 家計簿アプリ AI開発ガイド

## ドキュメント同期ルール

- `CLAUDE.md` と `AGENTS.md` は常に同じ内容を維持する
- どちらか一方を修正する場合は、同じ変更をもう一方にも反映する

## プロジェクト概要

Flask製のローカル家計簿Webアプリ。Gmail連携・PayPay連携による自動取込、カテゴリ管理、ダッシュボード可視化が主な機能。

## 技術スタック

- **バックエンド**: Python 3.12+, Flask, SQLAlchemy, SQLite
- **フロントエンド**: Bootstrap 5, Chart.js, Vanilla JS
- **パッケージ管理**: uv
- **外部API**: Gmail API (OAuth 2.0 + PKCE)

## 開発サーバー起動

```bash
APP_ENV=development uv run flask --app app:create_app run --debug --port 8080
# → http://localhost:8080
```

環境別設定・必須環境変数・Renderデプロイ手順は `README.md` を参照。
設定は `Config` / `DevelopmentConfig` / `ProductionConfig` で分け、
`create_app()` 内でDB初期化前に検証する。開発・本番ともBasic認証は必須。
既存データを維持するため `DATABASE_URL` の接続先を変更しないこと。
GmailトークンはDB保存に統一し、通信・DB障害時は削除しない。

## ディレクトリ構成

```
app/
├── __init__.py          # create_app(), 拡張機能・Blueprint登録, スケジューラ起動
├── commands.py          # Flask CLIコマンド
├── config.py
├── extensions.py        # db, scheduler インスタンス
├── models/              # SQLAlchemy モデル
├── routes/              # Flask Blueprint (dashboard, transactions, gmail, paypay, categories)
├── services/            # ビジネスロジック (gmail_service, email_parser, paypay_parser)
├── templates/           # Jinja2 テンプレート
└── static/
tests/                  # unittestテスト
migrations/             # Alembic設定・バージョン別DB変更
```

## DBマイグレーション方針

DBスキーマはAlembicで管理する。`create_app()` では `db.create_all()`、マイグレーション、初期データ登録を実行しない。アプリの起動前に次を明示的に実行する。

```bash
APP_ENV=development uv run alembic upgrade head
APP_ENV=development uv run flask --app app:create_app seed-categories
```

モデルやテーブルを変更する場合は `migrations/versions/` に新しいリビジョンを追加する。マイグレーション例外を握りつぶさず、失敗時はデプロイを停止する。既存データ保護のため、DB構造を確認せず `alembic stamp` を使用しない。初期カテゴリは冪等な `seed-categories` CLIで登録し、既存カテゴリを変更しない。

## 重要な設計パターン

- **Blueprint**: 各機能はBlueprintで分離（`url_prefix` あり）
- **Gmailスキャン非同期処理**: `threading.Thread` + モジュールレベルの `_scan_progress` dict でスレッドセーフな進捗管理
- **スキャン進捗ポーリング**: フロントエンドが `/gmail/scan/progress` を1秒ごとにfetch
- **フラッシュメッセージの制限**: バックグラウンドスレッドからはFlaskの `flash()` が使えないため、URLクエリパラメータで結果を渡しJSで表示

## セキュリティ指針（最優先）

- Basic認証のパスワードは15文字以上のランダムな専用値を使い、認証失敗のレート制限を維持する。制限状態はプロセス内管理のためworker・インスタンス数は1とする
- ユーザー入力はHTMLテンプレート内でJinja2の自動エスケープに任せる（`{{ }}`）
- JSでHTMLを動的生成する場合は必ず `escHtml()` 関数でエスケープする（XSS防止）
- SQLインジェクション防止のためSQLAlchemy ORMを使い、生SQLは避ける
- `credentials.json` / `token.json` はGitにコミットしない
- 入力値のバリデーションはサーバーサイドで必ず行う
- 判断に迷う変更はユーザーに確認してから進める

## スタイル・UI方針

- 既存のBootstrap 5ベースのスタイルを大きく変えない
- 新しいUIコンポーネントを追加する場合はBootstrapの既存クラスを優先して使う
- 色やレイアウトの大幅な変更は事前にユーザーに確認する

## テスト

```bash
uv run python -m unittest discover -s tests -v
```

変更内容に応じて関連テストを実行し、必要ならテストを追加・更新する。

## 判断が必要な変更

既存コードと本ガイドから安全に判断できる範囲では作業を進める。仕様を大きく左右する選択、データ削除、破壊的変更、既存データや本番環境に影響する変更は、実施前にユーザーへ確認する。
