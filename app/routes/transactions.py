from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app.extensions import db
from app.models import Transaction, Category
from datetime import date, datetime

bp = Blueprint("transactions", __name__, url_prefix="/transactions")


@bp.route("/")
def index():
    year = request.args.get("year", date.today().year, type=int)
    month = request.args.get("month", date.today().month, type=int)
    category_id = request.args.get("category_id", type=int)
    tx_type = request.args.get("type", "")
    source = request.args.get("source", "")

    query = Transaction.query.filter(
        db.extract("year", Transaction.date) == year,
        db.extract("month", Transaction.date) == month,
    )
    if category_id:
        query = query.filter(Transaction.category_id == category_id)
    if tx_type in ("income", "expense"):
        query = query.filter(Transaction.type == tx_type)
    if source in ("manual", "gmail", "paypay"):
        query = query.filter(Transaction.source == source)

    transactions = query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    categories = Category.query.order_by(Category.name).all()

    return render_template(
        "transactions/index.html",
        transactions=transactions,
        categories=categories,
        year=year,
        month=month,
        selected_category=category_id,
        selected_type=tx_type,
        selected_source=source,
    )


@bp.route("/new", methods=["GET", "POST"])
def new():
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        tx = Transaction(
            date=datetime.strptime(request.form["date"], "%Y-%m-%d").date(),
            amount=int(request.form["amount"]),
            type=request.form["type"],
            description=request.form["description"],
            memo=request.form.get("memo"),
            category_id=request.form.get("category_id") or None,
            source="manual",
        )
        db.session.add(tx)
        db.session.commit()
        flash("追加しました", "success")
        return redirect(url_for("transactions.index",
                                year=tx.date.year, month=tx.date.month))

    return render_template("transactions/form.html",
                           categories=categories, tx=None)


@bp.route("/<int:tx_id>/edit", methods=["GET", "POST"])
def edit(tx_id):
    tx = Transaction.query.get_or_404(tx_id)
    categories = Category.query.order_by(Category.name).all()
    if request.method == "POST":
        tx.date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        tx.amount = int(request.form["amount"])
        tx.type = request.form["type"]
        tx.description = request.form["description"]
        tx.memo = request.form.get("memo")
        tx.category_id = request.form.get("category_id") or None
        db.session.commit()
        flash("更新しました", "success")
        return redirect(url_for("transactions.index",
                                year=tx.date.year, month=tx.date.month))

    return render_template("transactions/form.html",
                           categories=categories, tx=tx)


@bp.route("/<int:tx_id>/delete", methods=["POST"])
def delete(tx_id):
    tx = Transaction.query.get_or_404(tx_id)
    year, month = tx.date.year, tx.date.month
    db.session.delete(tx)
    db.session.commit()
    flash("削除しました", "success")
    return redirect(url_for("transactions.index", year=year, month=month))


def _build_bulk_query(form):
    """一括削除用のフィルタークエリを構築する"""
    query = Transaction.query
    date_from = form.get("date_from")
    date_to = form.get("date_to")
    category_id = form.get("bulk_category_id")
    source = form.get("bulk_source")

    if date_from:
        query = query.filter(Transaction.date >= date_from)
    if date_to:
        query = query.filter(Transaction.date <= date_to)
    if category_id == "none":
        query = query.filter(Transaction.category_id.is_(None))
    elif category_id:
        query = query.filter(Transaction.category_id == int(category_id))
    if source in ("manual", "gmail", "paypay"):
        query = query.filter(Transaction.source == source)

    return query


@bp.route("/bulk_delete_count")
def bulk_delete_count():
    count = _build_bulk_query(request.args).count()
    return jsonify({"count": count})


@bp.route("/bulk_delete", methods=["POST"])
def bulk_delete():
    query = _build_bulk_query(request.form)
    count = query.count()
    if count == 0:
        flash("対象の取引がありません", "warning")
        return redirect(url_for("transactions.index"))
    query.delete(synchronize_session=False)
    db.session.commit()
    flash(f"{count}件の取引を削除しました", "success")
    return redirect(url_for("transactions.index"))
