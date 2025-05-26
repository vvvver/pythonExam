import csv
from io import StringIO
from datetime import datetime, timedelta
import io
from flask import Blueprint, render_template, request, redirect, send_file, url_for, flash
from flask_login import current_user
from models import db, Visit, Book, User
from sqlalchemy import func

stats_bp = Blueprint('stats', __name__, template_folder='templates', url_prefix='/stats')

def admin_allowed(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user.is_authenticated or current_user.role.name!='admin':
            flash('Недостаточно прав', 'warning')
            return redirect(url_for('main.index'))
        return f(*a, **kw)
    return wrapper

@stats_bp.route('/', methods=['GET'])
@stats_bp.route('/logs')
@admin_allowed
def stats_actions():
    page = request.args.get('page', 1, type=int)
    pagination = (Visit.query
        .order_by(Visit.timestamp.desc())
        .paginate(page=page, per_page=10, error_out=False)
    )
    return render_template('stats_actions.html', pagination=pagination)

@stats_bp.route('/logs/export')
@admin_allowed
def stats_actions_export():
    visits = Visit.query.order_by(Visit.timestamp.desc()).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['№', 'Пользователь', 'Книга', 'Дата/Время'])
    for i, v in enumerate(visits, 1):
        user = f"{v.user.last_name} {v.user.first_name}" if v.user else "Неаутентифицированный"
        writer.writerow([i, user, v.book.title, v.timestamp])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.read().encode('utf-8-sig')),
        as_attachment=True,
        download_name=f"user_log_{datetime.utcnow().date()}.csv",
        mimetype='text/csv'
    )

@stats_bp.route('/views', methods=['GET', 'POST'])
@admin_allowed
def stats_views():
    date_from = request.values.get('date_from')
    date_to   = request.values.get('date_to')
    page      = request.args.get('page', 1, type=int)

    q = (
        db.session
        .query(
            Book.id.label('id'),
            Book.title.label('title'),
            func.count(Visit.id).label('cnt')
        )
        .join(Visit)
        .join(User)
        .filter(User.id.isnot(None))
    )
    if date_from:
        q = q.filter(Visit.timestamp >= date_from)
    if date_to:
        q = q.filter(Visit.timestamp < datetime.fromisoformat(date_to) + timedelta(days=1))

    pagination = (
        q.group_by(Book.id, Book.title)
         .order_by(func.count(Visit.id).desc())
         .paginate(page=page, per_page=10, error_out=False)
    )

    return render_template(
        'stats_views.html',
        pagination=pagination,
        date_from=date_from,
        date_to=date_to
    )


@stats_bp.route('/views/export')
@admin_allowed
def stats_views_export():
    date_from = request.values.get('date_from')
    date_to   = request.values.get('date_to')

    q = (
        db.session
        .query(
            Book.id.label('id'),
            Book.title.label('title'),
            func.count(Visit.id).label('cnt')
        )
        .join(Visit)
        .join(User)
        .filter(User.id.isnot(None))
    )
    if date_from:
        q = q.filter(Visit.timestamp >= date_from)
    if date_to:
        q = q.filter(Visit.timestamp < datetime.fromisoformat(date_to) + timedelta(days=1))

    rows = (
        q.group_by(Book.id, Book.title)
         .order_by(func.count(Visit.id).desc())
         .all()
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['№', 'Книга', 'Просмотров'])
    for i, (_, title, cnt) in enumerate(rows, 1):
        writer.writerow([i, title, cnt])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.read().encode('utf-8-sig')),
        as_attachment=True,
        download_name=f"visits_actions_{datetime.utcnow().date()}.csv",
        mimetype='text/csv'
    )
