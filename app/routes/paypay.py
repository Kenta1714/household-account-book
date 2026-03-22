from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app.models import ScanLog
from app.services.paypay_parser import import_csv

bp = Blueprint("paypay", __name__, url_prefix="/paypay")


@bp.route("/")
def index():
    logs = ScanLog.query.filter_by(source="paypay").order_by(ScanLog.scanned_at.desc()).limit(10).all()
    return render_template("paypay/index.html", logs=logs)


@bp.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("csv_file")
    if not file or not file.filename.endswith(".csv"):
        flash("CSVファイルを選択してください", "danger")
        return redirect(url_for("paypay.index"))

    import_investment = request.form.get("import_investment") == "1"
    content = file.read()

    try:
        result = import_csv(content, import_investment=import_investment)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
