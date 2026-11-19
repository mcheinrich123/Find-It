import json
import os
import uuid

from datetime import date, datetime, timezone, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

import nlp  # NER and TF-IDF matching (nlp.py)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-me')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# On the public HTTPS site set SESSION_COOKIE_SECURE=1 (see DEPLOY.md), so the login cookie is
# never sent over plain HTTP. Leave it unset locally: http://localhost would then lose logins.
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE') == '1'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # reject request bodies over 10 MB

UPLOAD_DIR = os.path.join(app.root_path, 'static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
PROOF_EXTENSIONS = IMAGE_EXTENSIONS | {'pdf'}

db = SQLAlchemy(app)

# One list for the whole site. Keep these in sync with js/item-store.js.
CATEGORIES = ["Electronics", "Bags", "Clothing", "ID & Docs", "Accessories", "Others"]
ITEM_STATUSES = ["pending", "matched", "ready", "claimed"]


def utcnow():
    """Naive UTC time (SQLite does not store time zones)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    school_id = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')  # 'user' | 'admin'
    created_at = db.Column(db.DateTime, default=utcnow)
    phone = db.Column(db.String(30))
    photo = db.Column(db.Text)

    items = db.relationship('Item', backref='reporter', lazy=True)

    def __repr__(self):
        return f"<User {self.email}>"


class Item(db.Model):
    """Both lost reports and found items live here; report_type tells them apart."""
    __tablename__ = 'items'
    id = db.Column(db.Integer, primary_key=True)
    reference_code = db.Column(db.String(20), unique=True)        
    report_type = db.Column(db.String(10), nullable=False)        
    item_name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    color = db.Column(db.String(50))
    description = db.Column(db.Text)
    location = db.Column(db.String(150), nullable=False)            # a location id from js/data/locations.js
    location_other = db.Column(db.String(150))                      # what was typed for "Other / Not sure"
    location_details = db.Column(db.String(500))
    event_date = db.Column(db.Date, nullable=False)                 
    image = db.Column(db.String(255))                              

    # PRIVATE: only SWAFO (and the owner proving a claim) should ever see these.
    brand = db.Column(db.String(100))
    identifying_details = db.Column(db.Text)
    storage = db.Column(db.String(100))
    turned_in_by = db.Column(db.String(100))

    item_now = db.Column(db.String(20))                           
    contact_phone = db.Column(db.String(30))
    status = db.Column(db.String(20), nullable=False, default='pending')
    added_by_admin = db.Column(db.Boolean, default=False)
    date_reported = db.Column(db.DateTime, default=utcnow)
    reporter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    def to_public(self):
        """Safe for anyone. Never add brand, identifying details, storage or turned-in-by here."""
        iso = self.event_date.isoformat() if self.event_date else None
        return {
            "id": self.reference_code,
            "type": self.report_type,
            "name": self.item_name,
            "category": self.category,
            "color": self.color or "",
            "description": self.description or "",
            "location": self.location,             # same as locationId; kept for older callers
            "locationId": self.location,
            "locationOther": self.location_other or "",
            "locationDetails": self.location_details or "",
            "dateFound": iso if self.report_type == 'found' else None,
            "dateLost": iso if self.report_type == 'lost' else None,
            "dateReported": self.date_reported.isoformat() + "Z" if self.date_reported else None,
            "status": self.status,
            "image": self.image,
        }

    def nlp_record(self):
        """The fields nlp.py reads, private ones included: matches are only ever shown to SWAFO."""
        return {
            "id": self.reference_code,
            "type": self.report_type,
            "status": self.status,
            "name": self.item_name,
            "category": self.category,
            "color": self.color or "",
            "description": self.description or "",
            "brand": self.brand or "",
            "identifying_details": self.identifying_details or "",
            "location": self.location,
            "location_other": self.location_other or "",
            "location_details": self.location_details or "",
            "event_date": self.event_date,
        }

    def entities(self):
        """NER over everything written about the item. Admin only: the brand and details are private."""
        return nlp.entity_summary(self.item_name, self.color, self.description, self.location_other,
                                  self.location_details, self.brand, self.identifying_details)

    def to_private(self):
        """Admin only."""
        data = self.to_public()
        data.update({
            "brand": self.brand or "",
            "identifyingDetails": self.identifying_details or "",
            "storage": self.storage or "",
            "turnedInBy": self.turned_in_by or "",
            "itemNow": self.item_now,
            "addedByAdmin": bool(self.added_by_admin),
            "reporter": {
                "id": self.reporter.id,
                "name": self.reporter.full_name,
                "email": self.reporter.email,
                "schoolId": self.reporter.school_id,
                "phone": self.contact_phone or "",
            },
        })
        return data

class Claim(db.Model):
    __tablename__ = 'claims'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    claimant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    brand = db.Column(db.String(100))
    identifiers = db.Column(db.Text, nullable=False)
    lost_location = db.Column(db.String(150))
    lost_date = db.Column(db.Date)
    proof_files = db.Column(db.Text, default='[]')                  # JSON list of upload paths
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending|approved|rejected|released
    admin_note = db.Column(db.Text)
    date_submitted = db.Column(db.DateTime, default=utcnow)
    reviewed_at = db.Column(db.DateTime)

    item = db.relationship('Item', backref='claims')
    claimant = db.relationship('User', backref='claims')

    @property
    def reference(self):
        return f"CL-{self.id:04d}"

    def to_dict(self, admin=False):
        data = {
            "id": self.id,
            "reference": self.reference,
            "itemRef": self.item.reference_code,
            "itemName": self.item.item_name,
            "image": self.item.image,
            "status": self.status,
            "dateSubmitted": self.date_submitted.isoformat() + "Z",
            "reviewedAt": self.reviewed_at.isoformat() + "Z" if self.reviewed_at else None,
            "adminNote": self.admin_note or "",
        }
        if admin:
            item_brand = (self.item.brand or "").strip().lower()
            data.update({
                "brand": self.brand or "",
                "identifiers": self.identifiers,
                "lostLocation": self.lost_location or "",
                "lostDate": self.lost_date.isoformat() if self.lost_date else None,
                "proofFiles": json.loads(self.proof_files or '[]'),
                "brandMatches": bool(item_brand and item_brand == (self.brand or "").strip().lower()),
                "claimant": {
                    "name": self.claimant.full_name,
                    "email": self.claimant.email,
                    "schoolId": self.claimant.school_id,
                    "phone": self.claimant.phone or "",
                },
                # With its private fields, so SWAFO can check the claimant's answers against them.
                "item": self.item.to_private(),
            })
        return data


class Message(db.Model):
    """One conversation per student; every row belongs to that student's thread with SWAFO."""
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    sender = db.Column(db.String(10), nullable=False)               # 'user' | 'swafo' | 'system'
    text = db.Column(db.Text)
    image = db.Column(db.Text)                                      # small data: URL from photos.js
    time = db.Column(db.DateTime, default=utcnow)
    read_by_user = db.Column(db.Boolean, default=False)
    read_by_swafo = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "sender": self.sender,
            "text": self.text or "",
            "image": self.image,
            "time": self.time.isoformat() + "Z",
        }


def current_user():
    """The logged-in User row, or None. Reads the role from the database, not the cookie."""
    data = session.get('user')
    return db.session.get(User, data['id']) if data else None


def api_error(code, status=400):
    return jsonify({"error": code}), status


def clean(value, limit):
    return (value or "").strip()[:limit]


def parse_date(text):
    """'2026-10-16' -> date, or None if empty. Raises ValueError if invalid."""
    text = (text or "").strip()
    return date.fromisoformat(text) if text else None


def save_upload(file_storage, allowed):
    """Saves an uploaded file under a random name; returns its URL path or None if no file."""
    if not file_storage or not file_storage.filename:
        return None
    name = file_storage.filename
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in allowed:
        raise ValueError('bad_file_type')
    saved_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, saved_name))
    return f"/static/uploads/{saved_name}"


def remove_upload(url_path):
    """Deletes a file save_upload() stored, given its URL path. Ignores anything else."""
    prefix = '/static/uploads/'
    if not url_path or not url_path.startswith(prefix):
        return
    path = os.path.join(UPLOAD_DIR, os.path.basename(url_path))
    if os.path.isfile(path):
        os.remove(path)


def login_required(f):
    """For pages: send visitors who are not logged in to the login page."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """For pages: admins only."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None or user.role != 'admin':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def api_login_required(f):
    """For API routes: answers 401 JSON instead of redirecting."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return api_error('login_required', 401)
        return f(*args, **kwargs)
    return wrapper


def api_admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return api_error('login_required', 401)
        if user.role != 'admin':
            return api_error('forbidden', 403)
        return f(*args, **kwargs)
    return wrapper


@app.errorhandler(413)
def file_too_large(_error):
    return api_error('file_too_large', 413)

def build_matcher():
    """A TF-IDF matcher over every report (nlp.py). Built per request, so it's never stale."""
    return nlp.Matcher([item.nlp_record() for item in Item.query.all()])

# --------------------- Pages Section ------------------------------#

@app.route('/')
def index(): return render_template('index.html')

@app.route('/browse')
def browse_items(): return render_template('items.html')

@app.route('/item-details')
def item_details(): return render_template('item-details.html')

@app.route('/claim')
def claim_item(): return render_template('claim.html')

@app.route('/report')
def report(): return render_template('report.html')

@app.route('/report-form')
def report_form(): return render_template('report-form.html')

@app.route('/report-submitted')
def report_submitted(): return render_template('report-submitted.html')

@app.route('/my-reports')
@login_required
def my_reports():
    user = current_user()
    if user.role == 'admin':
        return redirect('/admin/dashboard')
    return render_template('my-reports.html')

@app.route('/messages')
@login_required
def messages(): return render_template('messages.html')

@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html')

@app.route('/about')
def about(): return render_template('about.html')

@app.route('/signup')
def signup(): return render_template('signup.html')

@app.route('/login')
def login():
    user = current_user()
    if user is not None:
        return redirect('/admin/dashboard' if user.role == 'admin' else '/')
    return render_template('login.html')

# Admin pages
@app.route('/admin')
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard(): return render_template('admin/dashboard.html')

@app.route('/admin/claims')
@admin_required
def admin_claims():
    return render_template('admin/claims.html')

@app.route('/admin/reports')
@admin_required
def admin_reports(): return render_template('admin/reports.html')

@app.route('/admin/found-items')
@admin_required
def admin_found_items(): return render_template('admin/found-items.html')

@app.route('/admin/messages')
@admin_required
def admin_admin_messages(): return render_template('admin/messages.html')

@app.route('/admin/analytics')
@admin_required
def admin_analytics(): return render_template('admin/analytics.html')

@app.route('/admin/settings')
@admin_required
def admin_settings(): return render_template('admin/settings.html')


# Old "items.html"-style links redirect to the real route. Remove once no .html links are left.
LEGACY_PAGES = {
    'index.html': '/', 'items.html': '/browse', 'item-details.html': '/item-details',
    'claim.html': '/claim', 'report.html': '/report', 'report-form.html': '/report-form',
    'report-submitted.html': '/report-submitted', 'my-reports.html': '/my-reports',
    'messages.html': '/messages', 'about.html': '/about', 'login.html': '/login',
    'signup.html': '/signup','settings.html': '/settings',
}

@app.route('/<page>.html')
def legacy_html(page):
    target = LEGACY_PAGES.get(f'{page}.html')
    if not target:
        return 'Not found', 404
    query = request.query_string.decode()
    return redirect(f'{target}?{query}' if query else target)


ADMIN_LEGACY_PAGES = {
    'dashboard.html': '/admin/dashboard', 'claims.html': '/admin/claims',
    'reports.html': '/admin/reports', 'found-items.html': '/admin/found-items',
    'messages.html': '/admin/messages', 'analytics.html': '/admin/analytics',
    'settings.html': '/admin/settings',
}

@app.route('/admin/<page>.html')
def legacy_admin_html(page):
    target = ADMIN_LEGACY_PAGES.get(f'{page}.html')
    if not target:
        return 'Not found', 404
    query = request.query_string.decode()
    return redirect(f'{target}?{query}' if query else target)


# ==========================================
# Auth API
# ==========================================

@app.route('/api/auth/signup', methods=['POST'])
def api_signup():
    data = request.get_json(silent=True) or {}
    full_name = clean(data.get('fullName'), 100)
    school_id = clean(data.get('schoolId'), 50)
    email = clean(data.get('email'), 120).lower()
    password = data.get('password') or ''

    if not all([full_name, school_id, email, password]):
        return api_error('missing_fields')
    if len(password) < 8:
        return api_error('weak_password')

    existing = User.query.filter((User.email == email) | (User.school_id == school_id)).first()
    if existing:
        return api_error('user_exists')

    user = User(
        full_name=full_name,
        school_id=school_id,
        email=email,
        password_hash=generate_password_hash(password, method='scrypt'),
        role='user',
    )
    try:
        db.session.add(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return api_error('database_error', 500)
    return jsonify({"success": True, "message": "Account created successfully!"}), 201


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    email = clean(data.get('email'), 120).lower()
    password = data.get('password') or ''
    role = data.get('role')

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return api_error('credentials', 401)
    if role and user.role != role:
        return api_error('role', 401)

    session['user'] = {'id': user.id, 'name': user.full_name, 'email': user.email, 'role': user.role}
    return jsonify({"session": session['user']})

def user_payload(user):
    return {'id': user.id, 'name': user.full_name, 'email': user.email,
            'role': user.role, 'phone': user.phone or '', 'photo': user.photo}


@app.route('/api/auth/me', methods=['GET'])
def api_current_user():
    user = current_user()
    if user is None:
        return jsonify(None), 401
    return jsonify(user_payload(user))


@app.route('/api/auth/profile', methods=['PATCH'])
@api_login_required
def api_update_profile():
    user = current_user()
    data = request.get_json(silent=True) or {}

    if 'fullName' in data:
        name = clean(data['fullName'], 100)
        if not name:
            return api_error('missing_fields')
        user.full_name = name

    if 'phone' in data:
        user.phone = clean(data['phone'], 30)

    if 'photo' in data:
        photo = data['photo']
        if photo:  # new photo
            if (not isinstance(photo, str) or not photo.startswith('data:image/')
                    or len(photo) > 2_000_000):
                return api_error('bad_image')
            user.photo = photo
        else:      # empty or null = "Remove photo"
            user.photo = None

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return api_error('database_error', 500)

    session['user'] = {'id': user.id, 'name': user.full_name,
                       'email': user.email, 'role': user.role}
    return jsonify(user_payload(user))


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({"success": True})


@app.route('/api/auth/change-password', methods=['POST'])
@api_login_required
def api_change_password():
    user = current_user()
    data = request.get_json(silent=True) or {}
    current_password = data.get('currentPassword') or ''
    new_password = data.get('newPassword') or ''

    if not check_password_hash(user.password_hash, current_password):
        return api_error('wrong_password', 403)
    if len(new_password) < 8:
        return api_error('weak_password')
    if new_password == current_password:
        return api_error('same_password')

    user.password_hash = generate_password_hash(new_password, method='scrypt')
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return api_error('database_error', 500)
    return jsonify({"success": True})


# ==========================================
# 7. ITEMS API
# ==========================================


@app.route('/api/items', methods=['GET'])
def api_items():
    """Public Browse list: found items that are not claimed yet. Never includes private fields."""
    query = Item.query.filter(Item.report_type == 'found', Item.status != 'claimed')
    q = clean(request.args.get('q'), 100)
    if q:
        like = f'%{q}%'
        query = query.filter(
            Item.item_name.ilike(like) | Item.category.ilike(like) |
            Item.color.ilike(like) | Item.description.ilike(like) | Item.location.ilike(like)
        )
    items = query.order_by(Item.event_date.desc(), Item.id.desc()).all()
    return jsonify([i.to_public() for i in items])


@app.route('/api/items/<code>', methods=['GET'])
def api_item(code):
    item = Item.query.filter_by(reference_code=code).first()
    if item is None:
        return api_error('not_found', 404)

    user = current_user()
    if user is not None and user.role == 'admin':
        return jsonify(item.to_private())
    if user is not None and item.reporter_id == user.id:
        return jsonify(item.to_public())
    # Everyone else may only see found items that are still available.
    if item.report_type != 'found' or item.status == 'claimed':
        return api_error('not_found', 404)
    return jsonify(item.to_public())


@app.route('/api/items', methods=['POST'])
@api_login_required
def api_create_item():
    """The report form (multipart/form-data) and the admin 'Add found item' form both post here."""
    user = current_user()
    form = request.form

    report_type = form.get('type', '')
    if report_type not in ('lost', 'found'):
        return api_error('invalid_type')

    name = clean(form.get('itemName'), 100)
    category = form.get('category', '')
    description = clean(form.get('description'), 2000)
    location = clean(form.get('locationId'), 150)
    if not all([name, category, description, location, form.get('date')]):
        return api_error('missing_fields')
    if  category not in CATEGORIES:
        return api_error('invalid_category')

    try:
        event_date = parse_date(form.get('date'))
    except ValueError:
        return api_error('invalid_date')
    if event_date > date.today():
        return api_error('future_date')

    try:
        image = save_upload(request.files.get('photo'), IMAGE_EXTENSIONS)
    except ValueError:
        return api_error('bad_file_type')
    if report_type == 'found' and not image:
        return api_error('photo_required')

    item_now = form.get('itemNow') if form.get('itemNow') in ('swafo', 'with-me') else None
    is_admin = user.role == 'admin'

    item = Item(
        report_type=report_type,
        item_name=name,
        category=category,
        color=clean(form.get('color'), 50),
        description=description,
        location=location,
        # The typed place only means something for "Other / Not sure".
        location_other=clean(form.get('locationOther'), 150) if location == 'other' else None,
        location_details=clean(form.get('locationDetails'), 500),
        event_date=event_date,
        image=image,
        brand=clean(form.get('brand'), 100),
        identifying_details=clean(form.get('identifyingDetails'), 2000),
        item_now=item_now if report_type == 'found' else None,
        contact_phone=clean(form.get('phone'), 30),
        storage=clean(form.get('storage'), 100) if is_admin else None,
        turned_in_by=clean(form.get('turnedInBy'), 100) if is_admin else None,
        added_by_admin=is_admin,
        reporter_id=user.id,
    )
    try:
        db.session.add(item)
        db.session.flush()                       # assigns item.id
        item.reference_code = f"FI-{item.id:04d}"
        db.session.commit()
    except Exception:
        db.session.rollback()
        return api_error('database_error', 500)

    return jsonify({"success": True, "reference": item.reference_code}), 201


@app.route('/api/my-reports', methods=['GET'])
@api_login_required
def api_my_reports():
    user = current_user()
    items = Item.query.filter_by(reporter_id=user.id).order_by(Item.date_reported.desc()).all()
    claims = Claim.query.filter_by(claimant_id=user.id).order_by(Claim.date_submitted.desc()).all()
    return jsonify({
        "reports": [i.to_public() for i in items],
        "claims": [c.to_dict() for c in claims],
    })


@app.route('/api/my-reports/<code>', methods=['DELETE'])
@api_login_required
def api_cancel_report(code):
    """A student cancels their own report, only while it's still pending and nobody has claimed it."""
    user = current_user()
    item = Item.query.filter_by(reference_code=code, reporter_id=user.id).first()
    if item is None:
        return api_error('not_found', 404)
    if item.status != 'pending' or item.claims:
        return api_error('not_pending', 409)

    image = item.image
    try:
        db.session.delete(item)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return api_error('database_error', 500)
    remove_upload(image)
    return jsonify({"success": True})


# ==========================================
# 8. CLAIMS API
# ==========================================

@app.route('/api/claims', methods=['POST'])
@api_login_required
def api_create_claim():
    user = current_user()
    form = request.form

    item = Item.query.filter_by(reference_code=clean(form.get('itemId'), 20)).first()
    if item is None or item.report_type != 'found' or item.status == 'claimed':
        return api_error('item_unavailable', 404)

    identifiers = clean(form.get('identifiers'), 2000)
    if not identifiers or not form.get('certify'):
        return api_error('missing_fields')

    try:
        lost_date = parse_date(form.get('lostDate'))
    except ValueError:
        return api_error('invalid_date')

    duplicate = Claim.query.filter(
        Claim.item_id == item.id,
        Claim.claimant_id == user.id,
        Claim.status.in_(['pending', 'approved']),
    ).first()
    if duplicate:
        return api_error('duplicate_claim', 409)

    proofs = []
    try:
        for file in request.files.getlist('proof')[:5]:
            path = save_upload(file, PROOF_EXTENSIONS)
            if path:
                proofs.append(path)
    except ValueError:
        return api_error('bad_file_type')

    claim = Claim(
        item_id=item.id,
        claimant_id=user.id,
        brand=clean(form.get('brand'), 100),
        identifiers=identifiers,
        lost_location=clean(form.get('lostLocation'), 150),
        lost_date=lost_date,
        proof_files=json.dumps(proofs),
    )
    try:
        db.session.add(claim)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return api_error('database_error', 500)

    return jsonify({"success": True, "claimId": claim.id, "reference": claim.reference,
                    "status": claim.status}), 201


# ==========================================
# 9. ADMIN API
# ==========================================

@app.route('/api/admin/items', methods=['GET'])
@api_admin_required
def api_admin_items():
    """Every report with its private fields. Unless ?nlp=0, each also has `entities` (what NER
    found in it) and `match` (its best TF-IDF match among the open reports of the other type)."""
    report_type = request.args.get('type')
    query = Item.query
    if report_type in ('lost', 'found'):
        query = query.filter_by(report_type=report_type)
    items = query.order_by(Item.date_reported.desc()).all()
    if request.args.get('nlp') == '0':
        return jsonify([i.to_private() for i in items])

    matcher = build_matcher()
    result = []
    for item in items:
        data = item.to_private()
        data["entities"] = item.entities()
        data["match"] = matcher.best(item.reference_code) if item.status != 'claimed' else None
        result.append(data)
    return jsonify(result)


@app.route('/api/admin/items/<code>', methods=['PATCH'])
@api_admin_required
def api_admin_update_item(code):
    item = Item.query.filter_by(reference_code=code).first()
    if item is None:
        return api_error('not_found', 404)

    data = request.get_json(silent=True) or {}
    if 'status' in data:
        if data['status'] not in ITEM_STATUSES:
            return api_error('invalid_status')
        item.status = data['status']
    if 'storage' in data:
        item.storage = clean(data['storage'], 100)
    if 'turnedInBy' in data:
        item.turned_in_by = clean(data['turnedInBy'], 100)

    db.session.commit()
    return jsonify(item.to_private())


@app.route('/api/admin/items/<code>/matches', methods=['GET'])
@api_admin_required
def api_admin_matches(code):
    """Likely matches for one report, best first: a lost report is compared with the open found
    items, and the reverse, by TF-IDF cosine similarity plus place, category and date (nlp.py).
    Each result has its score (0-100), the signals behind it (0-1) and the other report."""
    item = Item.query.filter_by(reference_code=code).first()
    if item is None:
        return api_error('not_found', 404)

    by_code = {other.reference_code: other for other in Item.query.all()}
    matcher = nlp.Matcher([other.nlp_record() for other in by_code.values()])
    return jsonify([dict(result, item=by_code[result["id"]].to_private())
                    for result in matcher.matches(code)])


@app.route('/api/admin/nlp/entities', methods=['POST'])
@api_admin_required
def api_admin_entities():
    """Runs NER on any text, to try it out: {"text": "..."} -> each entity and a summary per label."""
    text = clean((request.get_json(silent=True) or {}).get('text'), 5000)
    return jsonify({"entities": nlp.extract_entities(text), "summary": nlp.entity_summary(text)})


@app.route('/api/admin/claims', methods=['GET'])
@api_admin_required
def api_admin_claims():
    claims = Claim.query.order_by(Claim.date_submitted.desc()).all()
    return jsonify([c.to_dict(admin=True) for c in claims])


@app.route('/api/admin/claims/<int:claim_id>', methods=['PATCH'])
@api_admin_required
def api_admin_review_claim(claim_id):
    claim = db.session.get(Claim, claim_id)
    if claim is None:
        return api_error('not_found', 404)

    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    if new_status not in ('approved', 'rejected', 'released'):
        return api_error('invalid_status')

    item = claim.item
    if new_status == 'approved':
        # Only one claim per item can be approved, and a collected item stays collected.
        other_approved = any(c.status in ('approved', 'released') for c in item.claims if c.id != claim.id)
        if item.status == 'claimed' or other_approved:
            return api_error('already_approved', 409)
        claim.status = 'approved'
        item.status = 'ready'
    elif new_status == 'rejected':
        claim.status = 'rejected'
        other_approved = any(c.status == 'approved' for c in item.claims if c.id != claim.id)
        if item.status == 'ready' and not other_approved:
            item.status = 'pending'
    elif new_status == 'released':
        if claim.status != 'approved':
            return api_error('not_approved', 409)
        claim.status = 'released'
        item.status = 'claimed'

    claim.admin_note = clean(data.get('note'), 1000)
    claim.reviewed_at = utcnow()
    db.session.commit()
    return jsonify(claim.to_dict(admin=True))


# ==========================================
# 10. MESSAGES API
# ==========================================

def read_message_payload():
    """Returns (text, image) from the JSON body, or raises ValueError."""
    data = request.get_json(silent=True) or {}
    text = clean(data.get('text'), 2000)
    image = data.get('image')
    if image:
        if not isinstance(image, str) or not image.startswith('data:image/') or len(image) > 2_000_000:
            raise ValueError('bad_image')
    else:
        image = None
    if not text and not image:
        raise ValueError('empty_message')
    return text, image


@app.route('/api/messages', methods=['GET'])
@api_login_required
def api_messages():
    user = current_user()
    rows = Message.query.filter_by(student_id=user.id).order_by(Message.time, Message.id).all()
    # `read`: whether the student has seen it (their own messages always are)
    return jsonify([dict(m.to_dict(), read=bool(m.read_by_user)) for m in rows])


@app.route('/api/messages', methods=['POST'])
@api_login_required
def api_send_message():
    user = current_user()
    try:
        text, image = read_message_payload()
    except ValueError as err:
        return api_error(str(err))
    message = Message(student_id=user.id, sender='user', text=text, image=image,
                      read_by_user=True, read_by_swafo=False)
    db.session.add(message)
    db.session.commit()
    return jsonify(message.to_dict()), 201


@app.route('/api/messages/read', methods=['POST'])
@api_login_required
def api_mark_read():
    user = current_user()
    Message.query.filter_by(student_id=user.id, read_by_user=False).update({"read_by_user": True})
    db.session.commit()
    return jsonify({"success": True})


@app.route('/api/admin/conversations', methods=['GET'])
@api_admin_required
def api_admin_conversations():
    student_ids = [row[0] for row in db.session.query(Message.student_id).distinct().all()]
    result = []
    for student_id in student_ids:
        student = db.session.get(User, student_id)
        if student is None:
            continue
        last = (Message.query.filter_by(student_id=student_id)
                .order_by(Message.time.desc(), Message.id.desc()).first())
        unread = Message.query.filter_by(student_id=student_id, sender='user', read_by_swafo=False).count()
        result.append({
            "studentId": student.id,
            "name": student.full_name,
            "email": student.email,
            "schoolId": student.school_id,
            "unread": unread,
            "lastMessage": last.to_dict(),
        })
    result.sort(key=lambda r: r["lastMessage"]["time"], reverse=True)

    # ?with=<student id> also lists that student before either side has written, so SWAFO can
    # start the conversation (the Message button on the Reports page).
    with_id = request.args.get('with', type=int)
    if with_id and with_id not in student_ids:
        student = db.session.get(User, with_id)
        if student is not None and student.role != 'admin':
            result.insert(0, {
                "studentId": student.id,
                "name": student.full_name,
                "email": student.email,
                "schoolId": student.school_id,
                "unread": 0,
                "lastMessage": None,
            })
    return jsonify(result)


@app.route('/api/admin/conversations/<int:student_id>', methods=['GET'])
@api_admin_required
def api_admin_conversation(student_id):
    rows = Message.query.filter_by(student_id=student_id).order_by(Message.time, Message.id).all()
    return jsonify([m.to_dict() for m in rows])


@app.route('/api/admin/conversations/<int:student_id>', methods=['POST'])
@api_admin_required
def api_admin_reply(student_id):
    if db.session.get(User, student_id) is None:
        return api_error('not_found', 404)
    try:
        text, image = read_message_payload()
    except ValueError as err:
        return api_error(str(err))
    message = Message(student_id=student_id, sender='swafo', text=text, image=image,
                      read_by_user=False, read_by_swafo=True)
    db.session.add(message)
    db.session.commit()
    return jsonify(message.to_dict()), 201


@app.route('/api/admin/conversations/<int:student_id>/read', methods=['POST'])
@api_admin_required
def api_admin_mark_read(student_id):
    Message.query.filter_by(student_id=student_id, read_by_swafo=False).update({"read_by_swafo": True})
    db.session.commit()
    return jsonify({"success": True})


# ==========================================
# To star the server:
# ==========================================
def ensure_default_admin():
    """Creates one starter admin if the database has no admin yet. Does nothing otherwise."""
    if User.query.filter_by(role='admin').first():
        return
    email = os.environ.get('ADMIN_EMAIL', 'swafo.admin@dlsud.edu.ph').strip().lower()
    password = os.environ.get('ADMIN_PASSWORD', 'ChangeThisPassword123')
    db.session.add(User(
        full_name='SWAFO Admin',
        school_id='SWAFO-ADMIN',
        email=email,
        password_hash=generate_password_hash(password, method='scrypt'),
        role='admin',
    ))
    db.session.commit()
    print(f'Created starter admin: {email}')


def ensure_columns():
    """db.create_all() never adds columns to tables that already exist, so add the newer ones here."""
    from sqlalchemy import inspect, text
    columns = {column['name'] for column in inspect(db.engine).get_columns('items')}
    if 'location_other' not in columns:
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE items ADD COLUMN location_other VARCHAR(150)'))
        print('Added items.location_other')


# Tables and newer columns are set up whenever the app loads: with `python app.py` locally, and
# on a web host, which imports this file instead of running it.
with app.app_context():
    db.create_all()
    ensure_columns()

if app.secret_key == 'dev-only-change-me' and __name__ != '__main__':
    print('WARNING: SECRET_KEY is not set. Anyone could forge a login. Set it before going public.')


if __name__ == '__main__':
    # Local only: a starter admin for an empty database. A public server never gets one with the
    # default password; upload your database or run create_admin.py there instead.
    with app.app_context():
        ensure_default_admin()
    app.run(debug=True, port=5000)
