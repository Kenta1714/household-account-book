from flask import Blueprint, render_template, request, redirect, url_for, flash
from app.extensions import db
from app.models import Category

bp = Blueprint("categories", __name__, url_prefix="/categories")

DEFAULT_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
    "#9c755f", "#bab0ac",
]


@bp.route("/")
def index():
    categories = Category.query.order_by(Category.type, Category.name).all()
    return render_template("categories/index.html",
                           categories=categories, colors=DEFAULT_COLORS)


@bp.route("/new", methods=["POST"])
def new():
    cat = Category(
        name=request.form["name"],
        type=request.form["type"],
        color=request.form.get("color", "#6c757d"),
    )
    db.session.add(cat)
    db.session.commit()
    flash("カテゴリを追加しました", "success")
    return redirect(url_for("categories.index"))


@bp.route("/<int:cat_id>/edit", methods=["POST"])
def edit(cat_id):
    cat = Category.query.get_or_404(cat_id)
    cat.name = request.form["name"]
    cat.type = request.form["type"]
    cat.color = request.form.get("color", cat.color)
    db.session.commit()
    flash("更新しました", "success")
    return redirect(url_for("categories.index"))


@bp.route("/<int:cat_id>/delete", methods=["POST"])
def delete(cat_id):
    cat = Category.query.get_or_404(cat_id)
    db.session.delete(cat)
    db.session.commit()
    flash("削除しました", "success")
    return redirect(url_for("categories.index"))
