from flask import Blueprint, render_template, request, redirect, session, url_for, flash, current_app, send_from_directory
from flask_login import login_required, current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename
from models import Visit, db, Book, Genre, Cover, Review, login_manager
from werkzeug.datastructures import MultiDict
from datetime import date, datetime, timedelta
import os
import hashlib
import markdown
import bleach

main = Blueprint('main', __name__, template_folder='templates')

ALLOWED_EXT = {'png','jpg','jpeg','gif'}
BLEACH_TAGS = bleach.sanitizer.ALLOWED_TAGS.union({
    'p','pre','code','h1','h2','h3','ul','ol','li','blockquote','img'
})

BLEACH_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    'img': ['src','alt','title']
}

def file_allow(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXT

def render_md(md_text):
    html = markdown.markdown(md_text)
    return bleach.clean(html, tags=BLEACH_TAGS, attributes=BLEACH_ATTRS)

def check_role(*roles):
    from functools import wraps
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kw):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role.name not in roles:
                flash('У вас недостаточно прав для выполнения данного действия.', 'warning')
                return redirect(url_for('main.index'))
            return f(*args, **kw)
        return wrapper
    return deco

def visits_cnt(book_id, session_id, user_id, max_per_day=10):
    today = date.today()
    visits_today_q = Visit.query.filter_by(
        book_id=book_id,
        session_id=session_id
    ).filter(func.date(Visit.timestamp) == today)
    print('a')
    print(user_id)
    if user_id:
        visits_today_q = visits_today_q.filter(Visit.user_id == user_id)

    if visits_today_q.count() < max_per_day:
        visit = Visit(book_id=book_id, session_id=session_id,user_id=user_id)
        db.session.add(visit)
        db.session.commit()

def cover_save(file, book):
    data = file.read()
    checksum = hashlib.md5(data).hexdigest()
    existing = Cover.query.filter_by(md5_hash=checksum).first()
    if existing:
        existing.book_id = book.id
    else:
        ext = secure_filename(file.filename).rsplit('.', 1)[1]
        cover = Cover(filename='', mime_type=file.mimetype,
                      md5_hash=checksum, book_id=book.id)
        db.session.add(cover)
        db.session.flush()
        fname = f"{cover.id}.{ext}"
        path = os.path.join(current_app.config['UPLOAD_FOLDER'], fname)
        with open(path, 'wb') as f:
            f.write(data)
        cover.filename = fname

@main.route('/', methods=['GET'])
@main.route('/page/<int:page>', methods=['GET'])
def index(page=1):
    search_term = request.args.get('q', '').strip()

    books_q = Book.query
    if search_term:
        books_q = books_q.filter(Book.title.ilike(f'%{search_term}%'))

    paginated = books_q \
        .order_by(Book.year.desc()) \
        .paginate(page=page, per_page=10, error_out=False)

    three_months_ago = datetime.now() - timedelta(days=90)
    popular = (
        db.session.query(Book, func.count(Visit.id).label('views'))
        .join(Visit)
        .filter(Visit.timestamp >= three_months_ago)
        .group_by(Book.id)
        .order_by(func.count(Visit.id).desc())
        .limit(5)
        .all()
    )

    visitor_id = session.get('visitor_id')
    user_id    = current_user.get_id()
    recent_q = Visit.query.filter(Visit.session_id == visitor_id)
    if user_id:
        recent_q = recent_q.filter(Visit.user_id == user_id)

    recent_records = recent_q \
        .order_by(Visit.timestamp.desc()) \
        .limit(20) \
        .all()

    seen_books = set()
    recent_books = []
    for rec in recent_records:
        if rec.book_id not in seen_books:
            recent_books.append(rec.book)
            seen_books.add(rec.book_id)
        if len(recent_books) >= 5:
            break

    return render_template('index.html', pagination=paginated, q=search_term, popular=popular, recent=recent_books)

@main.route('/books/<int:book_id>', methods=['GET'])
def book_detail(book_id):
    book = Book.query.get_or_404(book_id)

    visitor_sid = session['visitor_id']
    user_uid = current_user.get_id()
    visits_cnt(book.id, visitor_sid, user_uid)

    rendered_desc = render_md(book.description)
    user_review = None
    if current_user.is_authenticated:
        user_review = Review.query.filter_by(
            book_id=book.id,
            user_id=current_user.id
        ).first()

    return render_template('book_detail.html', book=book, book_html=rendered_desc, existing_review=user_review)

@main.route('/books/<int:book_id>/review', methods=['GET','POST'])
@login_required
def book_review(book_id):
    book = Book.query.get_or_404(book_id)
    if Review.query.filter_by(book_id=book.id, user_id=current_user.id).first():
        return redirect(url_for('main.book_detail', book_id=book.id))
    if request.method == 'POST':
        try:
            rating = int(request.form['rating'])
            text   = request.form['text']
            review = Review(
                book_id=book.id,
                user_id=current_user.id,
                rating=rating,
                text=text
            )
            db.session.add(review)
            db.session.commit()
            flash('Рецензия успешно сохранена.', 'success')
            return redirect(url_for('main.book_detail', book_id=book.id))
        except Exception:
            db.session.rollback()
            flash('При сохранении рецензии возникла ошибка.', 'danger')
    return render_template('review_form.html', book=book)

@main.route('/books/create', methods=['GET','POST'])
@main.route('/books/<int:book_id>/edit', methods=['GET','POST'])
def upsert_book(book_id=None):
    is_edit = book_id is not None

    if not current_user.is_authenticated:
        flash('Для этого действия нужно войти', 'warning')
        return redirect(url_for('auth.login', next=request.path))

    if is_edit:
        if current_user.role.name not in ('admin', 'moderator'):
            flash('Недостаточно прав для редактирования', 'danger')
            return redirect(url_for('main.index'))
        book = Book.query.get_or_404(book_id)
    else:
        if current_user.role.name != 'admin':
            flash('Недостаточно прав для создания', 'danger')
            return redirect(url_for('main.index'))
        book = None

    all_genres = Genre.query.order_by(Genre.name).all()
    errors = {}
    form = request.form if request.method=='POST' else MultiDict() 

    if request.method == 'POST':
        keys = ['title','description','year','publisher','author','pages']
        if any(not request.form.get(k) for k in keys):
            flash('Заполните все обязательные поля', 'danger')
            return render_template(
                'form_books.html',
                action=('edit' if is_edit else 'create'),
                genres=all_genres, book=book, form=request.form, errors=errors
            )

        try:
            if not is_edit:
                book = Book()
                db.session.add(book)

            book.title       = form['title']
            book.description = form['description']
            book.year        = int(form['year'])
            book.publisher   = form['publisher']
            book.author      = form['author']
            book.pages       = int(form['pages'])

            book.genres[:] = []  
            for gid in form.getlist('genres'):
                genre = Genre.query.get(int(gid))
                if genre:
                    book.genres.append(genre)

            db.session.flush()
            file = request.files.get('cover')
            if file and file_allow(file.filename):
                cover_save(file, book)

            db.session.commit()
            flash(
                f'Книга успешно {"обновлена" if is_edit else "добавлена"}.',
                'success'
            )
            return redirect(url_for('main.book_detail', book_id=book.id))

        except Exception:
            db.session.rollback()
            flash(
                f'Ошибка при {"обновлении" if is_edit else "сохранении"} книги.',
                'danger'
            )
            return render_template('form_books.html', action=('edit' if is_edit else 'create'), genres=all_genres, book=book, form=request.form, errors=errors)

    return render_template('form_books.html',
        action = 'edit' if book_id else 'create',
        genres = Genre.query.all(),
        form   = form,
        errors = errors,
        book   = Book.query.get(book_id) if book_id else None
    )

@main.route('/books/<int:book_id>/delete', methods=['POST'])
@check_role('admin')
def book_delete(book_id):
    book = Book.query.get_or_404(book_id)
    cover_path = None
    if book.cover:
        cover_path = os.path.join(current_app.config['UPLOAD_FOLDER'], book.cover.filename)

    try:
        db.session.delete(book)
        db.session.commit()
        if cover_path and os.path.exists(cover_path):
            os.remove(cover_path)
        flash('Книга успешно удалена.', 'success')
    except Exception:
        db.session.rollback()
        flash('При удалении книги возникла ошибка.', 'danger')

    return redirect(url_for('main.index'))

@main.route('/covers/<filename>')
def covers(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
