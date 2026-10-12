import hmac
import click
from flask import Flask, request, Response, jsonify, render_template
from flask_wtf.csrf import CSRFError
from app.config import configure_app
from app.extensions import csrf, db, scheduler
from werkzeug.serving import is_running_from_reloader


def _apply_basic_auth(app):
    """起動時に検証済みの認証情報を両環境で必須にする。"""
    username = app.config["APP_USERNAME"]
    password = app.config["APP_PASSWORD"]

    @app.before_request
    def require_auth():
        if request.path in ("/ping", "/favicon.ico") or \
                request.path.startswith("/static/apple-touch-icon") or \
                request.path.startswith("/static/icon-"):
            return  # スリープ対策・アイコン類は認証不要
        auth = request.authorization
        valid_username = hmac.compare_digest((auth.username or "").encode(), username.encode()) if auth else False
        valid_password = hmac.compare_digest((auth.password or "").encode(), password.encode()) if auth else False
        if not valid_username or not valid_password:
            return Response(
                "このアプリへのアクセスには認証が必要です",
                401,
                {"WWW-Authenticate": 'Basic realm="kakeibo"'},
            )


def create_app(test_config=None):
    app = Flask(__name__)
    configure_app(app)
    if test_config is not None:
        app.config.update(test_config)
    _apply_basic_auth(app)
    csrf.init_app(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        message = "操作の確認に失敗しました。画面を再読み込みして、もう一度操作してください。"
        if request.is_json or request.accept_mimetypes.best == "application/json":
            return jsonify({"error": message}), 400
        return render_template("csrf_error.html", message=message), 400

    from app.commands import register_commands
    register_commands(app)

    db.init_app(app)

    from app.routes import dashboard, transactions, categories, gmail, paypay
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(transactions.bp)
    app.register_blueprint(categories.bp)
    app.register_blueprint(gmail.bp)
    app.register_blueprint(paypay.bp)

    @app.route("/ping")
    def ping():
        return "ok", 200

    @app.route("/favicon.ico")
    def favicon():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "apple-touch-icon.png",
                                   mimetype="image/png")

    # Gmail定期スキャン（間隔はDBのscan_interval_minutes設定値、デフォルト30分）
    # Flaskのdebugリローダーが起動する子プロセスではスケジューラを起動しない
    # shell・トークン移行などの保守コマンドではバックグラウンド取込を起動しない。
    cli_context = click.get_current_context(silent=True)
    serving = cli_context is None or cli_context.find_root().invoked_subcommand == "run"
    if (
        app.config["SCHEDULER_ENABLED"]
        and serving
        and not scheduler.running
        and (not app.debug or is_running_from_reloader())
    ):
        from app.services import gmail_service as gs

        def scheduled_gmail_scan():
            with app.app_context():
                if gs.is_authenticated():
                    gs.scan_emails(batch=False, trigger="auto")

        with app.app_context():
            from app.models import Setting
            s = db.session.get(Setting, "scan_interval_minutes")
            interval_minutes = int(s.value) if s else 30

        scheduler.add_job(scheduled_gmail_scan, "interval", minutes=interval_minutes, id="gmail_scan")
        scheduler.start()

    return app
