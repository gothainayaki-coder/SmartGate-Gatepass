import os
import sys
import unittest
from datetime import datetime

# Set working directory to project root
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app
from database import get_db

class TestSRCWGatepassWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        cls.client = app.test_client()

    def test_01_public_pages(self):
        """Test all public institutional pages render with HTTP 200."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Sri Ramakrishna College of Arts & Science for Women", res.data)
        self.assertIn(b"SNR Sons Trust", res.data)

        res = self.client.get('/about')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Institution Profile", res.data)
        self.assertIn(b"single-use exit authorization", res.data)

        res = self.client.get('/login')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"SmartGate Unified Login", res.data)

        res = self.client.get('/register')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Student Portal Self-Registration", res.data)

    def test_02_dynamic_apis(self):
        """Test course & year lookup APIs."""
        db = get_db()
        dept = db.execute("SELECT id, name FROM departments WHERE code='CS'").fetchone()
        db.close()
        self.assertIsNotNone(dept)

        res = self.client.get(f"/api/courses?department_id={dept['id']}")
        self.assertEqual(res.status_code, 200)
        courses = res.get_json()
        self.assertIsInstance(courses, list)
        self.assertTrue(len(courses) >= 4)  # CS, BCA, IT, DA, AI

        bca = next(c for c in courses if c['code'] == 'BCA')
        res_years = self.client.get(f"/api/course/{bca['id']}/years")
        self.assertEqual(res_years.status_code, 200)
        self.assertEqual(res_years.get_json()['duration_years'], 3)

    def test_03_role_logins(self):
        """Test unified login for each of the 5 roles."""
        credentials = [
            ('admin', 'admin123', 'admin', '/admin/dashboard'),
            ('priyacs', 'student123', 'student', '/student/dashboard'),
            ('tutor_cs', 'tutor123', 'tutor', '/tutor/dashboard'),
            ('hod_cs', 'hod123', 'hod', '/hod/dashboard'),
            ('guard001', 'guard123', 'guard', '/guard/dashboard'),
        ]
        for username, password, expected_role, expected_redirect in credentials:
            with self.subTest(role=expected_role):
                # Ensure logged out
                self.client.get('/logout')
                res = self.client.post('/login', data={
                    'login_ident': username,
                    'password': password
                }, follow_redirects=False)
                self.assertEqual(res.status_code, 302)
                self.assertIn(expected_redirect, res.headers['Location'])

                # Check protected dashboard
                res_dash = self.client.get(expected_redirect)
                self.assertEqual(res_dash.status_code, 200)

        self.client.get('/logout')

    def test_04_student_registration(self):
        """Test public student self-registration."""
        self.client.get('/logout')
        db = get_db()
        dept = db.execute("SELECT id FROM departments WHERE code='CS'").fetchone()
        course = db.execute("SELECT id FROM courses WHERE code='BCA'").fetchone()
        db.close()

        test_reg = "24BCA999"
        test_uname = "test_student_bca"

        # Clean existing test student if present
        db = get_db()
        db.execute("DELETE FROM students WHERE register_number=?", (test_reg,))
        db.execute("DELETE FROM users WHERE username=?", (test_uname,))
        db.commit()
        db.close()

        res = self.client.post('/register', data={
            'name': 'BCA Test Student',
            'register_number': test_reg,
            'email': 'bca_test@srcw.ac.in',
            'username': test_uname,
            'password': 'password123',
            'confirm_password': 'password123',
            'department_id': dept['id'],
            'course_id': course['id'],
            'year': '1',
            'phone': '9876543210',
            'parent_phone': '9876543211',
            'student_type': 'Day Scholar'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Verify in DB
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (test_uname,)).fetchone()
        self.assertIsNotNone(user)
        self.assertEqual(user['role'], 'student')

        student = db.execute("SELECT * FROM students WHERE register_number=?", (test_reg,)).fetchone()
        self.assertIsNotNone(student)
        self.assertEqual(student['year'], 1)
        self.assertIsNotNone(student['class_id'], "Class ID must be automatically resolved")
        db.close()

    def test_05_complete_gatepass_workflow(self):
        """
        Full unidirectional gatepass lifecycle:
        Student Request -> Tutor Approval -> HOD Approval -> Guard Scan -> COMPLETED
        """
        # Step 1: Login as student Priya (CS Department, Year 3)
        self.client.get('/logout')
        res_login = self.client.post('/login', data={
            'login_ident': 'priyacs',
            'password': 'student123'
        }, follow_redirects=True)
        self.assertEqual(res_login.status_code, 200)

        # Clean any existing requests for priyacs with full foreign key cascading
        db = get_db()
        stu = db.execute("SELECT s.id FROM students s JOIN users u ON s.user_id = u.id WHERE u.username='priyacs'").fetchone()
        today_date = datetime.now().strftime('%Y-%m-%d')
        if stu:
            req_rows = db.execute("SELECT id FROM gate_pass_requests WHERE student_id=?", (stu['id'],)).fetchall()
            for r in req_rows:
                db.execute("DELETE FROM gate_logs WHERE request_id=?", (r['id'],))
                db.execute("DELETE FROM gate_passes WHERE request_id=?", (r['id'],))
                db.execute("DELETE FROM approvals WHERE request_id=?", (r['id'],))
                db.execute("DELETE FROM audit_logs WHERE request_id=?", (r['id'],))
            db.execute("DELETE FROM gate_pass_requests WHERE student_id=?", (stu['id'],))
            db.commit()
        db.close()

        # Step 2: Student creates exit request (NO RETURN TIME)
        res_req = self.client.post('/student/new-request', data={
            'reason_cat': 'Medical',
            'reason_detail': 'Severe headache and doctor consultation at clinic',
            'destination': 'Coimbatore Medical Center',
            'request_date': today_date,
            'exit_time': '11:45',
            'request_type': 'Normal'
        }, follow_redirects=True)
        self.assertEqual(res_req.status_code, 200)

        # Retrieve created request
        db = get_db()
        req = db.execute("""
            SELECT * FROM gate_pass_requests
            WHERE student_id=? AND request_date=?
            ORDER BY id DESC LIMIT 1
        """, (stu['id'], today_date)).fetchone()
        self.assertIsNotNone(req)
        req_id = req['id']
        self.assertEqual(req['status'], 'PENDING_TUTOR')
        db.close()

        # Step 3: Tutor reviews and recommends to HOD
        self.client.get('/logout')
        res_tutor_login = self.client.post('/login', data={
            'login_ident': 'tutor_cs',
            'password': 'tutor123'
        }, follow_redirects=True)
        self.assertEqual(res_tutor_login.status_code, 200)

        res_tutor_approve = self.client.post(f'/tutor/request/{req_id}', data={
            'action': 'approve',
            'remarks': 'Spoke with mother, permission granted for clinic visit.'
        }, follow_redirects=True)
        self.assertEqual(res_tutor_approve.status_code, 200)

        # Check status is now PENDING_HOD
        db = get_db()
        req_after_tutor = db.execute("SELECT status FROM gate_pass_requests WHERE id=?", (req_id,)).fetchone()
        self.assertEqual(req_after_tutor['status'], 'PENDING_HOD')
        db.close()

        # Step 4: HOD reviews and approves (Generates QR Code & Pass Code)
        self.client.get('/logout')
        res_hod_login = self.client.post('/login', data={
            'login_ident': 'hod_cs',
            'password': 'hod123'
        }, follow_redirects=True)
        self.assertEqual(res_hod_login.status_code, 200)

        res_hod_approve = self.client.post(f'/hod/request/{req_id}', data={
            'action': 'approve',
            'remarks': 'Approved by Head of Department.'
        }, follow_redirects=True)
        self.assertEqual(res_hod_approve.status_code, 200)

        # Verify Pass generated and status is APPROVED
        db = get_db()
        req_after_hod = db.execute("SELECT status FROM gate_pass_requests WHERE id=?", (req_id,)).fetchone()
        self.assertEqual(req_after_hod['status'], 'APPROVED')

        pass_row = db.execute("SELECT * FROM gate_passes WHERE request_id=?", (req_id,)).fetchone()
        self.assertIsNotNone(pass_row)
        pass_code = pass_row['pass_code']
        self.assertTrue(pass_code.startswith('SRCW-'))
        db.close()

        # Step 5: Student views QR Pass
        self.client.get('/logout')
        self.client.post('/login', data={'login_ident': 'priyacs', 'password': 'student123'}, follow_redirects=True)
        res_pass = self.client.get(f'/student/pass/{req_id}')
        self.assertEqual(res_pass.status_code, 200)
        self.assertIn(pass_code.encode(), res_pass.data)
        self.assertIn(b"Sri Ramakrishna College of Arts & Science for Women", res_pass.data)

        # Step 6: Gate Guard scans QR / enters pass code and confirms exit
        self.client.get('/logout')
        res_guard_login = self.client.post('/login', data={
            'login_ident': 'guard001',
            'password': 'guard123'
        }, follow_redirects=True)
        self.assertEqual(res_guard_login.status_code, 200)

        # Guard looks up pass code
        res_lookup = self.client.get(f'/guard/verify?pass_code={pass_code}')
        self.assertEqual(res_lookup.status_code, 200)
        self.assertIn(b"Pass verified! Ready for exit authorization", res_lookup.data)

        # Guard confirms exit
        res_exit = self.client.post(f'/guard/confirm-exit/{req_id}', follow_redirects=True)
        self.assertEqual(res_exit.status_code, 200)

        # Step 7: Verify final status is COMPLETED (Unidirectional exit-only)
        db = get_db()
        final_req = db.execute("SELECT status FROM gate_pass_requests WHERE id=?", (req_id,)).fetchone()
        self.assertEqual(final_req['status'], 'COMPLETED')

        gate_log = db.execute("SELECT * FROM gate_logs WHERE request_id=?", (req_id,)).fetchone()
        self.assertIsNotNone(gate_log)
        self.assertIsNotNone(gate_log['actual_exit_time'])
        self.assertEqual(gate_log['exit_status'], 'EXITED')

        # Check audit trail
        audit = db.execute("SELECT action FROM audit_logs WHERE request_id=? ORDER BY id", (req_id,)).fetchall()
        actions = [a['action'] for a in audit]
        self.assertIn('REQUEST_CREATED', actions)
        self.assertIn('TUTOR_APPROVED', actions)
        self.assertIn('HOD_APPROVED', actions)
        self.assertIn('GATE_EXIT_VERIFIED', actions)
        self.assertIn('PASS_COMPLETED', actions)

        db.close()
        self.client.get('/logout')

    def test_06_admin_controllers(self):
        """Test all administrative management interfaces."""
        self.client.get('/logout')
        res_admin_login = self.client.post('/login', data={
            'login_ident': 'admin',
            'password': 'admin123'
        }, follow_redirects=True)
        self.assertEqual(res_admin_login.status_code, 200)

        routes = [
            '/admin/dashboard',
            '/admin/users',
            '/admin/departments',
            '/admin/courses',
            '/admin/classes',
            '/admin/tutor-assignment',
            '/admin/hod-assignment',
            '/admin/audit-logs',
            '/admin/reports',
        ]
        for route in routes:
            with self.subTest(route=route):
                res = self.client.get(route)
                self.assertEqual(res.status_code, 200, f"Failed on {route}")

        # Test CSV export
        res_csv = self.client.get('/admin/reports/csv')
        self.assertEqual(res_csv.status_code, 200)
        self.assertEqual(res_csv.mimetype, 'text/csv')

        self.client.get('/logout')

if __name__ == '__main__':
    unittest.main()
