from flask import Blueprint, render_template, request, redirect, url_for, flash
from app.extensions import db
from app.models import Category
from app.validators import validate_category_form

bp = Blueprint("categories", __name__, url_prefix="/categories")

DEFAULT_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
    "#9c755f", "#bab0ac",
]


@bp.route("/")
def index():
    return _render_index()


def _render_index(status=200, **context):
    categories = Category.query.order_by(Category.type, Category.name).all()
    return render_template(
        "categories/index.html",
        categories=categories,
        colors=DEFAULT_COLORS,
        **context,
    ), status


@bp.route("/new", methods=["POST"])
def new():
    data, errors = validate_category_form(request.form)
    if errors:
        return _render_index(
            400,
            category_form=request.form,
            category_errors=errors,
            open_category_modal="newCatModal",
        )

    cat = Category(**data)
    db.session.add(cat)
    db.session.commit()
    flash("カテゴリを追加しました", "success")
    return redirect(url_for("categories.index"))


@bp.route("/<int:cat_id>/edit", methods=["POST"])
def edit(cat_id):
    cat = Category.query.get_or_404(cat_id)
    data, errors = validate_category_form(request.form, default_color=cat.color)
    if errors:
        return _render_index(
            400,
            category_form=request.form,
            category_errors=errors,
            open_category_modal="editCatModal",
            edit_category_id=cat.id,
        )

    for field, value in data.items():
        setattr(cat, field, value)
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
