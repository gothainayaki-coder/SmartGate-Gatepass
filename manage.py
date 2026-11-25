#!/usr/bin/env python
"""
SmartGate Management CLI
Sri Ramakrishna College of Arts and Science for Women (SNR Sons Trust)

Provides CLI commands including:
    python manage.py check      - System, database integrity, route, and environment validation
    python manage.py runserver  - Run the SmartGate web portal (optional port)
    python manage.py initdb     - Initialize / migrate SQLite database schema
"""

import sys
import os
import sqlite3

def run_system_check():
    """Execute comprehensive system, database, route, and template checks."""
    print("Performing system and database integrity checks...")
    issues = []

    # 1. Database Connection & File Existence
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
    if not os.path.exists(db_path):
        issues.append(f"Database file not found at {db_path}. Run 'python database.py' to initialize.")
    else:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # PRAGMA integrity_check
            cursor.execute("PRAGMA integrity_check;")
            integrity_result = cursor.fetchone()
            if not integrity_result or integrity_result[0] != "ok":
                issues.append(f"SQLite PRAGMA integrity check failed: {integrity_result}")

            # PRAGMA foreign_key_check
            cursor.execute("PRAGMA foreign_key_check;")
            fk_violations = cursor.fetchall()
            if fk_violations:
                issues.append(f"SQLite foreign key check violations detected: {len(fk_violations)} issues.")

            # Validate Required Institutional Tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cursor.fetchall()}
            required_tables = [
                'users', 'students', 'tutors', 'hods', 'guards',
                'departments', 'courses', 'classes',
                'gate_pass_requests', 'approvals', 'gate_passes', 'gate_logs',
                'audit_logs', 'notifications'
            ]
            for t in required_tables:
                if t not in tables:
                    issues.append(f"Missing required table: '{t}'")

            # Validate 5 Academic Departments
            if 'departments' in tables:
                cursor.execute("SELECT COUNT(*) FROM departments;")
                dept_count = cursor.fetchone()[0]
                if dept_count < 5:
                    issues.append(f"Expected at least 5 departments, found {dept_count}.")

            # Validate Columns in HODs (designation)
            if 'hods' in tables:
                cursor.execute("PRAGMA table_info(hods);")
                hod_cols = {col[1] for col in cursor.fetchall()}
                if 'designation' not in hod_cols:
                    issues.append("Missing 'designation' column in 'hods' table.")

            conn.close()
        except Exception as e:
            issues.append(f"Database error during check: {str(e)}")

    # 2. Flask Application & Blueprint/Routes Check
    try:
        from app import app
        required_endpoints = [
            'index', 'login', 'logout',
            'register', 'register_student', 'register_faculty', 'register_hod',
            'student_dashboard', 'tutor_dashboard', 'hod_dashboard',
            'guard_dashboard', 'admin_dashboard',
            'admin_approve_user', 'admin_reject_user'
        ]
        with app.app_context():
            rules = [rule.endpoint for rule in app.url_map.iter_rules()]
            for ep in required_endpoints:
                if ep not in rules:
                    issues.append(f"Missing required route endpoint: '{ep}'")
    except Exception as e:
        issues.append(f"Flask application check failed: {str(e)}")

    # 3. Print Results in Standard Format
    if issues:
        print("\n[!] System check identified issues:")
        for idx, iss in enumerate(issues, 1):
            print(f"  {idx}. {iss}")
        sys.exit(1)
    else:
        print("System check identified no issues (0 silenced).")
        sys.exit(0)

def run_server(port=5000):
    """Run the Flask development server."""
    from app import app
    print(f"Starting SmartGate Web Server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=True)

def init_db():
    """Initialize or migrate the database."""
    import database
    database.init_db()
    print("Database initialization / migration complete.")

def print_help():
    print("""SmartGate CLI Tool (manage.py)
Usage:
    python manage.py check         Validate database, tables, routes, and environment
    python manage.py runserver     Launch local development server (default port 5000)
    python manage.py initdb        Initialize or verify database tables
""")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == 'check':
        run_system_check()
    elif cmd == 'runserver':
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
        run_server(port)
    elif cmd in ('initdb', 'migrate'):
        init_db()
    elif cmd in ('help', '--help', '-h'):
        print_help()
    else:
        print(f"Unknown command: '{cmd}'. Available: check, runserver, initdb")
        sys.exit(1)
