from flask import Blueprint, render_template, jsonify, request
from sqlalchemy import func, extract
from app.extensions import db
from app.models import Transaction, Category
from datetime import date, datetime

bp = Blueprint("dashboard", __name__)


@bp.route("/api/transactions")
def transactions_json():
    year = request.args.get("year", date.today().year, type=int)
    month = request.args.get("month", date.today().month, type=int)
    limit = request.args.get("limit", 20, type=int)

    txs = Transaction.query.filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == month,
    ).order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit).all()

    return jsonify([t.to_dict() for t in txs])


@bp.route("/")
def index():
    today = date.today()
    return render_template("dashboard/index.html", today=today)


@bp.route("/api/summary")
def summary():
    year = request.args.get("year", date.today().year, type=int)
    month = request.args.get("month", date.today().month, type=int)

    base = db.session.query(Transaction).filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == month,
    )

    income = base.filter(Transaction.type == "income").with_entities(
        func.coalesce(func.sum(Transaction.amount), 0)
    ).scalar()

    expense = base.filter(Transaction.type == "expense").with_entities(
        func.coalesce(func.sum(Transaction.amount), 0)
    ).scalar()

    return jsonify({
        "year": year,
        "month": month,
        "income": income,
        "expense": expense,
        "balance": income - expense,
    })


@bp.route("/api/monthly_trend")
def monthly_trend():
    year = request.args.get("year", date.today().year, type=int)

    rows = db.session.query(
        extract("month", Transaction.date).label("month"),
        Transaction.type,
        func.sum(Transaction.amount).label("total"),
    ).filter(
        extract("year", Transaction.date) == year,
    ).group_by("month", Transaction.type).all()

    result = {m: {"income": 0, "expense": 0} for m in range(1, 13)}
    for row in rows:
        result[int(row.month)][row.type] = int(row.total)

    return jsonify([
        {"month": m, **result[m]} for m in range(1, 13)
    ])


@bp.route("/api/category_breakdown")
def category_breakdown():
    year = request.args.get("year", date.today().year, type=int)
    month = request.args.get("month", date.today().month, type=int)
    tx_type = request.args.get("type", "expense")

    rows = db.session.query(
        Category.name,
        Category.color,
        func.sum(Transaction.amount).label("total"),
    ).join(Transaction, Transaction.category_id == Category.id).filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == month,
        Transaction.type == tx_type,
    ).group_by(Category.id).all()

    uncategorized = db.session.query(
        func.sum(Transaction.amount)
    ).filter(
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == month,
        Transaction.type == tx_type,
        Transaction.category_id.is_(None),
    ).scalar() or 0

    data = [{"name": r.name, "color": r.color, "total": int(r.total)} for r in rows]
    if uncategorized > 0:
        data.append({"name": "未分類", "color": "#adb5bd", "total": int(uncategorized)})

    return jsonify(data)
