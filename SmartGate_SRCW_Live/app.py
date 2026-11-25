import os
import uuid
import json
import csv
import qrcode
from io import BytesIO, StringIO
from base64 import b64encode
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory, Response
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from database import get_db, init_db, log_audit

# ─── Application Configuration ────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get('SMARTGATE_SECRET_KEY') or uuid.uuid4().hex + uuid.uuid4().hex

UPLOAD_FOLDER = os.path.join('static', 'uploads')
QR_FOLDER = os.path.join('static', 'qrcodes')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

# ─── Institutional Constants & Context Processor ──────────────────────────────

COLLEGE_NAME = "Sri Ramakrishna College of Arts & Science for Women"
COLLEGE_SHORT = "SRCW"
TRUST_NAME = "SNR Sons Trust"
COLLEGE_TAGLINE = "Autonomous Institution | Affiliated to Bharathiar University | Re-Accredited by NAAC"
COLLEGE_LOGO = "images/srcw_logo.png"
TRUST_LOGO = "images/snr_trust_logo.jpg"

@app.context_processor
def inject_institutional_context():
    unread_count = 0
    notifications_list = []
    if 'user_id' in session:
        notes, unread_count = get_notifications(session['user_id'], limit=6)
        notifications_list = notes
    return {
        'COLLEGE_NAME': COLLEGE_NAME,
        'COLLEGE_SHORT': COLLEGE_SHORT,
        'TRUST_NAME': TRUST_NAME,
        'COLLEGE_TAGLINE': COLLEGE_TAGLINE,
        'COLLEGE_LOGO': COLLEGE_LOGO,
        'TRUST_LOGO': TRUST_LOGO,
        'notifications': notifications_list,
        'unread': unread_count,
        'current_year': datetime.now().year,
        'now_str': datetime.now().strftime('%d %b %Y, %I:%M %p')
    }

# ─── Helper Functions ─────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in to continue.', 'warning')
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                flash('You are not authorized to access this page.', 'danger')
                return redirect(url_for(f"{session.get('role', 'index')}_dashboard" if session.get('role') in ('admin', 'student', 'tutor', 'hod', 'guard') else 'index'))
            return f(*args, **kwargs)
        return wrapped
    return decorator

def add_notification(user_id, message, db=None):
    try:
        close_conn = False
        if db is None:
            db = get_db()
            close_conn = True
        db.execute("INSERT INTO notifications (user_id, message) VALUES (?,?)", (user_id, message))
        if close_conn:
            db.commit()
            db.close()
    except Exception as e:
        print(f"Error adding notification: {e}")

def get_notifications(user_id, limit=10):
    try:
        db = get_db()
        notes = db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        unread = db.execute(
            "SELECT COUNT(*) as cnt FROM notifications WHERE user_id=? AND is_read=0",
            (user_id,)
        ).fetchone()['cnt']
        db.close()
        return notes, unread
    except Exception as e:
        print(f"Error getting notifications: {e}")
        return [], 0

def generate_qr(pass_code, request_id):
    """Generates QR code payload, saves PNG file and returns base64 string."""
    data = json.dumps({
        'pass_code': pass_code,
        'request_id': request_id,
        'college': COLLEGE_SHORT,
        'institution': COLLEGE_NAME
    })
    img = qrcode.make(data)
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    b64 = b64encode(buf.read()).decode('utf-8')
    path = os.path.join(QR_FOLDER, f"{pass_code}.png")
    img.save(path)
    return b64, data

def get_student_by_user(user_id):
    db = get_db()
    s = db.execute("""
        SELECT s.*, u.username, d.name as dept_name, d.code as dept_code,
               c.name as course_name, c.code as course_code,
               cls.class_name, cls.tutor_id
        FROM students s
        JOIN users u ON s.user_id = u.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN courses c ON s.course_id = c.id
        LEFT JOIN classes cls ON s.class_id = cls.id
        WHERE s.user_id = ?
    """, (user_id,)).fetchone()
    db.close()
    return s

def get_staff_by_user(user_id, role):
    db = get_db()
    data = None
    if role == 'tutor':
        data = db.execute("""
            SELECT t.*, u.username, d.name as dept_name, d.code as dept_code
            FROM tutors t
            JOIN users u ON t.user_id = u.id
            LEFT JOIN departments d ON t.department_id = d.id
            WHERE t.user_id = ?
        """, (user_id,)).fetchone()
    elif role == 'hod':
        data = db.execute("""
            SELECT h.*, u.username, d.name as dept_name, d.code as dept_code
            FROM hods h
            JOIN users u ON h.user_id = u.id
            LEFT JOIN departments d ON h.department_id = d.id
            WHERE h.user_id = ?
        """, (user_id,)).fetchone()
    elif role == 'guard':
        data = db.execute("""
            SELECT g.*, u.username
            FROM guards g
            JOIN users u ON g.user_id = u.id
            WHERE g.user_id = ?
        """, (user_id,)).fetchone()
    db.close()
    return data

# ─── Public & Authentication Routes ───────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        role = session.get('role')
        if role in ('admin', 'student', 'tutor', 'hod', 'guard'):
            return redirect(url_for(f'{role}_dashboard'))
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/login', methods=['GET', 'POST'])
@app.route('/login/<role>', methods=['GET', 'POST'])
def login(role=None):
    if 'user_id' in session:
        current_role = session.get('role')
        if current_role in ('admin', 'student', 'tutor', 'hod', 'guard'):
            return redirect(url_for(f'{current_role}_dashboard'))

    if request.method == 'POST':
        login_ident = request.form.get('login_ident', '').strip()
        password = request.form.get('password', '')

        if not login_ident or not password:
            flash('Please provide both username/email and password.', 'danger')
            return render_template('login.html', requested_role=role)

        db = get_db()
        user = db.execute("""
            SELECT * FROM users
            WHERE (LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?))
        """, (login_ident, login_ident)).fetchone()

        if not user or not check_password_hash(user['password_hash'], password):
            flash('Invalid credentials. Please verify your username/email and password.', 'danger')
            db.close()
            return render_template('login.html', requested_role=role)

        if user['status'] == 'PENDING':
            flash('Your registration is pending Administrator verification. You will be able to log in once an administrator approves your account.', 'warning')
            db.close()
            return render_template('login.html', requested_role=role)
        elif user['status'] != 'ACTIVE':
            flash('Your account is currently inactive. Please contact the administrator.', 'warning')
            db.close()
            return render_template('login.html', requested_role=role)

        # Set session
        session.clear()
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        session['email'] = user['email']

        log_audit(db, user['id'], user['role'], 'USER_LOGIN', None, f"Logged in from {request.remote_addr}")
        db.commit()
        db.close()

        flash(f"Welcome back, {user['username']}! Logged in as {user['role'].title()}.", 'success')

        portal_map = {
            'admin': 'admin_dashboard',
            'student': 'student_dashboard',
            'tutor': 'tutor_dashboard',
            'hod': 'hod_dashboard',
            'guard': 'guard_dashboard'
        }
        return redirect(url_for(portal_map.get(user['role'], 'index')))

    return render_template('login.html', requested_role=role)

# Role alias routes for compatibility
@app.route('/login_student')
def login_student():
    return redirect(url_for('login', role='student'))

@app.route('/login_tutor')
def login_tutor():
    return redirect(url_for('login', role='tutor'))

@app.route('/login_hod')
def login_hod():
    return redirect(url_for('login', role='hod'))

@app.route('/login_guard')
def login_guard():
    return redirect(url_for('login', role='guard'))

@app.route('/login_admin')
def login_admin():
    return redirect(url_for('login', role='admin'))

@app.route('/register', methods=['GET', 'POST'])
@app.route('/register/student', methods=['GET', 'POST'], endpoint='register_student')
def register():
    """Public self-registration strictly for Students."""
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        register_number = request.form.get('register_number', '').strip().upper()
        email = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        department_id = request.form.get('department_id')
        course_id = request.form.get('course_id')
        year = request.form.get('year')
        phone = request.form.get('phone', '').strip()
        parent_phone = request.form.get('parent_phone', '').strip()
        student_type = request.form.get('student_type', 'Day Scholar').strip()

        # Validation
        if not (name and register_number and email and username and password and department_id and course_id and year and parent_phone):
            flash('Please fill in all mandatory fields.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register.html', departments=departments)

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register.html', departments=departments)

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register.html', departments=departments)

        # Uniqueness checks
        exist_user = db.execute("SELECT id FROM users WHERE LOWER(username)=? OR LOWER(email)=?", (username, email)).fetchone()
        if exist_user:
            flash('Username or Email already registered. Please login or choose another.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register.html', departments=departments)

        exist_student = db.execute("SELECT id FROM students WHERE register_number=?", (register_number,)).fetchone()
        if exist_student:
            flash('Register Number already registered in SmartGate system.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register.html', departments=departments)

        # Photo upload (optional)
        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename and allowed_file(file.filename):
                fname = secure_filename(f"{register_number}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                photo_filename = fname

        # Automatically resolve assigned class (Single class per year per course - NO SECTIONS)
        cls = db.execute("""
            SELECT id FROM classes
            WHERE department_id=? AND course_id=? AND year=?
        """, (department_id, course_id, year)).fetchone()
        class_id = cls['id'] if cls else None

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Insert user
        db.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, 'student', 'ACTIVE', ?)
        """, (username, email, generate_password_hash(password), now_str))
        user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']

        # Insert student record
        db.execute("""
            INSERT INTO students (
                user_id, name, register_number, department_id, course_id, year,
                class_id, email, phone, parent_phone, student_type, photo, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, register_number, department_id, course_id, year,
              class_id, email, phone, parent_phone, student_type, photo_filename, now_str))

        log_audit(db, user_id, 'student', 'STUDENT_REGISTERED', None, f"Student {name} ({register_number}) self-registered")
        db.commit()
        db.close()

        flash('Account created successfully. Please login.', 'success')
        return redirect(url_for('login'))

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()
    return render_template('register.html', departments=departments)

@app.route('/register/faculty', methods=['GET', 'POST'])
@app.route('/register/tutor', methods=['GET', 'POST'])
def register_faculty():
    """Registration for Faculty / Class Tutor (Unified Role)."""
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        employee_id = request.form.get('employee_id', '').strip().upper()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        department_id = request.form.get('department_id')
        designation = request.form.get('designation', 'Assistant Professor & Class Tutor').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Username defaults to lowercase employee_id or email prefix
        username = employee_id.lower().replace('-', '_').replace(' ', '')
        if not username:
            username = email.split('@')[0].lower()

        # Validation
        if not (name and employee_id and email and phone and department_id and password):
            flash('Please complete all mandatory fields.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_faculty.html', departments=departments)

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_faculty.html', departments=departments)

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_faculty.html', departments=departments)

        # Uniqueness checks
        exist_user = db.execute("SELECT id FROM users WHERE LOWER(username)=? OR LOWER(email)=?", (username, email)).fetchone()
        if exist_user:
            flash('An account with this Email or Employee ID is already registered.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_faculty.html', departments=departments)

        exist_tutor = db.execute("SELECT id FROM tutors WHERE employee_id=?", (employee_id,)).fetchone()
        if exist_tutor:
            flash('Employee ID already registered in SmartGate system.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_faculty.html', departments=departments)

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Insert user (Role: tutor = Faculty/Class Tutor)
        db.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, 'tutor', 'ACTIVE', ?)
        """, (username, email, generate_password_hash(password), now_str))
        user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']

        # Insert tutor record
        db.execute("""
            INSERT INTO tutors (user_id, name, employee_id, department_id, designation, phone, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, employee_id, department_id, designation, phone, email, now_str))

        log_audit(db, user_id, 'tutor', 'TUTOR_REGISTERED', None, f"Faculty/Tutor {name} ({employee_id}) registered")
        db.commit()
        db.close()

        flash(f'Faculty / Class Tutor account created successfully! You may now sign in using username "{username}" or your email.', 'success')
        return redirect(url_for('login'))

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()
    return render_template('register_faculty.html', departments=departments)

@app.route('/register/hod', methods=['GET', 'POST'])
def register_hod():
    """Registration for Head of Department (HOD) - Requires Admin Verification."""
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        employee_id = request.form.get('employee_id', '').strip().upper()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        department_id = request.form.get('department_id')
        designation = request.form.get('designation', 'Professor & Head of Department').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Username defaults to lowercase employee_id
        username = employee_id.lower().replace('-', '_').replace(' ', '')
        if not username:
            username = email.split('@')[0].lower()

        # Validation
        if not (name and employee_id and email and phone and department_id and password):
            flash('Please complete all mandatory fields.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_hod.html', departments=departments)

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_hod.html', departments=departments)

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_hod.html', departments=departments)

        # Uniqueness checks
        exist_user = db.execute("SELECT id FROM users WHERE LOWER(username)=? OR LOWER(email)=?", (username, email)).fetchone()
        if exist_user:
            flash('An account with this Email or Employee ID is already registered.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_hod.html', departments=departments)

        exist_hod = db.execute("SELECT id FROM hods WHERE employee_id=?", (employee_id,)).fetchone()
        if exist_hod:
            flash('Employee ID already registered in SmartGate system.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('register_hod.html', departments=departments)

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Insert user with status PENDING (Requires Admin Verification)
        db.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, 'hod', 'PENDING', ?)
        """, (username, email, generate_password_hash(password), now_str))
        user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']

        # Insert HOD record
        db.execute("""
            INSERT INTO hods (user_id, name, employee_id, department_id, designation, phone, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, employee_id, department_id, designation, phone, email, now_str))

        log_audit(db, user_id, 'hod', 'HOD_REGISTRATION_SUBMITTED', None,
                  f"HOD registration submitted by {name} ({employee_id}) - Status PENDING Administrator verification")

        # Notify administrator
        admin = db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if admin:
            add_notification(admin['id'], f"New HOD registration pending verification: {name} ({employee_id})", db=db)

        db.commit()
        db.close()

        flash('HOD registration submitted successfully! Your account is pending Administrator verification before you can log in.', 'info')
        return redirect(url_for('login'))

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()
    return render_template('register_hod.html', departments=departments)

@app.route('/logout')
def logout():
    uid = session.get('user_id')
    role = session.get('role')
    if uid:
        try:
            db = get_db()
            log_audit(db, uid, role, 'USER_LOGOUT', None, "Logged out")
            db.commit()
            db.close()
        except Exception:
            pass
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))

# ─── Dynamic Course & Class APIs ──────────────────────────────────────────────

@app.route('/api/courses')
def api_courses():
    """Returns active courses for a given department."""
    dept_id = request.args.get('department_id')
    if not dept_id:
        return jsonify([])
    db = get_db()
    courses = db.execute("""
        SELECT id, name, code, duration_years
        FROM courses
        WHERE department_id = ? AND status = 'ACTIVE'
        ORDER BY name
    """, (dept_id,)).fetchall()
    db.close()
    return jsonify([dict(c) for c in courses])

@app.route('/api/course/<int:course_id>/years')
def api_course_years(course_id):
    """Returns duration years of a specific course."""
    db = get_db()
    c = db.execute("SELECT duration_years FROM courses WHERE id=?", (course_id,)).fetchone()
    db.close()
    if c:
        return jsonify({'duration_years': c['duration_years']})
    return jsonify({'duration_years': 3})

# ─── Student Portal ───────────────────────────────────────────────────────────

@app.route('/student/dashboard')
@login_required('student')
def student_dashboard():
    student = get_student_by_user(session['user_id'])
    if not student:
        flash('Student record not found. Please contact administration.', 'danger')
        return redirect(url_for('logout'))

    db = get_db()
    stats = db.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status LIKE 'PENDING_%' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END) as approved,
            SUM(CASE WHEN status IN ('OUTSIDE', 'COMPLETED') THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected
        FROM gate_pass_requests
        WHERE student_id = ?
    """, (student['id'],)).fetchone()

    # Active pass: either APPROVED (ready to exit) or OUTSIDE
    active_pass = db.execute("""
        SELECT gpr.*, gp.pass_code, gp.qr_data
        FROM gate_pass_requests gpr
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        WHERE gpr.student_id = ? AND gpr.status IN ('APPROVED', 'OUTSIDE')
        ORDER BY gpr.created_at DESC LIMIT 1
    """, (student['id'],)).fetchone()

    recent_requests = db.execute("""
        SELECT gpr.*, gp.pass_code
        FROM gate_pass_requests gpr
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        WHERE gpr.student_id = ?
        ORDER BY gpr.created_at DESC LIMIT 6
    """, (student['id'],)).fetchall()

    db.close()
    return render_template(
        'student/dashboard.html',
        student=student,
        total=stats['total'] or 0,
        pending=stats['pending'] or 0,
        approved=stats['approved'] or 0,
        completed=stats['completed'] or 0,
        rejected=stats['rejected'] or 0,
        active_pass=active_pass,
        recent_requests=recent_requests
    )

@app.route('/student/new-request', methods=['GET', 'POST'])
@login_required('student')
def student_new_request():
    student = get_student_by_user(session['user_id'])
    if not student:
        flash('Student profile not found.', 'danger')
        return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        reason_cat = request.form.get('reason_cat', '').strip()
        reason_detail = request.form.get('reason_detail', '').strip()
        destination = request.form.get('destination', '').strip()
        request_date = request.form.get('request_date', '').strip()
        exit_time = request.form.get('exit_time', '').strip()
        request_type = request.form.get('request_type', 'Normal').strip()

        # Combine reason
        full_reason = f"[{reason_cat}] {reason_detail}" if reason_cat else reason_detail

        if not (reason_detail and destination and request_date and exit_time):
            flash('Please complete all required fields.', 'danger')
            return render_template('student/new_request.html', student=student)

        # Check for existing active/pending pass on same day
        db = get_db()
        existing_active = db.execute("""
            SELECT id FROM gate_pass_requests
            WHERE student_id = ? AND request_date = ? AND status IN ('PENDING_TUTOR', 'PENDING_HOD', 'APPROVED', 'OUTSIDE')
        """, (student['id'], request_date)).fetchone()

        if existing_active:
            flash(f'You already have an active or pending gate pass request for {request_date}.', 'warning')
            db.close()
            return redirect(url_for('student_my_requests'))

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Insert new gate pass request with initial status PENDING_TUTOR (NO RETURN FIELDS)
        db.execute("""
            INSERT INTO gate_pass_requests (
                student_id, reason, destination, request_date, exit_time,
                request_type, remarks, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_TUTOR', ?, ?)
        """, (student['id'], full_reason, destination, request_date, exit_time,
              request_type, None, now_str, now_str))

        req_id = db.execute("SELECT last_insert_rowid() as id").fetchone()['id']

        # Audit log
        log_audit(db, session['user_id'], 'student', 'REQUEST_CREATED', req_id,
                  f"Gate pass created for {request_date} at {exit_time} to {destination}")

        # Notify Class Tutor
        if student['tutor_id']:
            add_notification(student['tutor_id'],
                             f"New Gate Pass request from {student['name']} ({student['register_number']}) for {request_date} at {exit_time}.", db=db)

        db.commit()
        db.close()

        flash('Your Gate Pass request has been submitted to your Class Tutor for review.', 'success')
        return redirect(url_for('student_my_requests'))

    return render_template('student/new_request.html', student=student)

@app.route('/student/my-requests')
@login_required('student')
def student_my_requests():
    student = get_student_by_user(session['user_id'])
    status_filter = request.args.get('status', 'ALL').upper()

    db = get_db()
    query = """
        SELECT gpr.*, gp.pass_code
        FROM gate_pass_requests gpr
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        WHERE gpr.student_id = ?
    """
    params = [student['id']]

    if status_filter == 'PENDING':
        query += " AND gpr.status LIKE 'PENDING_%'"
    elif status_filter == 'APPROVED':
        query += " AND gpr.status = 'APPROVED'"
    elif status_filter == 'COMPLETED':
        query += " AND gpr.status IN ('OUTSIDE', 'COMPLETED')"
    elif status_filter == 'REJECTED':
        query += " AND gpr.status = 'REJECTED'"

    query += " ORDER BY gpr.created_at DESC"
    requests_list = db.execute(query, params).fetchall()
    db.close()

    return render_template(
        'student/my_requests.html',
        student=student,
        requests=requests_list,
        current_filter=status_filter
    )

@app.route('/student/request/<int:req_id>')
@login_required('student')
def student_request_detail(req_id):
    student = get_student_by_user(session['user_id'])
    db = get_db()
    req = db.execute("""
        SELECT gpr.*, gp.pass_code, gp.qr_data, gl.actual_exit_time,
               u_guard.username as guard_username
        FROM gate_pass_requests gpr
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        LEFT JOIN gate_logs gl ON gpr.id = gl.request_id
        LEFT JOIN users u_guard ON gl.gate_guard_id = u_guard.id
        WHERE gpr.id = ? AND gpr.student_id = ?
    """, (req_id, student['id'])).fetchone()

    if not req:
        flash('Request not found.', 'danger')
        db.close()
        return redirect(url_for('student_my_requests'))

    approvals = db.execute("""
        SELECT a.*, u.username as approver_username
        FROM approvals a
        JOIN users u ON a.approver_id = u.id
        WHERE a.request_id = ?
        ORDER BY a.approved_at ASC
    """, (req_id,)).fetchall()

    db.close()
    return render_template('student/request_detail.html', student=student, req=req, approvals=approvals)

@app.route('/student/pass/<int:req_id>')
@app.route('/student/pass-qr/<int:req_id>')
@login_required('student')
def student_pass(req_id):
    student = get_student_by_user(session['user_id'])
    db = get_db()
    pass_info = db.execute("""
        SELECT gpr.*, gp.pass_code, gp.qr_data, gp.generated_at,
               s.name, s.register_number, s.photo, s.parent_phone, s.student_type,
               d.name as dept_name, c.name as course_name, cls.class_name
        FROM gate_pass_requests gpr
        JOIN gate_passes gp ON gpr.id = gp.request_id
        JOIN students s ON gpr.student_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN courses c ON s.course_id = c.id
        LEFT JOIN classes cls ON s.class_id = cls.id
        WHERE gpr.id = ? AND gpr.student_id = ?
    """, (req_id, student['id'])).fetchone()

    if not pass_info:
        flash('Gate pass not found or not yet approved.', 'warning')
        db.close()
        return redirect(url_for('student_my_requests'))

    # Load QR image file or b64
    qr_b64 = None
    qr_path = os.path.join(QR_FOLDER, f"{pass_info['pass_code']}.png")
    if os.path.exists(qr_path):
        with open(qr_path, 'rb') as f:
            qr_b64 = b64encode(f.read()).decode('utf-8')
    else:
        qr_b64, _ = generate_qr(pass_info['pass_code'], req_id)

    db.close()
    return render_template('student/pass_qr.html', student=student, p=pass_info, qr_b64=qr_b64)

@app.route('/student/approved-passes')
@login_required('student')
def student_approved_passes():
    student = get_student_by_user(session['user_id'])
    db = get_db()
    passes = db.execute("""
        SELECT gpr.*, gp.pass_code
        FROM gate_pass_requests gpr
        JOIN gate_passes gp ON gpr.id = gp.request_id
        WHERE gpr.student_id = ? AND gpr.status IN ('APPROVED', 'OUTSIDE', 'COMPLETED')
        ORDER BY gpr.created_at DESC
    """, (student['id'],)).fetchall()
    db.close()
    return render_template('student/approved_passes.html', student=student, passes=passes)

@app.route('/student/history')
@login_required('student')
def student_history():
    student = get_student_by_user(session['user_id'])
    db = get_db()
    history = db.execute("""
        SELECT gpr.*, gp.pass_code, gl.actual_exit_time
        FROM gate_pass_requests gpr
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        LEFT JOIN gate_logs gl ON gpr.id = gl.request_id
        WHERE gpr.student_id = ?
        ORDER BY gpr.created_at DESC
    """, (student['id'],)).fetchall()
    db.close()
    return render_template('student/history.html', student=student, history=history)

@app.route('/student/cancel-request/<int:req_id>', methods=['POST'])
@login_required('student')
def student_cancel_request(req_id):
    student = get_student_by_user(session['user_id'])
    db = get_db()
    req = db.execute("SELECT status FROM gate_pass_requests WHERE id=? AND student_id=?", (req_id, student['id'])).fetchone()

    if not req:
        flash('Request not found.', 'danger')
    elif req['status'] not in ('PENDING_TUTOR', 'PENDING_HOD'):
        flash('Cannot cancel a pass that has already been approved or processed.', 'warning')
    else:
        db.execute("UPDATE gate_pass_requests SET status='CANCELLED' WHERE id=?", (req_id,))
        log_audit(db, session['user_id'], 'student', 'REQUEST_CANCELLED', req_id, "Student cancelled gate pass request")
        db.commit()
        flash('Your request has been cancelled.', 'info')

    db.close()
    return redirect(url_for('student_my_requests'))

@app.route('/student/profile', methods=['GET', 'POST'])
@login_required('student')
def student_profile():
    student = get_student_by_user(session['user_id'])
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        parent_phone = request.form.get('parent_phone', '').strip()
        new_password = request.form.get('new_password', '').strip()

        db = get_db()
        db.execute("""
            UPDATE students SET phone=?, parent_phone=? WHERE user_id=?
        """, (phone, parent_phone, session['user_id']))

        if new_password:
            if len(new_password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
                db.close()
                return redirect(url_for('student_profile'))
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), session['user_id']))
            log_audit(db, session['user_id'], 'student', 'PASSWORD_CHANGED', None, "Student updated password")

        db.commit()
        db.close()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('student_profile'))

    return render_template('student/profile.html', student=student)

@app.route('/student/notifications/read', methods=['POST'])
@login_required('student')
def student_notifications_read():
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    db.commit()
    db.close()
    return jsonify({'status': 'ok'})

# ─── Class Tutor Portal ───────────────────────────────────────────────────────

@app.route('/tutor/dashboard')
@login_required('tutor')
def tutor_dashboard():
    tutor = get_staff_by_user(session['user_id'], 'tutor')
    db = get_db()

    # Classes assigned to this tutor
    assigned_classes = db.execute("""
        SELECT cls.*, d.name as dept_name, c.name as course_name,
               (SELECT COUNT(*) FROM students s WHERE s.class_id = cls.id) as student_count
        FROM classes cls
        JOIN departments d ON cls.department_id = d.id
        JOIN courses c ON cls.course_id = c.id
        WHERE cls.tutor_id = ?
        ORDER BY c.name, cls.year
    """, (session['user_id'],)).fetchall()

    class_ids = [c['id'] for c in assigned_classes]

    if class_ids:
        placeholders = ','.join(['?'] * len(class_ids))
        today_date = datetime.now().strftime('%Y-%m-%d')

        stats = db.execute(f"""
            SELECT
                SUM(CASE WHEN gpr.status = 'PENDING_TUTOR' THEN 1 ELSE 0 END) as pending_tutor,
                SUM(CASE WHEN gpr.status = 'APPROVED' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN gpr.status = 'REJECTED' THEN 1 ELSE 0 END) as rejected,
                COUNT(*) as total
            FROM gate_pass_requests gpr
            JOIN students s ON gpr.student_id = s.id
            WHERE s.class_id IN ({placeholders})
        """, class_ids).fetchone()

        pending_requests = db.execute(f"""
            SELECT gpr.*, s.name as student_name, s.register_number, s.photo,
                   cls.class_name, c.name as course_name
            FROM gate_pass_requests gpr
            JOIN students s ON gpr.student_id = s.id
            JOIN classes cls ON s.class_id = cls.id
            JOIN courses c ON s.course_id = c.id
            WHERE s.class_id IN ({placeholders}) AND gpr.status = 'PENDING_TUTOR'
            ORDER BY gpr.created_at ASC
        """, class_ids).fetchall()
    else:
        stats = {'pending_tutor': 0, 'approved': 0, 'rejected': 0, 'total': 0}
        pending_requests = []

    db.close()
    return render_template(
        'tutor/dashboard.html',
        tutor=tutor,
        assigned_classes=assigned_classes,
        pending_count=stats['pending_tutor'] or 0,
        approved_count=stats['approved'] or 0,
        rejected_count=stats['rejected'] or 0,
        total_count=stats['total'] or 0,
        pending_requests=pending_requests
    )

@app.route('/tutor/all-requests')
@login_required('tutor')
def tutor_all_requests():
    tutor = get_staff_by_user(session['user_id'], 'tutor')
    status_filter = request.args.get('status', 'ALL').upper()

    db = get_db()
    assigned_classes = db.execute("SELECT id FROM classes WHERE tutor_id=?", (session['user_id'],)).fetchall()
    class_ids = [c['id'] for c in assigned_classes]

    requests_list = []
    if class_ids:
        placeholders = ','.join(['?'] * len(class_ids))
        query = f"""
            SELECT gpr.*, s.name as student_name, s.register_number, s.photo,
                   cls.class_name, c.name as course_name
            FROM gate_pass_requests gpr
            JOIN students s ON gpr.student_id = s.id
            JOIN classes cls ON s.class_id = cls.id
            JOIN courses c ON s.course_id = c.id
            WHERE s.class_id IN ({placeholders})
        """
        params = list(class_ids)

        if status_filter == 'PENDING':
            query += " AND gpr.status = 'PENDING_TUTOR'"
        elif status_filter == 'APPROVED':
            query += " AND gpr.status IN ('PENDING_HOD', 'APPROVED', 'OUTSIDE', 'COMPLETED')"
        elif status_filter == 'REJECTED':
            query += " AND gpr.status = 'REJECTED'"

        query += " ORDER BY gpr.created_at DESC"
        requests_list = db.execute(query, params).fetchall()

    db.close()
    return render_template(
        'tutor/all_requests.html',
        tutor=tutor,
        requests=requests_list,
        current_filter=status_filter
    )

@app.route('/tutor/request/<int:req_id>', methods=['GET', 'POST'])
@login_required('tutor')
def tutor_request_detail(req_id):
    tutor = get_staff_by_user(session['user_id'], 'tutor')
    db = get_db()

    req = db.execute("""
        SELECT gpr.*, s.name as student_name, s.register_number, s.photo,
               s.phone as student_phone, s.parent_phone, s.student_type,
               cls.class_name, cls.tutor_id, c.name as course_name, d.name as dept_name,
               s.department_id
        FROM gate_pass_requests gpr
        JOIN students s ON gpr.student_id = s.id
        JOIN classes cls ON s.class_id = cls.id
        JOIN courses c ON s.course_id = c.id
        JOIN departments d ON s.department_id = d.id
        WHERE gpr.id = ?
    """, (req_id,)).fetchone()

    if not req or req['tutor_id'] != session['user_id']:
        flash('Request not found or not in your assigned class.', 'danger')
        db.close()
        return redirect(url_for('tutor_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        remarks = request.form.get('remarks', '').strip()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if action == 'approve':
            # Advance to PENDING_HOD
            db.execute("""
                UPDATE gate_pass_requests
                SET status='PENDING_HOD', updated_at=?
                WHERE id=?
            """, (now_str, req_id))

            db.execute("""
                INSERT INTO approvals (request_id, approver_id, role, decision, remarks, approved_at)
                VALUES (?, ?, 'tutor', 'APPROVED', ?, ?)
            """, (req_id, session['user_id'], remarks or "Recommended by Class Tutor", now_str))

            log_audit(db, session['user_id'], 'tutor', 'TUTOR_APPROVED', req_id,
                      f"Tutor approved request. Remarks: {remarks}")

            # Notify Student
            add_notification(req['student_id'], "Your Gate Pass has been recommended by your Class Tutor and forwarded to the HOD.", db=db)

            # Notify Department HOD
            hod = db.execute("SELECT user_id FROM hods WHERE department_id=?", (req['department_id'],)).fetchone()
            if hod:
                add_notification(hod['user_id'], f"Gate Pass approval pending for {req['student_name']} ({req['class_name']}).", db=db)

            flash(f"Request for {req['student_name']} recommended and forwarded to HOD.", 'success')

        elif action == 'reject':
            db.execute("""
                UPDATE gate_pass_requests
                SET status='REJECTED', updated_at=?
                WHERE id=?
            """, (now_str, req_id))

            db.execute("""
                INSERT INTO approvals (request_id, approver_id, role, decision, remarks, approved_at)
                VALUES (?, ?, 'tutor', 'REJECTED', ?, ?)
            """, (req_id, session['user_id'], remarks or "Rejected by Class Tutor", now_str))

            log_audit(db, session['user_id'], 'tutor', 'TUTOR_REJECTED', req_id,
                      f"Tutor rejected request. Reason: {remarks}")

            add_notification(req['student_id'], f"Your Gate Pass request was rejected by Class Tutor. Reason: {remarks}", db=db)
            flash(f"Request for {req['student_name']} was rejected.", 'warning')

        db.commit()
        db.close()
        return redirect(url_for('tutor_dashboard'))

    approvals = db.execute("""
        SELECT a.*, u.username as approver_username
        FROM approvals a
        JOIN users u ON a.approver_id = u.id
        WHERE a.request_id = ?
        ORDER BY a.approved_at ASC
    """, (req_id,)).fetchall()

    db.close()
    return render_template('tutor/request_detail.html', tutor=tutor, req=req, approvals=approvals)

@app.route('/tutor/profile')
@login_required('tutor')
def tutor_profile():
    tutor = get_staff_by_user(session['user_id'], 'tutor')
    db = get_db()
    assigned_classes = db.execute("""
        SELECT cls.*, d.name as dept_name, c.name as course_name,
               (SELECT COUNT(*) FROM students s WHERE s.class_id = cls.id) as student_count
        FROM classes cls
        JOIN departments d ON cls.department_id = d.id
        JOIN courses c ON cls.course_id = c.id
        WHERE cls.tutor_id = ?
        ORDER BY c.name, cls.year
    """, (session['user_id'],)).fetchall()
    db.close()
    return render_template('tutor/profile.html', tutor=tutor, assigned_classes=assigned_classes)

@app.route('/tutor/notifications/read', methods=['POST'])
@login_required('tutor')
def tutor_notifications_read():
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    db.commit()
    db.close()
    return jsonify({'status': 'ok'})

# ─── HOD Portal ───────────────────────────────────────────────────────────────

@app.route('/hod/dashboard')
@login_required('hod')
def hod_dashboard():
    hod = get_staff_by_user(session['user_id'], 'hod')
    db = get_db()
    dept_id = hod['department_id'] if hod else None

    if dept_id:
        stats = db.execute("""
            SELECT
                SUM(CASE WHEN gpr.status = 'PENDING_HOD' THEN 1 ELSE 0 END) as pending_hod,
                SUM(CASE WHEN gpr.status = 'APPROVED' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN gpr.status IN ('OUTSIDE', 'COMPLETED') THEN 1 ELSE 0 END) as completed,
                COUNT(*) as total
            FROM gate_pass_requests gpr
            JOIN students s ON gpr.student_id = s.id
            WHERE s.department_id = ?
        """, (dept_id,)).fetchone()

        pending_requests = db.execute("""
            SELECT gpr.*, s.name as student_name, s.register_number, s.photo,
                   cls.class_name, c.name as course_name,
                   (SELECT remarks FROM approvals WHERE request_id=gpr.id AND role='tutor' ORDER BY id DESC LIMIT 1) as tutor_remarks
            FROM gate_pass_requests gpr
            JOIN students s ON gpr.student_id = s.id
            JOIN classes cls ON s.class_id = cls.id
            JOIN courses c ON s.course_id = c.id
            WHERE s.department_id = ? AND gpr.status = 'PENDING_HOD'
            ORDER BY gpr.created_at ASC
        """, (dept_id,)).fetchall()
    else:
        stats = {'pending_hod': 0, 'approved': 0, 'completed': 0, 'total': 0}
        pending_requests = []

    db.close()
    return render_template(
        'hod/dashboard.html',
        hod=hod,
        pending_count=stats['pending_hod'] or 0,
        approved_count=stats['approved'] or 0,
        completed_count=stats['completed'] or 0,
        total_count=stats['total'] or 0,
        pending_requests=pending_requests
    )

@app.route('/hod/all-requests')
@login_required('hod')
def hod_all_requests():
    hod = get_staff_by_user(session['user_id'], 'hod')
    status_filter = request.args.get('status', 'ALL').upper()

    db = get_db()
    dept_id = hod['department_id'] if hod else None
    requests_list = []

    if dept_id:
        query = """
            SELECT gpr.*, s.name as student_name, s.register_number, s.photo,
                   cls.class_name, c.name as course_name, gp.pass_code
            FROM gate_pass_requests gpr
            JOIN students s ON gpr.student_id = s.id
            JOIN classes cls ON s.class_id = cls.id
            JOIN courses c ON s.course_id = c.id
            LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
            WHERE s.department_id = ?
        """
        params = [dept_id]

        if status_filter == 'PENDING':
            query += " AND gpr.status = 'PENDING_HOD'"
        elif status_filter == 'APPROVED':
            query += " AND gpr.status IN ('APPROVED', 'OUTSIDE', 'COMPLETED')"
        elif status_filter == 'REJECTED':
            query += " AND gpr.status = 'REJECTED'"

        query += " ORDER BY gpr.created_at DESC"
        requests_list = db.execute(query, params).fetchall()

    db.close()
    return render_template(
        'hod/all_requests.html',
        hod=hod,
        requests=requests_list,
        current_filter=status_filter
    )

@app.route('/hod/request/<int:req_id>', methods=['GET', 'POST'])
@login_required('hod')
def hod_request_detail(req_id):
    hod = get_staff_by_user(session['user_id'], 'hod')
    db = get_db()

    req = db.execute("""
        SELECT gpr.*, s.name as student_name, s.register_number, s.photo,
               s.phone as student_phone, s.parent_phone, s.student_type,
               cls.class_name, c.name as course_name, d.name as dept_name,
               s.department_id, gp.pass_code
        FROM gate_pass_requests gpr
        JOIN students s ON gpr.student_id = s.id
        JOIN classes cls ON s.class_id = cls.id
        JOIN courses c ON s.course_id = c.id
        JOIN departments d ON s.department_id = d.id
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        WHERE gpr.id = ?
    """, (req_id,)).fetchone()

    if not req or (hod and req['department_id'] != hod['department_id']):
        flash('Request not found or outside your department.', 'danger')
        db.close()
        return redirect(url_for('hod_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action')
        remarks = request.form.get('remarks', '').strip()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if action == 'approve':
            # Generate unique Pass Code (e.g. SRCW-YYYY-XXXXX)
            pass_code = f"SRCW-{datetime.now().strftime('%y%m')}-{uuid.uuid4().hex[:6].upper()}"

            # Generate QR Code image & payload
            b64_qr, qr_data = generate_qr(pass_code, req_id)

            # Mark APPROVED
            db.execute("""
                UPDATE gate_pass_requests
                SET status='APPROVED', updated_at=?
                WHERE id=?
            """, (now_str, req_id))

            # Insert Gate Pass (expires at 2:00 PM when college concludes)
            expires_at = f"{req['request_date']} 14:00:00"
            db.execute("""
                INSERT INTO gate_passes (request_id, pass_code, qr_data, generated_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET pass_code=excluded.pass_code, qr_data=excluded.qr_data, expires_at=excluded.expires_at
            """, (req_id, pass_code, qr_data, now_str, expires_at))

            # Record Approval
            db.execute("""
                INSERT INTO approvals (request_id, approver_id, role, decision, remarks, approved_at)
                VALUES (?, ?, 'hod', 'APPROVED', ?, ?)
            """, (req_id, session['user_id'], remarks or "Approved by Head of Department", now_str))

            log_audit(db, session['user_id'], 'hod', 'HOD_APPROVED', req_id,
                      f"HOD approved request. Pass Code: {pass_code}")

            # Notify Student
            add_notification(req['student_id'],
                             f"Your Gate Pass ({pass_code}) has been APPROVED by the HOD. You may now view and show your QR pass at the main gate.", db=db)

            flash(f"Gate Pass approved successfully! Pass Code: {pass_code}", 'success')

        elif action == 'reject':
            db.execute("""
                UPDATE gate_pass_requests
                SET status='REJECTED', updated_at=?
                WHERE id=?
            """, (now_str, req_id))

            db.execute("""
                INSERT INTO approvals (request_id, approver_id, role, decision, remarks, approved_at)
                VALUES (?, ?, 'hod', 'REJECTED', ?, ?)
            """, (req_id, session['user_id'], remarks or "Rejected by HOD", now_str))

            log_audit(db, session['user_id'], 'hod', 'HOD_REJECTED', req_id,
                      f"HOD rejected request. Reason: {remarks}")

            add_notification(req['student_id'], f"Your Gate Pass request was rejected by HOD. Reason: {remarks}", db=db)
            flash('Gate Pass request rejected.', 'warning')

        db.commit()
        db.close()
        return redirect(url_for('hod_dashboard'))

    approvals = db.execute("""
        SELECT a.*, u.username as approver_username
        FROM approvals a
        JOIN users u ON a.approver_id = u.id
        WHERE a.request_id = ?
        ORDER BY a.approved_at ASC
    """, (req_id,)).fetchall()

    db.close()
    return render_template('hod/request_detail.html', hod=hod, req=req, approvals=approvals)

@app.route('/hod/reports')
@login_required('hod')
def hod_reports():
    hod = get_staff_by_user(session['user_id'], 'hod')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    status_filter = request.args.get('status', 'ALL')
    export_csv = request.args.get('export') == 'csv'

    db = get_db()
    dept_id = hod['department_id'] if hod else None

    query = """
        SELECT gpr.id, gpr.request_date, gpr.exit_time, gpr.reason, gpr.destination, gpr.status,
               s.name as student_name, s.register_number, c.name as course_name, cls.class_name,
               gp.pass_code, gl.actual_exit_time
        FROM gate_pass_requests gpr
        JOIN students s ON gpr.student_id = s.id
        JOIN classes cls ON s.class_id = cls.id
        JOIN courses c ON s.course_id = c.id
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        LEFT JOIN gate_logs gl ON gpr.id = gl.request_id
        WHERE s.department_id = ?
    """
    params = [dept_id]

    if start_date:
        query += " AND gpr.request_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND gpr.request_date <= ?"
        params.append(end_date)
    if status_filter != 'ALL':
        query += " AND gpr.status = ?"
        params.append(status_filter)

    query += " ORDER BY gpr.request_date DESC, gpr.created_at DESC"
    records = db.execute(query, params).fetchall()
    db.close()

    if export_csv:
        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(['Pass Code', 'Date', 'Student Name', 'Reg No', 'Course', 'Class', 'Exit Time', 'Reason', 'Destination', 'Status', 'Actual Exit Time'])
        for r in records:
            cw.writerow([
                r['pass_code'] or 'N/A', r['request_date'], r['student_name'], r['register_number'],
                r['course_name'], r['class_name'], r['exit_time'], r['reason'], r['destination'],
                r['status'], r['actual_exit_time'] or 'Not Exited'
            ])
        output = si.getvalue()
        return Response(output, mimetype='text/csv', headers={"Content-disposition": f"attachment; filename=SRCW_HOD_Report_{datetime.now().strftime('%Y%m%d')}.csv"})

    return render_template(
        'hod/reports.html',
        hod=hod,
        records=records,
        start_date=start_date,
        end_date=end_date,
        current_status=status_filter
    )

@app.route('/hod/notifications/read', methods=['POST'])
@login_required('hod')
def hod_notifications_read():
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    db.commit()
    db.close()
    return jsonify({'status': 'ok'})

# ─── Gate Guard Portal (Exit Only - No Return) ────────────────────────────────

@app.route('/guard/dashboard')
@login_required('guard')
def guard_dashboard():
    guard = get_staff_by_user(session['user_id'], 'guard')
    db = get_db()
    today_date = datetime.now().strftime('%Y-%m-%d')

    stats = db.execute("""
        SELECT
            COUNT(DISTINCT gl.id) as exits_today,
            (SELECT COUNT(*) FROM gate_pass_requests WHERE request_date=? AND status='APPROVED') as pending_approved,
            (SELECT COUNT(*) FROM gate_pass_requests WHERE status IN ('OUTSIDE', 'COMPLETED')) as total_completed
        FROM gate_logs gl
        WHERE DATE(gl.actual_exit_time) = ?
    """, (today_date, today_date)).fetchone()

    recent_exits = db.execute("""
        SELECT gl.actual_exit_time, gpr.destination, gpr.reason,
               s.name as student_name, s.register_number, s.photo,
               d.code as dept_code, c.name as course_name, gp.pass_code
        FROM gate_logs gl
        JOIN gate_pass_requests gpr ON gl.request_id = gpr.id
        JOIN students s ON gpr.student_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN courses c ON s.course_id = c.id
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        ORDER BY gl.actual_exit_time DESC LIMIT 10
    """).fetchall()

    db.close()
    return render_template(
        'guard/dashboard.html',
        guard=guard,
        exits_today=stats['exits_today'] or 0,
        pending_approved=stats['pending_approved'] or 0,
        total_completed=stats['total_completed'] or 0,
        recent_exits=recent_exits
    )

@app.route('/guard/verify', methods=['GET', 'POST'])
@login_required('guard')
def guard_verify():
    guard = get_staff_by_user(session['user_id'], 'guard')
    pass_code = request.form.get('pass_code', '').strip().upper() if request.method == 'POST' else request.args.get('pass_code', '').strip().upper()
    pass_info = None
    verification_status = None
    message = None

    if pass_code:
        db = get_db()
        pass_info = db.execute("""
            SELECT gp.*, gpr.reason, gpr.destination, gpr.request_date, gpr.exit_time,
                   gpr.status, gpr.id as request_id,
                   s.name as student_name, s.register_number, s.photo, s.phone as student_phone,
                   s.parent_phone, s.student_type,
                   d.name as dept_name, c.name as course_name, cls.class_name,
                   gl.actual_exit_time
            FROM gate_passes gp
            JOIN gate_pass_requests gpr ON gp.request_id = gpr.id
            JOIN students s ON gpr.student_id = s.id
            LEFT JOIN departments d ON s.department_id = d.id
            LEFT JOIN courses c ON s.course_id = c.id
            LEFT JOIN classes cls ON s.class_id = cls.id
            LEFT JOIN gate_logs gl ON gpr.id = gl.request_id
            WHERE UPPER(gp.pass_code) = ?
        """, (pass_code,)).fetchone()
        db.close()

        if not pass_info:
            verification_status = 'NOT_FOUND'
            message = f"Invalid Pass Code '{pass_code}'. No such pass found in SmartGate system."
        elif pass_info['status'] == 'APPROVED':
            verification_status = 'VALID'
            message = "Pass verified! Ready for exit authorization."
        elif pass_info['status'] in ('OUTSIDE', 'COMPLETED'):
            verification_status = 'ALREADY_EXITED'
            message = f"Student already exited at {pass_info['actual_exit_time'][:16] if pass_info['actual_exit_time'] else 'earlier'}. Pass is COMPLETED."
        elif pass_info['status'] == 'REJECTED':
            verification_status = 'REJECTED'
            message = "This gate pass was REJECTED by college authorities."
        else:
            verification_status = 'PENDING'
            message = f"Pass is still awaiting approval (Status: {pass_info['status']}). Exit not permitted."

    return render_template(
        'guard/verify.html',
        guard=guard,
        pass_info=pass_info,
        verification_status=verification_status,
        message=message,
        searched_code=pass_code
    )

@app.route('/guard/confirm-exit/<int:req_id>', methods=['POST'])
@login_required('guard')
def guard_confirm_exit(req_id):
    """
    Confirms exit of student at main gate:
    1. Records actual exit time in gate_logs.
    2. Transitions status: APPROVED -> OUTSIDE -> COMPLETED.
    3. Records institutional audit logs.
    4. Notifies student.
    NO RETURN REQUIRED (College ends at 2:00 PM).
    """
    db = get_db()
    req = db.execute("""
        SELECT gpr.*, s.name as student_name, s.register_number, s.id as student_id
        FROM gate_pass_requests gpr
        JOIN students s ON gpr.student_id = s.id
        WHERE gpr.id = ?
    """, (req_id,)).fetchone()

    if not req:
        flash('Request not found.', 'danger')
        db.close()
        return redirect(url_for('guard_verify'))

    if req['status'] != 'APPROVED':
        flash(f"Cannot authorize exit. Pass status is '{req['status']}'.", 'warning')
        db.close()
        return redirect(url_for('guard_verify'))

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 1. Insert exit log
    db.execute("""
        INSERT INTO gate_logs (request_id, gate_guard_id, actual_exit_time, exit_status, created_at)
        VALUES (?, ?, ?, 'EXITED', ?)
    """, (req_id, session['user_id'], now_str, now_str))

    # 2. Transition status to OUTSIDE, then immediately COMPLETED
    db.execute("""
        UPDATE gate_pass_requests
        SET status='COMPLETED', updated_at=?
        WHERE id=?
    """, (now_str, req_id))

    # 3. Audit trail
    log_audit(db, session['user_id'], 'guard', 'GATE_EXIT_VERIFIED', req_id,
              f"Guard verified exit for {req['student_name']} ({req['register_number']}) at {now_str}")
    log_audit(db, session['user_id'], 'guard', 'PASS_COMPLETED', req_id,
              f"Pass marked COMPLETED upon exit verification (Exit-Only policy)")

    # 4. Notify student
    add_notification(req['student_id'],
                     f"Your exit was verified at the Main Gate at {now_str}. Pass is now COMPLETED.", db=db)

    db.commit()
    db.close()

    flash(f"Exit verified successfully for {req['student_name']} ({req['register_number']}). Gate pass marked COMPLETED.", 'success')
    return redirect(url_for('guard_dashboard'))

@app.route('/guard/history')
@login_required('guard')
def guard_history():
    guard = get_staff_by_user(session['user_id'], 'guard')
    date_filter = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    search_q = request.args.get('q', '').strip()

    db = get_db()
    query = """
        SELECT gl.id as log_id, gl.actual_exit_time, gl.exit_status,
               gpr.destination, gpr.reason, gpr.status,
               s.name as student_name, s.register_number, s.photo,
               d.code as dept_code, c.name as course_name, gp.pass_code,
               u_guard.username as guard_name
        FROM gate_logs gl
        JOIN gate_pass_requests gpr ON gl.request_id = gpr.id
        JOIN students s ON gpr.student_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN courses c ON s.course_id = c.id
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        LEFT JOIN users u_guard ON gl.gate_guard_id = u_guard.id
        WHERE 1=1
    """
    params = []

    if date_filter:
        query += " AND DATE(gl.actual_exit_time) = ?"
        params.append(date_filter)

    if search_q:
        query += " AND (s.name LIKE ? OR s.register_number LIKE ? OR gp.pass_code LIKE ?)"
        params.extend([f"%{search_q}%", f"%{search_q}%", f"%{search_q}%"])

    query += " ORDER BY gl.actual_exit_time DESC"
    records = db.execute(query, params).fetchall()
    db.close()

    return render_template(
        'guard/history.html',
        guard=guard,
        records=records,
        date_filter=date_filter,
        search_q=search_q
    )

@app.route('/guard/notifications/read', methods=['POST'])
@login_required('guard')
def guard_notifications_read():
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    db.commit()
    db.close()
    return jsonify({'status': 'ok'})

# ─── API for Camera Scanner Verification ──────────────────────────────────────

@app.route('/api/verify-pass/<pass_code>')
def api_verify_pass(pass_code):
    db = get_db()
    gp = db.execute("SELECT * FROM gate_passes WHERE UPPER(pass_code)=?", (pass_code.upper(),)).fetchone()
    if not gp:
        db.close()
        return jsonify({'valid': False, 'message': 'Pass code not found'})

    req = db.execute("""
        SELECT gpr.*, s.name as student_name, s.register_number, s.parent_phone, s.photo,
               d.name as dept_name, c.name as course_name, cls.class_name
        FROM gate_pass_requests gpr
        JOIN students s ON gpr.student_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN courses c ON s.course_id = c.id
        LEFT JOIN classes cls ON s.class_id = cls.id
        WHERE gpr.id = ?
    """, (gp['request_id'],)).fetchone()
    db.close()

    if not req:
        return jsonify({'valid': False, 'message': 'Request details missing'})

    is_ready = req['status'] == 'APPROVED'
    return jsonify({
        'valid': is_ready,
        'status': req['status'],
        'request_id': req['id'],
        'pass_code': gp['pass_code'],
        'student_name': req['student_name'],
        'register_number': req['register_number'],
        'department': req['dept_name'],
        'course': req['course_name'],
        'class_name': req['class_name'],
        'destination': req['destination'],
        'reason': req['reason'],
        'exit_time': req['exit_time'],
        'parent_phone': req['parent_phone']
    })

# ─── Administrator Portal ─────────────────────────────────────────────────────

@app.route('/admin/dashboard')
@login_required('admin')
def admin_dashboard():
    db = get_db()
    counts = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM users) as total_users,
            (SELECT COUNT(*) FROM users WHERE status='PENDING') as pending_registrations,
            (SELECT COUNT(*) FROM students) as total_students,
            (SELECT COUNT(*) FROM tutors) as total_tutors,
            (SELECT COUNT(*) FROM hods) as total_hods,
            (SELECT COUNT(*) FROM guards) as total_guards,
            (SELECT COUNT(*) FROM departments WHERE status='ACTIVE') as total_departments,
            (SELECT COUNT(*) FROM courses WHERE status='ACTIVE') as total_courses,
            (SELECT COUNT(*) FROM classes) as total_classes,
            (SELECT COUNT(*) FROM gate_passes) as total_passes_issued,
            (SELECT COUNT(*) FROM gate_logs WHERE DATE(actual_exit_time)=DATE('now')) as exits_today
    """).fetchone()

    recent_audits = db.execute("""
        SELECT a.*, u.username
        FROM audit_logs a
        LEFT JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC LIMIT 10
    """).fetchall()

    recent_passes = db.execute("""
        SELECT gpr.*, s.name as student_name, s.register_number, d.code as dept_code, gp.pass_code
        FROM gate_pass_requests gpr
        JOIN students s ON gpr.student_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        ORDER BY gpr.created_at DESC LIMIT 6
    """).fetchall()

    db.close()
    return render_template(
        'admin/dashboard.html',
        counts=counts,
        recent_audits=recent_audits,
        recent_passes=recent_passes
    )

@app.route('/admin/users')
@login_required('admin')
def admin_users():
    role_filter = request.args.get('role', 'ALL').lower()
    search_q = request.args.get('q', '').strip()

    db = get_db()
    query = """
        SELECT u.id, u.username, u.email, u.role, u.status, u.created_at,
               COALESCE(s.name, t.name, h.name, g.name, u.username) as display_name,
               COALESCE(s.register_number, t.employee_id, h.employee_id, g.employee_id, '-') as ident_number,
               COALESCE(s.phone, t.phone, h.phone, g.phone, '-') as phone,
               d.name as dept_name
        FROM users u
        LEFT JOIN students s ON u.id = s.user_id
        LEFT JOIN tutors t ON u.id = t.user_id
        LEFT JOIN hods h ON u.id = h.user_id
        LEFT JOIN guards g ON u.id = g.user_id
        LEFT JOIN departments d ON (s.department_id = d.id OR t.department_id = d.id OR h.department_id = d.id)
        WHERE 1=1
    """
    params = []

    if role_filter == 'pending':
        query += " AND u.status = 'PENDING'"
    elif role_filter != 'all':
        query += " AND u.role = ?"
        params.append(role_filter)

    if search_q:
        query += " AND (u.username LIKE ? OR u.email LIKE ? OR display_name LIKE ? OR ident_number LIKE ?)"
        params.extend([f"%{search_q}%", f"%{search_q}%", f"%{search_q}%", f"%{search_q}%"])

    query += " ORDER BY u.role, u.username"
    users_list = db.execute(query, params).fetchall()
    pending_count = db.execute("SELECT COUNT(*) as cnt FROM users WHERE status='PENDING'").fetchone()['cnt']
    db.close()

    return render_template(
        'admin/users.html',
        users=users_list,
        current_role=role_filter,
        search_q=search_q,
        pending_count=pending_count
    )

@app.route('/admin/approve-user/<int:user_id>', methods=['GET', 'POST'])
@login_required('admin')
def admin_approve_user(user_id):
    """Administrator verifies and approves pending staff/HOD registration."""
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        flash('User account not found.', 'danger')
        db.close()
        return redirect(url_for('admin_users'))

    db.execute("UPDATE users SET status='ACTIVE' WHERE id=?", (user_id,))
    log_audit(db, session['user_id'], 'admin', 'USER_APPROVED', user_id,
              f"Administrator approved {u['role'].upper()} account: {u['username']} ({u['email']})")

    add_notification(user_id, "Your account has been verified and activated by the Administrator. You may now log in.", db=db)
    db.commit()
    db.close()
    flash(f"Account for {u['username']} ({u['role'].upper()}) has been verified and ACTIVATED successfully.", 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/reject-user/<int:user_id>', methods=['GET', 'POST'])
@login_required('admin')
def admin_reject_user(user_id):
    """Administrator rejects pending registration."""
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        flash('User account not found.', 'danger')
        db.close()
        return redirect(url_for('admin_users'))

    db.execute("UPDATE users SET status='INACTIVE' WHERE id=?", (user_id,))
    log_audit(db, session['user_id'], 'admin', 'USER_REJECTED', user_id,
              f"Administrator rejected {u['role'].upper()} registration for {u['username']}")
    db.commit()
    db.close()
    flash(f"Account registration for {u['username']} was rejected and set to INACTIVE.", 'warning')
    return redirect(request.referrer or url_for('admin_users'))

@app.route('/admin/user/new', methods=['GET', 'POST'])
@login_required('admin')
def admin_user_new():
    """Create staff account (Tutor, HOD, Guard, Admin)."""
    db = get_db()
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        role = request.form.get('role', '').strip().lower()
        name = request.form.get('name', '').strip()
        employee_id = request.form.get('employee_id', '').strip().upper()
        phone = request.form.get('phone', '').strip()
        department_id = request.form.get('department_id') or None
        designation = request.form.get('designation', '').strip()
        shift = request.form.get('shift', 'General').strip()

        if not (username and email and password and role and name):
            flash('Please complete all mandatory fields.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('admin/user_form.html', departments=departments)

        if role not in ('admin', 'tutor', 'hod', 'guard'):
            flash('Invalid role selected.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('admin/user_form.html', departments=departments)

        exist = db.execute("SELECT id FROM users WHERE LOWER(username)=? OR LOWER(email)=?", (username, email)).fetchone()
        if exist:
            flash('Username or Email already exists.', 'danger')
            departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
            db.close()
            return render_template('admin/user_form.html', departments=departments)

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Insert user
        db.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, ?, 'ACTIVE', ?)
        """, (username, email, generate_password_hash(password), role, now_str))
        user_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()['id']

        # Insert profile
        if role == 'tutor':
            db.execute("""
                INSERT INTO tutors (user_id, name, employee_id, department_id, designation, phone, email, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, name, employee_id or f"TUT-{user_id}", department_id, designation, phone, email, now_str))
        elif role == 'hod':
            db.execute("""
                INSERT INTO hods (user_id, name, employee_id, department_id, phone, email, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, name, employee_id or f"HOD-{user_id}", department_id, phone, email, now_str))
        elif role == 'guard':
            db.execute("""
                INSERT INTO guards (user_id, name, employee_id, shift, phone, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, name, employee_id or f"SEC-{user_id}", shift, phone, now_str))

        log_audit(db, session['user_id'], 'admin', 'USER_CREATED', None,
                  f"Created new {role} user: {username} ({name})")

        db.commit()
        db.close()
        flash(f"Account for {name} ({role.title()}) created successfully.", 'success')
        return redirect(url_for('admin_users'))

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()
    return render_template('admin/user_form.html', departments=departments)

@app.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required('admin')
def admin_user_edit(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash('User not found.', 'danger')
        db.close()
        return redirect(url_for('admin_users'))

    profile = None
    if user['role'] == 'student':
        profile = db.execute("SELECT * FROM students WHERE user_id=?", (user_id,)).fetchone()
    elif user['role'] == 'tutor':
        profile = db.execute("SELECT * FROM tutors WHERE user_id=?", (user_id,)).fetchone()
    elif user['role'] == 'hod':
        profile = db.execute("SELECT * FROM hods WHERE user_id=?", (user_id,)).fetchone()
    elif user['role'] == 'guard':
        profile = db.execute("SELECT * FROM guards WHERE user_id=?", (user_id,)).fetchone()

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        status = request.form.get('status', 'ACTIVE').strip()
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        department_id = request.form.get('department_id') or None

        db.execute("UPDATE users SET email=?, status=? WHERE id=?", (email, status, user_id))

        if user['role'] == 'student' and profile:
            parent_phone = request.form.get('parent_phone', '').strip()
            db.execute("UPDATE students SET name=?, phone=?, parent_phone=?, department_id=? WHERE user_id=?",
                       (name, phone, parent_phone, department_id, user_id))
        elif user['role'] == 'tutor' and profile:
            designation = request.form.get('designation', '').strip()
            db.execute("UPDATE tutors SET name=?, phone=?, department_id=?, designation=? WHERE user_id=?",
                       (name, phone, department_id, designation, user_id))
        elif user['role'] == 'hod' and profile:
            db.execute("UPDATE hods SET name=?, phone=?, department_id=? WHERE user_id=?",
                       (name, phone, department_id, user_id))
        elif user['role'] == 'guard' and profile:
            shift = request.form.get('shift', 'General').strip()
            db.execute("UPDATE guards SET name=?, phone=?, shift=? WHERE user_id=?",
                       (name, phone, shift, user_id))

        log_audit(db, session['user_id'], 'admin', 'USER_UPDATED', None, f"Admin updated user {user['username']}")
        db.commit()
        db.close()
        flash(f"User '{user['username']}' updated successfully.", 'success')
        return redirect(url_for('admin_users'))

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()
    return render_template('admin/user_edit.html', user=user, profile=profile, departments=departments)

@app.route('/admin/user/<int:user_id>/toggle-status', methods=['POST'])
@login_required('admin')
def admin_user_toggle_status(user_id):
    if user_id == session['user_id']:
        flash('Cannot deactivate your own administrator account.', 'danger')
        return redirect(url_for('admin_users'))

    db = get_db()
    u = db.execute("SELECT username, status FROM users WHERE id=?", (user_id,)).fetchone()
    if u:
        new_status = 'INACTIVE' if u['status'] == 'ACTIVE' else 'ACTIVE'
        db.execute("UPDATE users SET status=? WHERE id=?", (new_status, user_id))
        log_audit(db, session['user_id'], 'admin', 'USER_STATUS_TOGGLED', None,
                  f"User {u['username']} set to {new_status}")
        db.commit()
        flash(f"User '{u['username']}' status changed to {new_status}.", 'success')
    db.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/reset-password', methods=['POST'])
@login_required('admin')
def admin_user_reset_password(user_id):
    """Direct administrative password reset (No external email required)."""
    new_password = request.form.get('new_password', '').strip()
    if not new_password or len(new_password) < 4:
        flash('New password must be at least 4 characters long.', 'danger')
        return redirect(url_for('admin_users'))

    db = get_db()
    u = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if u:
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash(new_password), user_id))
        log_audit(db, session['user_id'], 'admin', 'ADMIN_PASSWORD_RESET', None,
                  f"Password reset for user {u['username']}")
        db.commit()
        flash(f"Password for '{u['username']}' successfully reset to: {new_password}", 'success')
    db.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@login_required('admin')
def admin_user_delete(user_id):
    if user_id == session['user_id']:
        flash('Cannot delete your own administrator account.', 'danger')
        return redirect(url_for('admin_users'))

    db = get_db()
    u = db.execute("SELECT username, role FROM users WHERE id=?", (user_id,)).fetchone()
    if u:
        if u['role'] == 'student':
            db.execute("DELETE FROM students WHERE user_id=?", (user_id,))
        elif u['role'] == 'tutor':
            db.execute("UPDATE classes SET tutor_id=NULL WHERE tutor_id=?", (user_id,))
            db.execute("DELETE FROM tutors WHERE user_id=?", (user_id,))
        elif u['role'] == 'hod':
            db.execute("DELETE FROM hods WHERE user_id=?", (user_id,))
        elif u['role'] == 'guard':
            db.execute("DELETE FROM guards WHERE user_id=?", (user_id,))

        db.execute("DELETE FROM notifications WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        log_audit(db, session['user_id'], 'admin', 'USER_DELETED', None, f"Deleted user {u['username']} ({u['role']})")
        db.commit()
        flash(f"User '{u['username']}' deleted.", 'info')
    db.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/departments', methods=['GET', 'POST'])
@login_required('admin')
def admin_departments():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip().upper()

        if not name or not code:
            flash('Department name and code are required.', 'danger')
        else:
            exist = db.execute("SELECT id FROM departments WHERE UPPER(code)=? OR UPPER(name)=?", (code, name.upper())).fetchone()
            if exist:
                flash('Department name or code already exists.', 'danger')
            else:
                db.execute("INSERT INTO departments (name, code, status) VALUES (?,?, 'ACTIVE')", (name, code))
                log_audit(db, session['user_id'], 'admin', 'DEPARTMENT_CREATED', None, f"Created department {name} ({code})")
                db.commit()
                flash(f"Department '{name}' ({code}) added successfully.", 'success')
        return redirect(url_for('admin_departments'))

    departments = db.execute("""
        SELECT d.*,
               (SELECT COUNT(*) FROM courses c WHERE c.department_id = d.id) as course_count,
               (SELECT COUNT(*) FROM students s WHERE s.department_id = d.id) as student_count,
               (SELECT COUNT(*) FROM tutors t WHERE t.department_id = d.id) as tutor_count
        FROM departments d ORDER BY d.name
    """).fetchall()
    db.close()
    return render_template('admin/departments.html', departments=departments)

@app.route('/admin/department/<int:dept_id>/edit', methods=['POST'])
@login_required('admin')
def admin_department_edit(dept_id):
    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip().upper()
    status = request.form.get('status', 'ACTIVE').strip()

    db = get_db()
    db.execute("UPDATE departments SET name=?, code=?, status=? WHERE id=?", (name, code, status, dept_id))
    log_audit(db, session['user_id'], 'admin', 'DEPARTMENT_UPDATED', None, f"Updated department id {dept_id}")
    db.commit()
    db.close()
    flash('Department updated successfully.', 'success')
    return redirect(url_for('admin_departments'))

@app.route('/admin/courses', methods=['GET', 'POST'])
@login_required('admin')
def admin_courses():
    db = get_db()
    if request.method == 'POST':
        dept_id = request.form.get('department_id')
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip().upper()
        duration_years = int(request.form.get('duration_years', 3))

        if not (dept_id and name and code):
            flash('Department, Course name, and Code are required.', 'danger')
        else:
            exist = db.execute("SELECT id FROM courses WHERE department_id=? AND UPPER(name)=?", (dept_id, name.upper())).fetchone()
            if exist:
                flash('Course already exists in this department.', 'danger')
            else:
                db.execute("""
                    INSERT INTO courses (department_id, name, code, duration_years, status)
                    VALUES (?, ?, ?, ?, 'ACTIVE')
                """, (dept_id, name, code, duration_years))
                cid = db.execute("SELECT last_insert_rowid() as id").fetchone()['id']

                # Automatically create single class per year (NO SECTIONS)
                for yr in range(1, duration_years + 1):
                    cname = f"{name} Year {yr}"
                    db.execute("""
                        INSERT INTO classes (department_id, course_id, year, class_name)
                        VALUES (?, ?, ?, ?)
                    """, (dept_id, cid, yr, cname))

                log_audit(db, session['user_id'], 'admin', 'COURSE_CREATED', None, f"Created course {name} ({duration_years} yrs)")
                db.commit()
                flash(f"Course '{name}' created with {duration_years} annual classes.", 'success')
        return redirect(url_for('admin_courses'))

    courses = db.execute("""
        SELECT c.*, d.name as dept_name, d.code as dept_code,
               (SELECT COUNT(*) FROM classes cls WHERE cls.course_id = c.id) as class_count,
               (SELECT COUNT(*) FROM students s WHERE s.course_id = c.id) as student_count
        FROM courses c
        JOIN departments d ON c.department_id = d.id
        ORDER BY d.name, c.name
    """).fetchall()

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()
    return render_template('admin/courses.html', courses=courses, departments=departments)

@app.route('/admin/classes', methods=['GET', 'POST'])
@login_required('admin')
def admin_classes():
    """Manage single classes per year for each course (NO SECTIONS)."""
    db = get_db()
    classes = db.execute("""
        SELECT cls.*, d.name as dept_name, d.code as dept_code,
               c.name as course_name, c.code as course_code,
               u.username as tutor_username, t.name as tutor_name,
               (SELECT COUNT(*) FROM students s WHERE s.class_id = cls.id) as student_count
        FROM classes cls
        JOIN departments d ON cls.department_id = d.id
        JOIN courses c ON cls.course_id = c.id
        LEFT JOIN users u ON cls.tutor_id = u.id
        LEFT JOIN tutors t ON u.id = t.user_id
        ORDER BY d.name, c.name, cls.year
    """).fetchall()

    tutors = db.execute("""
        SELECT u.id, u.username, t.name, d.code as dept_code
        FROM users u
        JOIN tutors t ON u.id = t.user_id
        LEFT JOIN departments d ON t.department_id = d.id
        WHERE u.role = 'tutor' AND u.status = 'ACTIVE'
        ORDER BY t.name
    """).fetchall()

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()
    return render_template('admin/classes.html', classes=classes, tutors=tutors, departments=departments)

@app.route('/admin/class/<int:class_id>/edit', methods=['POST'])
@login_required('admin')
def admin_class_edit(class_id):
    tutor_id = request.form.get('tutor_id')
    tutor_id = int(tutor_id) if tutor_id and tutor_id != '0' else None
    class_name = request.form.get('class_name', '').strip()

    db = get_db()
    if class_name:
        db.execute("UPDATE classes SET class_name=?, tutor_id=? WHERE id=?", (class_name, tutor_id, class_id))
    else:
        db.execute("UPDATE classes SET tutor_id=? WHERE id=?", (tutor_id, class_id))

    log_audit(db, session['user_id'], 'admin', 'CLASS_TUTOR_ASSIGNED', None,
              f"Updated class {class_id} tutor to {tutor_id}")
    db.commit()
    db.close()
    flash('Class tutor assignment updated successfully.', 'success')
    return redirect(url_for('admin_classes'))

@app.route('/admin/tutor-assignment', methods=['GET', 'POST'])
@login_required('admin')
def admin_tutor_assignment():
    db = get_db()
    if request.method == 'POST':
        class_id = request.form.get('class_id')
        tutor_id = request.form.get('tutor_id')
        tutor_id = int(tutor_id) if tutor_id and tutor_id != '0' else None

        db.execute("UPDATE classes SET tutor_id=? WHERE id=?", (tutor_id, class_id))
        log_audit(db, session['user_id'], 'admin', 'TUTOR_ASSIGNED', None, f"Assigned tutor {tutor_id} to class {class_id}")
        db.commit()
        flash('Class tutor mapped successfully.', 'success')
        return redirect(url_for('admin_tutor_assignment'))

    classes = db.execute("""
        SELECT cls.*, d.name as dept_name, c.name as course_name,
               u.username as tutor_username, t.name as tutor_name
        FROM classes cls
        JOIN departments d ON cls.department_id = d.id
        JOIN courses c ON cls.course_id = c.id
        LEFT JOIN users u ON cls.tutor_id = u.id
        LEFT JOIN tutors t ON u.id = t.user_id
        ORDER BY d.name, c.name, cls.year
    """).fetchall()

    tutors = db.execute("""
        SELECT u.id, u.username, t.name, d.name as dept_name, d.code as dept_code
        FROM users u
        JOIN tutors t ON u.id = t.user_id
        LEFT JOIN departments d ON t.department_id = d.id
        WHERE u.status = 'ACTIVE'
        ORDER BY t.name
    """).fetchall()

    db.close()
    return render_template('admin/tutor_assignment.html', classes=classes, tutors=tutors)

@app.route('/admin/hod-assignment', methods=['GET', 'POST'])
@login_required('admin')
def admin_hod_assignment():
    db = get_db()
    if request.method == 'POST':
        dept_id = request.form.get('department_id')
        user_id = request.form.get('user_id')

        if dept_id and user_id:
            # Update hod record
            db.execute("UPDATE hods SET department_id=? WHERE user_id=?", (dept_id, user_id))
            log_audit(db, session['user_id'], 'admin', 'HOD_ASSIGNED', None, f"Assigned HOD {user_id} to department {dept_id}")
            db.commit()
            flash('HOD department mapping updated.', 'success')
        return redirect(url_for('admin_hod_assignment'))

    departments = db.execute("""
        SELECT d.*, h.name as hod_name, u.username as hod_username, h.phone as hod_phone
        FROM departments d
        LEFT JOIN hods h ON d.id = h.department_id
        LEFT JOIN users u ON h.user_id = u.id
        ORDER BY d.name
    """).fetchall()

    hods = db.execute("""
        SELECT u.id as user_id, u.username, h.name, h.employee_id
        FROM users u
        JOIN hods h ON u.id = h.user_id
        WHERE u.status = 'ACTIVE'
        ORDER BY h.name
    """).fetchall()

    db.close()
    return render_template('admin/hod_assignment.html', departments=departments, hods=hods)

@app.route('/admin/audit-logs')
@login_required('admin')
def admin_audit_logs():
    role_filter = request.args.get('role', 'ALL')
    action_filter = request.args.get('action', '').strip()
    date_filter = request.args.get('date', '').strip()

    db = get_db()
    query = """
        SELECT a.*, u.username
        FROM audit_logs a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE 1=1
    """
    params = []

    if role_filter != 'ALL':
        query += " AND a.role = ?"
        params.append(role_filter)

    if action_filter:
        query += " AND a.action LIKE ?"
        params.append(f"%{action_filter}%")

    if date_filter:
        query += " AND DATE(a.created_at) = ?"
        params.append(date_filter)

    query += " ORDER BY a.created_at DESC LIMIT 200"
    logs = db.execute(query, params).fetchall()
    db.close()

    return render_template(
        'admin/audit_logs.html',
        logs=logs,
        current_role=role_filter,
        action_filter=action_filter,
        date_filter=date_filter
    )

@app.route('/admin/reports')
@app.route('/admin/reports/csv')
@login_required('admin')
def admin_reports():
    dept_filter = request.args.get('department_id', '')
    status_filter = request.args.get('status', 'ALL')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    export_csv = request.args.get('export') == 'csv' or request.path.endswith('/csv')

    db = get_db()
    query = """
        SELECT gpr.id, gpr.request_date, gpr.exit_time, gpr.reason, gpr.destination, gpr.status,
               s.name as student_name, s.register_number, s.student_type,
               d.name as dept_name, c.name as course_name, cls.class_name,
               gp.pass_code, gl.actual_exit_time
        FROM gate_pass_requests gpr
        JOIN students s ON gpr.student_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN courses c ON s.course_id = c.id
        LEFT JOIN classes cls ON s.class_id = cls.id
        LEFT JOIN gate_passes gp ON gpr.id = gp.request_id
        LEFT JOIN gate_logs gl ON gpr.id = gl.request_id
        WHERE 1=1
    """
    params = []

    if dept_filter:
        query += " AND s.department_id = ?"
        params.append(dept_filter)

    if status_filter != 'ALL':
        query += " AND gpr.status = ?"
        params.append(status_filter)

    if start_date:
        query += " AND gpr.request_date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND gpr.request_date <= ?"
        params.append(end_date)

    query += " ORDER BY gpr.request_date DESC, gpr.created_at DESC"
    records = db.execute(query, params).fetchall()

    if export_csv:
        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(['Pass Code', 'Date', 'Student Name', 'Reg No', 'Department', 'Course', 'Class', 'Type', 'Reason', 'Destination', 'Exit Time', 'Status', 'Actual Exit Time'])
        for r in records:
            cw.writerow([
                r['pass_code'] or 'N/A', r['request_date'], r['student_name'], r['register_number'],
                r['dept_name'], r['course_name'], r['class_name'], r['student_type'],
                r['reason'], r['destination'], r['exit_time'], r['status'],
                r['actual_exit_time'] or 'Not Exited'
            ])
        output = si.getvalue()
        db.close()
        return Response(output, mimetype='text/csv', headers={"Content-disposition": f"attachment; filename=SRCW_GatePass_MasterReport_{datetime.now().strftime('%Y%m%d')}.csv"})

    departments = db.execute("SELECT * FROM departments WHERE status='ACTIVE' ORDER BY name").fetchall()
    db.close()

    return render_template(
        'admin/reports.html',
        records=records,
        departments=departments,
        current_dept=dept_filter,
        current_status=status_filter,
        start_date=start_date,
        end_date=end_date
    )

# ─── Static Files & Image Serving ─────────────────────────────────────────────

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/qrcodes/<filename>')
def qr_file(filename):
    return send_from_directory(QR_FOLDER, filename)

# ─── Application Bootstrap ────────────────────────────────────────────────────

if __name__ == '__main__':
    if not os.path.exists('database.db'):
        init_db()
    else:
        try:
            init_db()
        except Exception as e:
            print(f"Database bootstrap notice: {e}")
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
