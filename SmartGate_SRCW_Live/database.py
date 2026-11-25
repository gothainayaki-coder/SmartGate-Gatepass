import sqlite3
import os
import shutil
from werkzeug.security import generate_password_hash
from datetime import datetime

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')

def get_db():
    conn = sqlite3.connect(DATABASE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def log_audit(conn, user_id, role, action, request_id=None, details=""):
    """Helper to record system audit trail."""
    try:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("""
            INSERT INTO audit_logs (user_id, role, action, request_id, details, created_at)
            VALUES (?,?,?,?,?,?)
        """, (user_id, role, action, request_id, details, now_str))
    except Exception as e:
        print(f"Audit log error: {e}")

def migrate_db():
    """
    Safely migrates SQLite schema to:
    - 5 Roles only: admin, student, tutor, hod, guard
    - 5 Institutional Departments
    - 13 Courses with configurable duration
    - Classes: exactly one class per year for each course (No sections!)
    - Tutors & HODs mapped to classes & departments
    - Gate pass requests: PENDING_TUTOR -> PENDING_HOD -> APPROVED -> OUTSIDE -> COMPLETED
    - No Return workflow, return columns, or return statuses
    - Audit logs tracking every event
    """
    conn = get_db()
    c = conn.cursor()

    # Disable foreign keys temporarily during schema restructuring
    c.execute("PRAGMA foreign_keys = OFF")
    # Enable WAL mode for performance and concurrency
    c.execute("PRAGMA journal_mode = WAL")

    # 1. Users table (Strictly 5 roles)
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        status TEXT DEFAULT 'ACTIVE',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Drop obsolete faculty table
    c.execute("DROP TABLE IF EXISTS faculty")

    # 2. Departments table
    c.execute('''CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        code TEXT UNIQUE NOT NULL,
        status TEXT DEFAULT 'ACTIVE',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # Remove obsolete departments not belonging to SRCW
    c.execute("""
        DELETE FROM departments 
        WHERE name NOT IN ('Commerce', 'Computer Science', 'Business Administration', 'Department of Biochemistry', 'Department of Microbiology')
    """)


    # 3. Courses table (Institutional Courses linked to Departments)
    c.execute('''CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        code TEXT NOT NULL,
        duration_years INTEGER DEFAULT 3,
        status TEXT DEFAULT 'ACTIVE',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (department_id) REFERENCES departments(id),
        UNIQUE(department_id, name)
    )''')

    # Check and upgrade classes table if it is outdated
    cls_cols = [col[1] for col in c.execute("PRAGMA table_info(classes)").fetchall()]
    if cls_cols and 'course_id' not in cls_cols:
        c.execute("DROP TABLE classes")

    # 4. Classes table (Single Class per Year per Course - NO SECTIONS)
    c.execute('''CREATE TABLE IF NOT EXISTS classes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department_id INTEGER NOT NULL,
        course_id INTEGER NOT NULL,
        year INTEGER NOT NULL,
        class_name TEXT NOT NULL,
        tutor_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (department_id) REFERENCES departments(id),
        FOREIGN KEY (course_id) REFERENCES courses(id),
        FOREIGN KEY (tutor_id) REFERENCES users(id),
        UNIQUE(department_id, course_id, year)
    )''')

    # Check and upgrade students table if it has legacy section or missing columns
    s_cols = [col[1] for col in c.execute("PRAGMA table_info(students)").fetchall()]
    if s_cols and 'section' in s_cols:
        c.execute("DROP TABLE students")

    # 5. Students table (NO SECTIONS)
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        register_number TEXT UNIQUE NOT NULL,
        department_id INTEGER,
        course_id INTEGER,
        year INTEGER NOT NULL,
        class_id INTEGER,
        email TEXT,
        phone TEXT,
        parent_phone TEXT,
        student_type TEXT DEFAULT 'Day Scholar',
        photo TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (department_id) REFERENCES departments(id),
        FOREIGN KEY (course_id) REFERENCES courses(id),
        FOREIGN KEY (class_id) REFERENCES classes(id)
    )''')


    # 6. Tutors table
    c.execute('''CREATE TABLE IF NOT EXISTS tutors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        employee_id TEXT UNIQUE NOT NULL,
        department_id INTEGER,
        designation TEXT,
        phone TEXT,
        email TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (department_id) REFERENCES departments(id)
    )''')

    # 7. HODs table
    c.execute('''CREATE TABLE IF NOT EXISTS hods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        employee_id TEXT UNIQUE NOT NULL,
        department_id INTEGER,
        phone TEXT,
        email TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (department_id) REFERENCES departments(id)
    )''')
    h_cols = [col[1] for col in c.execute("PRAGMA table_info(hods)").fetchall()]
    if 'designation' not in h_cols:
        c.execute("ALTER TABLE hods ADD COLUMN designation TEXT DEFAULT 'Head of Department'")

    # 8. Guards table
    c.execute('''CREATE TABLE IF NOT EXISTS guards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        employee_id TEXT UNIQUE NOT NULL,
        shift TEXT DEFAULT 'General',
        phone TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    g_cols = [col[1] for col in c.execute("PRAGMA table_info(guards)").fetchall()]
    if 'created_at' not in g_cols:
        c.execute("ALTER TABLE guards ADD COLUMN created_at TIMESTAMP DEFAULT NULL")


    # 9. Gate Pass Requests (No return columns, starts at PENDING_TUTOR)
    c.execute('''CREATE TABLE IF NOT EXISTS gate_pass_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        destination TEXT NOT NULL,
        request_date TEXT NOT NULL,
        exit_time TEXT NOT NULL,
        request_type TEXT DEFAULT 'Normal',
        remarks TEXT,
        status TEXT DEFAULT 'PENDING_TUTOR',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id)
    )''')
    gpr_info = c.execute("PRAGMA table_info(gate_pass_requests)").fetchall()
    gpr_cols = {col[1]: col for col in gpr_info}
    if 'expected_return_time' in gpr_cols:
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("""
            CREATE TABLE gate_pass_requests_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                destination TEXT NOT NULL,
                request_date TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                request_type TEXT DEFAULT 'Normal',
                remarks TEXT,
                status TEXT DEFAULT 'PENDING_TUTOR',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id)
            )
        """)
        c.execute("""
            INSERT INTO gate_pass_requests_new (id, student_id, reason, destination, request_date, exit_time, request_type, remarks, status, created_at, updated_at)
            SELECT id, student_id, reason, destination, request_date, exit_time, request_type, remarks,
                   CASE WHEN status = 'PENDING_FACULTY' THEN 'PENDING_TUTOR' ELSE status END,
                   created_at, updated_at
            FROM gate_pass_requests
        """)
        c.execute("DROP TABLE gate_pass_requests")
        c.execute("ALTER TABLE gate_pass_requests_new RENAME TO gate_pass_requests")
        c.execute("PRAGMA foreign_keys = ON")

    # 10. Approvals (Tutor & HOD only)
    c.execute('''CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        approver_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        decision TEXT NOT NULL,
        remarks TEXT,
        approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (request_id) REFERENCES gate_pass_requests(id),
        FOREIGN KEY (approver_id) REFERENCES users(id)
    )''')

    # 11. Gate Passes
    c.execute('''CREATE TABLE IF NOT EXISTS gate_passes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER UNIQUE NOT NULL,
        pass_code TEXT UNIQUE NOT NULL,
        qr_data TEXT NOT NULL,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP DEFAULT NULL,
        FOREIGN KEY (request_id) REFERENCES gate_pass_requests(id)
    )''')
    gp_info = c.execute("PRAGMA table_info(gate_passes)").fetchall()
    gp_cols = {col[1]: col for col in gp_info}
    if gp_cols.get('expires_at') and gp_cols['expires_at'][3] == 1:
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("""
            CREATE TABLE gate_passes_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER UNIQUE NOT NULL,
                pass_code TEXT UNIQUE NOT NULL,
                qr_data TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP DEFAULT NULL,
                FOREIGN KEY (request_id) REFERENCES gate_pass_requests(id)
            )
        """)
        c.execute("""
            INSERT INTO gate_passes_new (id, request_id, pass_code, qr_data, generated_at, expires_at)
            SELECT id, request_id, pass_code, qr_data, generated_at, expires_at
            FROM gate_passes
        """)
        c.execute("DROP TABLE gate_passes")
        c.execute("ALTER TABLE gate_passes_new RENAME TO gate_passes")
        c.execute("PRAGMA foreign_keys = ON")

    # 12. Gate Exit Logs (Exit Only - No Return)
    c.execute('''CREATE TABLE IF NOT EXISTS gate_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        gate_guard_id INTEGER NOT NULL,
        actual_exit_time TIMESTAMP NOT NULL,
        exit_status TEXT DEFAULT 'EXITED',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (request_id) REFERENCES gate_pass_requests(id),
        FOREIGN KEY (gate_guard_id) REFERENCES users(id)
    )''')
    gl_info = c.execute("PRAGMA table_info(gate_logs)").fetchall()
    gl_cols = {col[1]: col for col in gl_info}
    if 'actual_return_time' in gl_cols:
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("""
            CREATE TABLE gate_logs_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                gate_guard_id INTEGER NOT NULL,
                actual_exit_time TIMESTAMP NOT NULL,
                exit_status TEXT DEFAULT 'EXITED',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES gate_pass_requests(id),
                FOREIGN KEY (gate_guard_id) REFERENCES users(id)
            )
        """)
        c.execute("""
            INSERT INTO gate_logs_new (id, request_id, gate_guard_id, actual_exit_time, exit_status, created_at)
            SELECT id, request_id, gate_guard_id, actual_exit_time, COALESCE(exit_status, 'EXITED'), COALESCE(created_at, actual_exit_time)
            FROM gate_logs
        """)
        c.execute("DROP TABLE gate_logs")
        c.execute("ALTER TABLE gate_logs_new RENAME TO gate_logs")
        c.execute("PRAGMA foreign_keys = ON")


    # 13. Audit Logs (Complete institutional event tracking)
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        action TEXT NOT NULL,
        request_id INTEGER,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # 14. Notifications
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    c.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()

def seed_srcw_data():
    """
    Seeds the exact 5 departments and 13 courses for
    Sri Ramakrishna College of Arts & Science for Women,
    plus single class per year, and default multi-user accounts.
    """
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 1. Exact 5 Departments
    srcw_departments = [
        ('Commerce', 'COM'),
        ('Computer Science', 'CS'),
        ('Business Administration', 'BBA'),
        ('Department of Biochemistry', 'BIOCHEM'),
        ('Department of Microbiology', 'MICROBIO')
    ]
    for name, code in srcw_departments:
        c.execute("""
            INSERT INTO departments (name, code, status, created_at)
            VALUES (?, ?, 'ACTIVE', ?)
            ON CONFLICT(name) DO UPDATE SET code=excluded.code, status='ACTIVE'
        """, (name, code, now_str))

    dept_map = {row['name']: row['id'] for row in c.execute("SELECT id, name FROM departments").fetchall()}

    # 2. Exact 13 Courses with configurable duration
    # Commerce: M.Com CA (2 yrs), B.Com A&F (3 yrs), B.Com PA (3 yrs), B.Com CA (3 yrs), B.Com (3 yrs)
    # Computer Science: CS (3 yrs), BCA (3 yrs), IT (3 yrs), DA (3 yrs), AI (3 yrs)
    # Business Administration: BBA (3 yrs)
    # Department of Biochemistry: Biochemistry (3 yrs)
    # Department of Microbiology: Microbiology (3 yrs)
    srcw_courses = [
        # Commerce
        (dept_map['Commerce'], 'M.Com CA', 'MCOM-CA', 2),
        (dept_map['Commerce'], 'B.Com A&F', 'BCOM-AF', 3),
        (dept_map['Commerce'], 'B.Com PA', 'BCOM-PA', 3),
        (dept_map['Commerce'], 'B.Com CA', 'BCOM-CA', 3),
        (dept_map['Commerce'], 'B.Com', 'BCOM', 3),
        # Computer Science
        (dept_map['Computer Science'], 'CS', 'BSC-CS', 3),
        (dept_map['Computer Science'], 'BCA', 'BCA', 3),
        (dept_map['Computer Science'], 'IT', 'BSC-IT', 3),
        (dept_map['Computer Science'], 'DA', 'BSC-DA', 3),
        (dept_map['Computer Science'], 'AI', 'BSC-AI', 3),
        # Business Administration
        (dept_map['Business Administration'], 'BBA', 'BBA', 3),
        # Biochemistry
        (dept_map['Department of Biochemistry'], 'Biochemistry', 'BSC-BC', 3),
        # Microbiology
        (dept_map['Department of Microbiology'], 'Microbiology', 'BSC-MB', 3)
    ]

    for dept_id, name, code, duration in srcw_courses:
        c.execute("""
            INSERT INTO courses (department_id, name, code, duration_years, status, created_at)
            VALUES (?, ?, ?, ?, 'ACTIVE', ?)
            ON CONFLICT(department_id, name) DO UPDATE SET
                code=excluded.code, duration_years=excluded.duration_years, status='ACTIVE'
        """, (dept_id, name, code, duration, now_str))

    course_rows = c.execute("SELECT id, department_id, name, duration_years FROM courses").fetchall()

    # 3. Create Exactly ONE Class per Year per Course (No sections!)
    year_labels = {1: 'I Year', 2: 'II Year', 3: 'III Year', 4: 'IV Year'}
    for course in course_rows:
        c_id = course['id']
        d_id = course['department_id']
        c_name = course['name']
        dur = course['duration_years']

        for yr in range(1, dur + 1):
            class_name = f"{c_name} {year_labels.get(yr, f'Year {yr}')}"
            c.execute("""
                INSERT INTO classes (department_id, course_id, year, class_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(department_id, course_id, year) DO UPDATE SET
                    class_name=excluded.class_name
            """, (d_id, c_id, yr, class_name, now_str))

    # 4. Seed Standard Professional Accounts
    # Passwords hashed with Werkzeug
    pw_admin = generate_password_hash('admin123')
    pw_tutor = generate_password_hash('tutor123')
    pw_hod = generate_password_hash('hod123')
    pw_guard = generate_password_hash('guard123')
    pw_student = generate_password_hash('student123')

    # Admin
    c.execute("""
        INSERT INTO users (username, email, password_hash, role, status, created_at)
        VALUES ('admin', 'admin@srcw.ac.in', ?, 'admin', 'ACTIVE', ?)
        ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role='admin', status='ACTIVE'
    """, (pw_admin, now_str))
    admin_uid = c.execute("SELECT id FROM users WHERE username='admin'").fetchone()['id']

    # Tutors (One sample per major department)
    tutors_data = [
        ('tutor_cs', 'tutor_cs@srcw.ac.in', 'Dr. Meena Krishnan', 'EMP-TUT-001', dept_map['Computer Science'], 'Assistant Professor & Class Tutor', '9876500001'),
        ('tutor_bca', 'tutor_bca@srcw.ac.in', 'Prof. Suresh Babu', 'EMP-TUT-002', dept_map['Computer Science'], 'Assistant Professor & Class Tutor', '9876500002'),
        ('tutor_com', 'tutor_com@srcw.ac.in', 'Dr. Radhika S.', 'EMP-TUT-003', dept_map['Commerce'], 'Associate Professor & Class Tutor', '9876500003'),
        ('tutor_bba', 'tutor_bba@srcw.ac.in', 'Prof. Anitha R.', 'EMP-TUT-004', dept_map['Business Administration'], 'Assistant Professor & Class Tutor', '9876500004'),
        ('tutor_bio', 'tutor_bio@srcw.ac.in', 'Dr. Priya V.', 'EMP-TUT-005', dept_map['Department of Biochemistry'], 'Assistant Professor & Class Tutor', '9876500005'),
    ]

    for uname, email, name, emp_id, dept_id, desig, phone in tutors_data:
        c.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, 'tutor', 'ACTIVE', ?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role='tutor', status='ACTIVE'
        """, (uname, email, pw_tutor, now_str))
        uid = c.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()['id']

        c.execute("""
            INSERT INTO tutors (user_id, name, employee_id, department_id, designation, phone, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                user_id=excluded.user_id, name=excluded.name, department_id=excluded.department_id,
                designation=excluded.designation, phone=excluded.phone, email=excluded.email
        """, (uid, name, emp_id, dept_id, desig, phone, email, now_str))

    # Assign Tutors to Sample Classes:
    # tutor_bca -> BCA I Year, BCA II Year, BCA III Year
    bca_course = c.execute("SELECT id FROM courses WHERE name='BCA'").fetchone()
    if bca_course:
        tutor_bca_uid = c.execute("SELECT id FROM users WHERE username='tutor_bca'").fetchone()['id']
        c.execute("UPDATE classes SET tutor_id=? WHERE course_id=?", (tutor_bca_uid, bca_course['id']))

    # tutor_cs -> CS I Year, CS II Year
    cs_course = c.execute("SELECT id FROM courses WHERE name='CS'").fetchone()
    if cs_course:
        tutor_cs_uid = c.execute("SELECT id FROM users WHERE username='tutor_cs'").fetchone()['id']
        c.execute("UPDATE classes SET tutor_id=? WHERE course_id=?", (tutor_cs_uid, cs_course['id']))

    # tutor_com -> B.Com CA classes
    bcomca_course = c.execute("SELECT id FROM courses WHERE name='B.Com CA'").fetchone()
    if bcomca_course:
        tutor_com_uid = c.execute("SELECT id FROM users WHERE username='tutor_com'").fetchone()['id']
        c.execute("UPDATE classes SET tutor_id=? WHERE course_id=?", (tutor_com_uid, bcomca_course['id']))

    # HODs (One per department)
    hods_data = [
        ('hod_cs', 'hod_cs@srcw.ac.in', 'Dr. Venkatesh R.', 'EMP-HOD-001', dept_map['Computer Science'], '9845011111'),
        ('hod_commerce', 'hod_com@srcw.ac.in', 'Dr. Saravanan K.', 'EMP-HOD-002', dept_map['Commerce'], '9845022222'),
        ('hod_bba', 'hod_bba@srcw.ac.in', 'Dr. Malini Devi', 'EMP-HOD-003', dept_map['Business Administration'], '9845033333'),
        ('hod_biochem', 'hod_biochem@srcw.ac.in', 'Dr. Geetha Mohan', 'EMP-HOD-004', dept_map['Department of Biochemistry'], '9845044444'),
        ('hod_microbio', 'hod_microbio@srcw.ac.in', 'Dr. Chitra Lakshmi', 'EMP-HOD-005', dept_map['Department of Microbiology'], '9845055555'),
    ]

    for uname, email, name, emp_id, dept_id, phone in hods_data:
        c.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, 'hod', 'ACTIVE', ?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role='hod', status='ACTIVE'
        """, (uname, email, pw_hod, now_str))
        uid = c.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()['id']

        c.execute("""
            INSERT INTO hods (user_id, name, employee_id, department_id, phone, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                user_id=excluded.user_id, name=excluded.name, department_id=excluded.department_id,
                phone=excluded.phone, email=excluded.email
        """, (uid, name, emp_id, dept_id, phone, email, now_str))

    # Gate Guards
    guards_data = [
        ('guard001', 'guard1@srcw.ac.in', 'Ravi Security Officer', 'SEC-001', 'General', '9700011111'),
        ('guard002', 'guard2@srcw.ac.in', 'Kumar Security Guard', 'SEC-002', 'Morning', '9700022222')
    ]

    for uname, email, name, emp_id, shift, phone in guards_data:
        c.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, 'guard', 'ACTIVE', ?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role='guard', status='ACTIVE'
        """, (uname, email, pw_guard, now_str))
        uid = c.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()['id']

        c.execute("""
            INSERT INTO guards (user_id, name, employee_id, shift, phone, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                user_id=excluded.user_id, name=excluded.name, shift=excluded.shift, phone=excluded.phone
        """, (uid, name, emp_id, shift, phone, now_str))

    # Sample Students (Mapped to SRCW departments and courses)
    # 1. Arun Kumar (BCA II Year)
    # 2. Priya CS (CS III Year)
    # 3. Kaviya (B.Com CA I Year)
    bca_class_2 = c.execute("SELECT id FROM classes WHERE course_id=? AND year=2", (bca_course['id'],)).fetchone() if bca_course else None
    cs_class_3 = c.execute("SELECT id FROM classes WHERE course_id=? AND year=3", (cs_course['id'],)).fetchone() if cs_course else None
    bcom_class_1 = c.execute("SELECT id FROM classes WHERE course_id=? AND year=1", (bcomca_course['id'],)).fetchone() if bcomca_course else None

    students_data = [
        ('arunkumar', 'arunkumar@student.srcw.ac.in', 'Arun Kumar R.', '22BCA101', dept_map['Computer Science'], bca_course['id'] if bca_course else None, 2, bca_class_2['id'] if bca_class_2 else None, '9876543210', '9876543219', 'Day Scholar'),
        ('priyacs', 'priya@student.srcw.ac.in', 'Priya S.', '21BCS204', dept_map['Computer Science'], cs_course['id'] if cs_course else None, 3, cs_class_3['id'] if cs_class_3 else None, '9876543211', '9876543218', 'Hosteller'),
        ('kaviyacom', 'kaviya@student.srcw.ac.in', 'Kaviya M.', '23BCC312', dept_map['Commerce'], bcomca_course['id'] if bcomca_course else None, 1, bcom_class_1['id'] if bcom_class_1 else None, '9876543212', '9876543217', 'Day Scholar'),
    ]

    for uname, email, name, regno, dept_id, course_id, yr, cls_id, phone, pphone, stype in students_data:
        c.execute("""
            INSERT INTO users (username, email, password_hash, role, status, created_at)
            VALUES (?, ?, ?, 'student', 'ACTIVE', ?)
            ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role='student', status='ACTIVE'
        """, (uname, email, pw_student, now_str))
        uid = c.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()['id']

        c.execute("""
            INSERT INTO students (user_id, name, register_number, department_id, course_id, year, class_id, email, phone, parent_phone, student_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(register_number) DO UPDATE SET
                user_id=excluded.user_id, name=excluded.name, department_id=excluded.department_id,
                course_id=excluded.course_id, year=excluded.year, class_id=excluded.class_id,
                email=excluded.email, phone=excluded.phone, parent_phone=excluded.parent_phone,
                student_type=excluded.student_type
        """, (uid, name, regno, dept_id, course_id, yr, cls_id, email, phone, pphone, stype, now_str))

    log_audit(conn, admin_uid, 'admin', 'SYSTEM_INITIALIZED', None, 'SRCW SmartGate Database Initialized')

    conn.commit()
    conn.close()
    print("SRCW Database migration and seeding completed successfully.")

def init_db():
    migrate_db()
    seed_srcw_data()

if __name__ == '__main__':
    init_db()
