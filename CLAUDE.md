# CLAUDE.md — 家計簿アプリ プロジェクトガイド

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
├── __init__.py          # create_app(), DB初期化, スケジューラ起動
├── config.py
├── extensions.py        # db, scheduler インスタンス
├── models/              # SQLAlchemy モデル
├── routes/              # Flask Blueprint (dashboard, transactions, gmail, paypay, categories)
├── services/            # ビジネスロジック (gmail_service, email_parser, paypay_parser)
├── templates/           # Jinja2 テンプレート
└── static/
```

## DBマイグレーション方針

Alembicは使用していない。カラム追加は `app/__init__.py` の `_migrate_db()` に `ALTER TABLE` を追記する（try/exceptで既存カラムを無視）。

```python
migrations = [
    "ALTER TABLE transactions ADD COLUMN new_column VARCHAR(200)",
]
```

`db.create_all()` は新テーブルの作成のみ。既存テーブルへのカラム追加には必ず `_migrate_db()` を使う。

## 重要な設計パターン

- **Blueprint**: 各機能はBlueprintで分離（`url_prefix` あり）
- **Gmailスキャン非同期処理**: `threading.Thread` + モジュールレベルの `_scan_progress` dict でスレッドセーフな進捗管理
- **スキャン進捗ポーリング**: フロントエンドが `/gmail/scan/progress` を1秒ごとにfetch
- **フラッシュメッセージの制限**: バックグラウンドスレッドからはFlaskの `flash()` が使えないため、URLクエリパラメータで結果を渡しJSで表示

## セキュリティ指針（最優先）

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

## 不明点は必ず確認

実装方針・仕様・削除・破壊的変更など、少しでも判断に迷う場合はコードを書く前にユーザーに質問する。
