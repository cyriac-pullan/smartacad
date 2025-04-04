from flask import Flask, render_template, request, redirect, url_for, session, flash,jsonify
import sqlite3
import os
from datetime import date
import google.generativeai as genai
from flask import Response
from datetime import date, datetime, timedelta  # Add timedelta to the import
from ktu_question_generator import ktu_question_bp

# Configure Gemini (add this after app.secret_key)
genai.configure(api_key='AIzaSyBlbLAmVB6yExlIr8WPkwxpmdLHu0VFgb0')
model = genai.GenerativeModel('gemini-1.5-pro-latest')

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a real secret key

app.register_blueprint(ktu_question_bp)

def get_db_connection():
    """Create a connection to the SQLite database"""
    db_path = os.path.join(os.path.dirname(__file__), 'db.sqlite3')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# LOGIN ROUTES
@app.route('/')
def index():
    """Home page route"""
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login route to handle user authentication"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM info_user WHERE username = ?', (username,)).fetchone()
        
        if user:
            # Check if the user is a student
            student = conn.execute('SELECT * FROM info_student WHERE user_id = ?', (user['id'],)).fetchone()
            
            # Check if the user is a teacher
            teacher = conn.execute('SELECT * FROM info_teacher WHERE user_id = ?', (user['id'],)).fetchone()
            
            # Simplified password check - replace with proper hashing in production
            if user['password'] == password:
                # Store user info in session
                session['user_id'] = user['id']
                session['username'] = user['username']
                
                # Redirect based on user type
                if student:
                    session['user_type'] = 'student'
                    session['student_usn'] = student['USN']
                    conn.close()
                    return redirect(url_for('student_dashboard'))
                elif teacher:
                    session['user_type'] = 'teacher'
                    session['teacher_id'] = teacher['id']
                    conn.close()
                    return redirect(url_for('teacher_dashboard'))
                else:
                    conn.close()
                    return "User not found in student or teacher tables"
            else:
                conn.close()
                return "Invalid password"
        
        conn.close()
        return "User not found"
    
    return render_template('login.html')

@app.route('/student/dashboard')
def student_dashboard():
    """Student dashboard route"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    # Fetch student details
    student = conn.execute('''
        SELECT s.*, c.section, c.sem, d.name as dept_name 
        FROM info_student s
        JOIN info_class c ON s.class_id_id = c.id
        JOIN info_dept d ON c.dept_id = d.id
        WHERE s.USN = ?
    ''', (session['student_usn'],)).fetchone()
    
    # Fetch student's courses
    courses = conn.execute('''
        SELECT c.name, c.shortname 
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_dashboard.html', 
                           student=student, 
                           courses=courses)

@app.route('/teacher/dashboard')
def teacher_dashboard():
    """Teacher dashboard route"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    # Fetch teacher details
    teacher = conn.execute('''
        SELECT t.*, d.name as dept_name 
        FROM info_teacher t
        JOIN info_dept d ON t.dept_id = d.id
        WHERE t.id = ?
    ''', (session['teacher_id'],)).fetchone()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT c.name, c.shortname, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    conn.close()
    
    return render_template('teacher_dashboard.html', 
                           teacher=teacher, 
                           assigned_courses=assigned_courses)

@app.route('/logout')
def logout():
    """Logout route to clear session"""
    session.clear()
    return redirect(url_for('index'))

# ATTENDANCE ROUTES
@app.route('/teacher/mark-attendance', methods=['GET', 'POST'])
def mark_attendance():
    """Route for teachers to mark attendance"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned classes and courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        attendance_date = request.form['attendance_date']
        
        # Get all students for this class
        students = conn.execute('''
            SELECT s.USN 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            WHERE a.id = ?
        ''', (assign_id,)).fetchall()
        
        # Get list of present students (those explicitly checked)
        present_students = request.form.getlist('students')
        
        try:
            # Begin transaction
            conn.execute('BEGIN')
            
            # Create an attendance class record
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO info_attendanceclass (date, status, assign_id) 
                VALUES (?, 1, ?)
            ''', (attendance_date, assign_id))
            attendanceclass_id = cursor.lastrowid
            
            # Fetch course details for the selected assignment
            course_details = conn.execute('''
                SELECT c.id as course_id, cl.section 
                FROM info_assign a
                JOIN info_course c ON a.course_id = c.id
                JOIN info_class cl ON a.class_id_id = cl.id
                WHERE a.id = ?
            ''', (assign_id,)).fetchone()
            
            # Mark attendance for ALL students
            for student in students:
                # Check if this student is in the present_students list
                status = 1 if student['USN'] in present_students else 0
                
                conn.execute('''
                    INSERT INTO info_attendance 
                    (date, status, attendanceclass_id, course_id, student_id) 
                    VALUES (?, ?, ?, ?, ?)
                ''', (attendance_date, status, attendanceclass_id, course_details['course_id'], student['USN']))
            
            # Commit transaction
            conn.commit()
            flash('Attendance marked successfully!', 'success')
            return redirect(url_for('mark_attendance'))
        
        except Exception as e:
            conn.rollback()
            flash(f'Error marking attendance: {str(e)}', 'error')
    
    # Fetch students for the first assigned course/class
    students = []
    if assigned_courses:
        first_assign = assigned_courses[0]
        students = conn.execute('''
            SELECT s.USN, s.name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            WHERE c.section = ? AND c.sem = ?
            ORDER BY s.name
        ''', (first_assign['section'], first_assign['sem'])).fetchall()
    
    conn.close()
    
    return render_template('mark_attendance.html', 
                           assigned_courses=assigned_courses, 
                           students=students)
@app.route('/teacher/view-attendance', methods=['GET', 'POST'])
def view_attendance():
    """Route for teachers to view attendance records"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    attendance_records = []
    selected_course = None
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        
        # Fetch course details
        course_details = conn.execute('''
            SELECT c.id as course_id, c.name as course_name, 
                   cl.section, cl.sem 
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            JOIN info_class cl ON a.class_id_id = cl.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        # Fetch attendance records
        attendance_records = conn.execute('''
            SELECT s.USN, s.name, 
                   COUNT(CASE WHEN a.status = 1 THEN 1 END) as present_days,
                   COUNT(*) as total_days,
                   ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as attendance_percentage
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            LEFT JOIN info_attendance a ON s.USN = a.student_id
            LEFT JOIN info_attendanceclass ac ON a.attendanceclass_id = ac.id
            LEFT JOIN info_assign assign ON ac.assign_id = assign.id
            WHERE c.section = ? AND c.sem = ? AND assign.id = ?
            GROUP BY s.USN, s.name
            ORDER BY s.name
        ''', (course_details['section'], course_details['sem'], assign_id)).fetchall()
        
        selected_course = course_details
    
    conn.close()
    
    return render_template('view_attendance.html', 
                           assigned_courses=assigned_courses, 
                           attendance_records=attendance_records,
                           selected_course=selected_course)

@app.route('/student/attendance')
def student_attendance():
    """Route for students to view their attendance"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch all courses for the student
    all_courses = conn.execute('''
        SELECT c.id as course_id, c.name as course_name
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    # Fetch student's course-wise attendance
    attendance_records = conn.execute('''
        SELECT c.id as course_id, 
               c.name as course_name, 
               COALESCE(COUNT(CASE WHEN a.status = 1 THEN 1 END), 0) as present_days,
               COALESCE(COUNT(a.id), 0) as total_days,
               COALESCE(ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(a.id), 2), 0) as attendance_percentage
        FROM info_course c
        LEFT JOIN info_attendance a ON c.id = a.course_id AND a.student_id = ?
        WHERE c.id IN (
            SELECT course_id 
            FROM info_studentcourse 
            WHERE student_id = ?
        )
        GROUP BY c.id, c.name
        ORDER BY c.name
    ''', (session['student_usn'], session['student_usn'])).fetchall()
    
    # Fetch detailed attendance record
    detailed_attendance = conn.execute('''
        SELECT c.name as course_name, 
               a.date, 
               a.status
        FROM info_attendance a
        JOIN info_course c ON a.course_id = c.id
        WHERE a.student_id = ?
        ORDER BY a.date DESC
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_attendance.html', 
                           attendance_records=attendance_records,
                           detailed_attendance=detailed_attendance)
@app.route('/teacher/mark-attendance/get-students', methods=['GET'])
def get_students_for_course():
    """Route to fetch students for a specific course and class"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify([]), 403
    
    assign_id = request.args.get('assign_id')
    
    if not assign_id:
        return jsonify([]), 400
    
    conn = get_db_connection()
    
    try:
        # Fetch course and class details for the assignment
        course_details = conn.execute('''
            SELECT c.section, c.sem
            FROM info_assign a
            JOIN info_class c ON a.class_id_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify([]), 404
        
        # Fetch students for the specific section and semester
        students = conn.execute('''
            SELECT USN, name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            WHERE c.section = ? AND c.sem = ?
            ORDER BY s.name
        ''', (course_details['section'], course_details['sem'])).fetchall()
        
        # Convert to list of dictionaries
        student_list = [dict(student) for student in students]
        
        return jsonify(student_list)
    
    except Exception as e:
        print(f"Error fetching students: {e}")
        return jsonify([]), 500
    finally:
        conn.close()
@app.route('/student/timetable')
def student_timetable():
    """Route for students to view their timetable"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch student's class details
    student_class = conn.execute('''
        SELECT s.class_id_id, c.section, c.sem 
        FROM info_student s
        JOIN info_class c ON s.class_id_id = c.id
        WHERE s.USN = ?
    ''', (session['student_usn'],)).fetchone()
    
    # Fetch timetable for the student's class
    timetable = conn.execute('''
        SELECT 
            at.day, 
            at.period, 
            c.name as course_name, 
            t.name as teacher_name
        FROM info_assigntime at
        JOIN info_assign a ON at.assign_id = a.id
        JOIN info_course c ON a.course_id = c.id
        JOIN info_teacher t ON a.teacher_id = t.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE cl.id = ?
        ORDER BY 
            CASE at.day 
                WHEN 'Monday' THEN 1 
                WHEN 'Tuesday' THEN 2 
                WHEN 'Wednesday' THEN 3 
                WHEN 'Thursday' THEN 4 
                WHEN 'Friday' THEN 5 
                WHEN 'Saturday' THEN 6 
                ELSE 7 
            END,
            at.period
    ''', (student_class['class_id_id'],)).fetchall()
    
    conn.close()
    
    # Organize timetable by day
    organized_timetable = {}
    for row in timetable:
        if row['day'] not in organized_timetable:
            organized_timetable[row['day']] = []
        organized_timetable[row['day']].append(row)
    
    return render_template('student_timetable.html', 
                           timetable=organized_timetable,
                           student_class=student_class)

@app.route('/teacher/timetable')
def teacher_timetable():
    """Route for teachers to view their timetable"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's timetable
    timetable = conn.execute('''
        SELECT 
            at.day, 
            at.period, 
            c.name as course_name, 
            cl.section,
            cl.sem
        FROM info_assigntime at
        JOIN info_assign a ON at.assign_id = a.id
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
        ORDER BY 
            CASE at.day 
                WHEN 'Monday' THEN 1 
                WHEN 'Tuesday' THEN 2 
                WHEN 'Wednesday' THEN 3 
                WHEN 'Thursday' THEN 4 
                WHEN 'Friday' THEN 5 
                WHEN 'Saturday' THEN 6 
                ELSE 7 
            END,
            at.period
    ''', (session['teacher_id'],)).fetchall()
    
    conn.close()
    
    # Organize timetable by day
    organized_timetable = {}
    for row in timetable:
        if row['day'] not in organized_timetable:
            organized_timetable[row['day']] = []
        organized_timetable[row['day']].append(row)
    
    return render_template('teacher_timetable.html', 
                           timetable=organized_timetable)

@app.route('/teacher/enter-marks', methods=['GET', 'POST'])
def enter_marks():
    """Route for teachers to enter marks"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, 
               cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    students = []
    selected_course = None
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        marks_name = request.form['marks_name']
        
        # Fetch students for this course/class
        students = conn.execute('''
            SELECT s.USN, s.name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            WHERE a.id = ?
            ORDER BY s.name
        ''', (assign_id,)).fetchall()
        
        # Get course details
        selected_course = conn.execute('''
            SELECT c.id as course_id, c.name as course_name, 
                   cl.section, cl.sem 
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            JOIN info_class cl ON a.class_id_id = cl.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
    conn.close()
    
    return render_template('enter_marks.html', 
                           assigned_courses=assigned_courses, 
                           students=students,
                           selected_course=selected_course)

@app.route('/teacher/get-existing-marks', methods=['GET'])
def get_existing_marks():
    """Route to fetch existing marks for a specific course and assessment type"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify([]), 403
    
    assign_id = request.args.get('assign_id')
    marks_name = request.args.get('marks_name')
    
    if not assign_id or not marks_name:
        return jsonify([]), 400
    
    conn = get_db_connection()
    
    try:
        # Get the course_id for this assignment
        course_details = conn.execute('''
            SELECT c.id as course_id
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify([]), 404
        
        # Fetch existing marks for this course and assessment type
        existing_marks = conn.execute('''
            SELECT s.USN as student_usn, 
                   m.marks1 as marks
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            LEFT JOIN info_studentcourse sc ON (sc.student_id = s.USN AND sc.course_id = ?)
            LEFT JOIN info_marks m ON (m.studentcourse_id = sc.id AND m.name = ?)
            WHERE a.id = ?
            ORDER BY s.name
        ''', (course_details['course_id'], marks_name, assign_id)).fetchall()
        
        # Convert to list of dictionaries
        marks_list = [dict(mark) for mark in existing_marks]
        
        return jsonify(marks_list)
    
    except Exception as e:
        print(f"Error fetching existing marks: {e}")
        return jsonify([]), 500
    finally:
        conn.close()

@app.route('/teacher/save-marks', methods=['POST'])
def save_marks():
    """Route to save marks for students with enhanced debugging"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    try:
        course_id = request.form.get('course_id')
        marks_name = request.form.get('marks_name')
        
        print(f"Saving marks for course_id: {course_id}, marks_name: {marks_name}")
        print(f"Form data received: {request.form}")
        
        if not course_id or not marks_name:
            flash('Missing required fields', 'error')
            return redirect(url_for('enter_marks'))
        
        # Begin transaction
        conn.execute('BEGIN')
        
        # Process each student's marks
        for key, value in request.form.items():
            if key.startswith('marks_'):
                usn = key.split('_')[1]
                marks = value or '0'
                
                print(f"Processing marks for USN: {usn}, Marks: {marks}")
                
                # Find or create studentcourse record
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO info_studentcourse (course_id, student_id)
                    VALUES (?, ?)
                ''', (course_id, usn))
                
                # Get studentcourse_id
                studentcourse = conn.execute('''
                    SELECT id FROM info_studentcourse 
                    WHERE course_id = ? AND student_id = ?
                ''', (course_id, usn)).fetchone()
                
                if not studentcourse:
                    raise ValueError(f"Failed to get studentcourse_id for USN: {usn}")
                
                # Insert or update marks
                cursor.execute('''
                    INSERT OR REPLACE INTO info_marks 
                    (name, marks1, studentcourse_id) 
                    VALUES (?, ?, ?)
                ''', (marks_name, marks, studentcourse['id']))
                
                print(f"Successfully saved marks for USN {usn}")
        
        # Commit transaction
        conn.commit()
        flash('Marks saved successfully!', 'success')
        return redirect(url_for('enter_marks'))
    
    except Exception as e:
        conn.rollback()
        print(f"Error saving marks: {str(e)}")
        flash(f'Error saving marks: {str(e)}', 'error')
        return redirect(url_for('enter_marks'))
    finally:
        conn.close()
    
    

@app.route('/student/marks')
def student_marks():
    """Route for students to view their marks"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch all courses for the student
    all_courses = conn.execute('''
        SELECT DISTINCT c.id as course_id, c.name as course_name
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    # Fetch student's marks for all courses
    marks_records = conn.execute('''
        SELECT 
            c.id as course_id,
            c.name as course_name, 
            m.name as marks_name,
            m.marks1 as marks
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        JOIN info_marks m ON sc.id = m.studentcourse_id
        WHERE sc.student_id = ?
        ORDER BY c.name, m.name
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_marks.html', 
                           marks_records=marks_records,
                           all_courses=all_courses)
@app.route('/teacher/get-course-details', methods=['GET'])
def get_course_details():
    """Route to fetch course details for a specific assignment"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify({}), 403
    
    assign_id = request.args.get('assign_id')
    
    if not assign_id:
        return jsonify({}), 400
    
    conn = get_db_connection()
    
    try:
        # Fetch course details for this assignment
        course_details = conn.execute('''
            SELECT c.id as course_id
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify({}), 404
        
        return jsonify(dict(course_details))
    
    except Exception as e:
        print(f"Error fetching course details: {e}")
        return jsonify({}), 500
    finally:
        conn.close()
@app.route('/chat')
def chat_interface():
    """Chat interface route"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html')

@app.route('/chat/send', methods=['POST'])
def chat_send():
    """Handle chat messages with precise database access"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_message = request.json.get('message', '').lower()
    if not user_message:
        return jsonify({'error': 'No message provided'}), 400
    
    try:
        conn = get_db_connection()
        response = ""
        
        # Get user context
        user_context = ""
        if session.get('user_type') == 'student':
            student = conn.execute('''
                SELECT s.*, c.section, c.sem, d.name as dept_name 
                FROM info_student s
                JOIN info_class c ON s.class_id_id = c.id
                JOIN info_dept d ON c.dept_id = d.id
                WHERE s.USN = ?
            ''', (session['student_usn'],)).fetchone()
            user_context = f"Student {student['name']} (USN: {student['USN']}), {student['dept_name']} Dept, Semester {student['sem']}"
            
        elif session.get('user_type') == 'teacher':
            teacher = conn.execute('''
                SELECT t.*, d.name as dept_name 
                FROM info_teacher t
                JOIN info_dept d ON t.dept_id = d.id
                WHERE t.id = ?
            ''', (session['teacher_id'],)).fetchone()
            user_context = f"Teacher {teacher['name']}, {teacher['dept_name']} Dept"
        # TIMETABLE QUERIES
        if any(word in user_message for word in ['timetable', 'schedule', 'period', 'today', 'tomorrow', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']):
            day = None
            for d in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']:
                if d in user_message:
                    day = d.capitalize()
                    break
            
            if 'today' in user_message:
                day = date.today().strftime('%A')
            elif 'tomorrow' in user_message:
                day = (date.today() + timedelta(days=1)).strftime('%A')
            
            if session.get('user_type') == 'student':
                # Get student's actual timetable
                timetable = conn.execute('''
                    SELECT at.day, at.period, c.name as course_name, t.name as teacher_name
                    FROM info_assigntime at
                    JOIN info_assign a ON at.assign_id = a.id
                    JOIN info_course c ON a.course_id = c.id
                    JOIN info_teacher t ON a.teacher_id = t.id
                    JOIN info_student s ON a.class_id_id = s.class_id_id
                    WHERE s.USN = ? AND (at.day = ? OR ? IS NULL)
                    ORDER BY 
                        CASE at.day 
                            WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 
                            WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 
                            WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 
                            ELSE 7 END,
                        at.period
                ''', (session['student_usn'], day, day)).fetchall()
                
                if timetable:
                    if day:
                        response = f"📅 Your timetable for {day}:\n\n"
                    else:
                        response = "📅 Your weekly timetable:\n\n"
                    
                    current_day = None
                    for row in timetable:
                        if row['day'] != current_day:
                            response += f"📌 {row['day']}:\n"
                            current_day = row['day']
                        response += f"⏰ Period {row['period']}: {row['course_name']} (Prof. {row['teacher_name']})\n"
                else:
                    response = "No timetable found for the specified day." if day else "No timetable data found in the database."
            
            elif session.get('user_type') == 'teacher':
                # Get teacher's actual timetable
                timetable = conn.execute('''
                    SELECT at.day, at.period, c.name as course_name, cl.section, cl.sem
                    FROM info_assigntime at
                    JOIN info_assign a ON at.assign_id = a.id
                    JOIN info_course c ON a.course_id = c.id
                    JOIN info_class cl ON a.class_id_id = cl.id
                    WHERE a.teacher_id = ? AND (at.day = ? OR ? IS NULL)
                    ORDER BY 
                        CASE at.day 
                            WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 
                            WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 
                            WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 
                            ELSE 7 END,
                        at.period
                ''', (session['teacher_id'], day, day)).fetchall()
                
                if timetable:
                    if day:
                        response = f"📅 Your timetable for {day}:\n\n"
                    else:
                        response = "📅 Your weekly timetable:\n\n"
                    
                    current_day = None
                    for row in timetable:
                        if row['day'] != current_day:
                            response += f"📌 {row['day']}:\n"
                            current_day = row['day']
                        response += f"⏰ Period {row['period']}: {row['course_name']} for {row['section']} (Sem {row['sem']})\n"
                else:
                    response = "No timetable found for the specified day." if day else "No timetable data found in the database."

        # ATTENDANCE QUERIES
        elif any(word in user_message for word in ['attendance', 'present', 'absent']):
            course_name = None
            for word in user_message.split():
                course = conn.execute('SELECT name FROM info_course WHERE LOWER(name) LIKE ?', (f'%{word}%',)).fetchone()
                if course:
                    course_name = course['name']
                    break
            
            if session.get('user_type') == 'student':
                if course_name:
                    # Get attendance for specific course
                    attendance = conn.execute('''
                        SELECT COUNT(CASE WHEN a.status = 1 THEN 1 END) as present,
                               COUNT(*) as total,
                               ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as percentage
                        FROM info_attendance a
                        JOIN info_course c ON a.course_id = c.id
                        WHERE a.student_id = ? AND c.name = ?
                    ''', (session['student_usn'], course_name)).fetchone()
                    
                    if attendance and attendance['total'] > 0:
                        response = f"📊 Your attendance for {course_name}:\n\n"
                        response += f"✅ Present: {attendance['present']} days\n"
                        response += f"❌ Absent: {attendance['total'] - attendance['present']} days\n"
                        response += f"📈 Percentage: {attendance['percentage']}%\n"
                        response += f"📅 Total classes: {attendance['total']}"
                    else:
                        response = f"No attendance records found for {course_name}."
                else:
                    # Get all attendance
                    attendance = conn.execute('''
                        SELECT c.name as course_name,
                               COUNT(CASE WHEN a.status = 1 THEN 1 END) as present,
                               COUNT(*) as total,
                               ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as percentage
                        FROM info_attendance a
                        JOIN info_course c ON a.course_id = c.id
                        WHERE a.student_id = ?
                        GROUP BY c.name
                        ORDER BY c.name
                    ''', (session['student_usn'],)).fetchall()
                    
                    if attendance:
                        response = "📊 Your attendance summary:\n\n"
                        for row in attendance:
                            response += f"📚 {row['course_name']}:\n"
                            response += f"  ✅ Present: {row['present']} days\n"
                            response += f"  ❌ Absent: {row['total'] - row['present']} days\n"
                            response += f"  📈 Percentage: {row['percentage']}%\n"
                            response += f"  📅 Total classes: {row['total']}\n\n"
                    else:
                        response = "No attendance records found in the database."
            
            elif session.get('user_type') == 'teacher':
                response = "You can view and manage attendance through the attendance dashboard."

        # MARKS QUERIES
        elif any(word in user_message for word in ['mark', 'marks', 'grade', 'grades', 'score']):
            course_name = None
            assessment_name = None
            student_name = None
            
            # Try to detect course name
            for word in user_message.split():
                course = conn.execute('SELECT name FROM info_course WHERE LOWER(name) LIKE ?', (f'%{word}%',)).fetchone()
                if course:
                    course_name = course['name']
                    break
            
            # Try to detect assessment name
            for word in user_message.split():
                if word in ['quiz', 'test', 'exam', 'midterm', 'final', 'assignment', 'project']:
                    assessment_name = word.capitalize()
                    break
            
            # Try to detect student name (for teacher queries)
            if session.get('user_type') == 'teacher':
                for word in user_message.split():
                    student = conn.execute('SELECT name, USN FROM info_student WHERE LOWER(name) LIKE ?', (f'%{word}%',)).fetchone()
                    if student:
                        student_name = student['name']
                        student_usn = student['USN']
                        break
            
            if session.get('user_type') == 'student':
                if course_name and assessment_name:
                    # Get specific mark
                    mark = conn.execute('''
                        SELECT m.marks1 as mark
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ? AND c.name = ? AND m.name = ?
                    ''', (session['student_usn'], course_name, assessment_name)).fetchone()
                    
                    if mark:
                        response = f"📝 Your marks for {assessment_name} in {course_name}: {mark['mark']}"
                    else:
                        response = f"No marks found for {assessment_name} in {course_name}."
                elif course_name:
                    # Get all marks for course
                    marks = conn.execute('''
                        SELECT m.name as assessment, m.marks1 as mark
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ? AND c.name = ?
                        ORDER BY m.name
                    ''', (session['student_usn'], course_name)).fetchall()
                    
                    if marks:
                        response = f"📝 Your marks for {course_name}:\n\n"
                        for row in marks:
                            response += f"📌 {row['assessment']}: {row['mark']}\n"
                    else:
                        response = f"No marks found for {course_name}."
                else:
                    # Get all marks
                    marks = conn.execute('''
                        SELECT c.name as course_name, m.name as assessment, m.marks1 as mark
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ?
                        ORDER BY c.name, m.name
                    ''', (session['student_usn'],)).fetchall()
                    
                    if marks:
                        response = "📝 Your marks summary:\n\n"
                        current_course = None
                        for row in marks:
                            if row['course_name'] != current_course:
                                response += f"📚 {row['course_name']}:\n"
                                current_course = row['course_name']
                            response += f"  📌 {row['assessment']}: {row['mark']}\n"
                    else:
                        response = "No marks records found in the database."
            
            elif session.get('user_type') == 'teacher':
                if student_name and course_name and assessment_name:
                    # Get specific student's mark for specific assessment
                    mark = conn.execute('''
                        SELECT m.marks1 as mark
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        JOIN info_student s ON sc.student_id = s.USN
                        WHERE s.USN = ? AND c.name = ? AND m.name = ?
                    ''', (student_usn, course_name, assessment_name)).fetchone()
                    
                    if mark:
                        response = f"📝 Marks for {student_name} ({student_usn}) in {course_name} for {assessment_name}: {mark['mark']}"
                    else:
                        response = f"No marks found for {student_name} in {course_name} for {assessment_name}."
                
                elif student_name and course_name:
                    # Get all marks for a student in a course
                    marks = conn.execute('''
                        SELECT m.name as assessment, m.marks1 as mark
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        JOIN info_student s ON sc.student_id = s.USN
                        WHERE s.USN = ? AND c.name = ?
                        ORDER BY m.name
                    ''', (student_usn, course_name)).fetchall()
                    
                    if marks:
                        response = f"📝 Marks for {student_name} ({student_usn}) in {course_name}:\n\n"
                        for row in marks:
                            response += f"📌 {row['assessment']}: {row['mark']}\n"
                    else:
                        response = f"No marks found for {student_name} in {course_name}."
                
                elif course_name and assessment_name:
                    # Get all student marks for a specific assessment in a course
                    marks = conn.execute('''
                        SELECT s.name as student_name, s.USN, m.marks1 as mark
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        JOIN info_student s ON sc.student_id = s.USN
                        JOIN info_assign a ON (a.course_id = c.id AND a.teacher_id = ?)
                        WHERE c.name = ? AND m.name = ?
                        ORDER BY s.name
                    ''', (session['teacher_id'], course_name, assessment_name)).fetchall()
                    
                    if marks:
                        response = f"📝 Marks for all students in {course_name} for {assessment_name}:\n\n"
                        for row in marks:
                            response += f"👨‍🎓 {row['student_name']} ({row['USN']}): {row['mark']}\n"
                    else:
                        response = f"No marks found for students in {course_name} for {assessment_name}."
                
                elif course_name:
                    # Get all student marks for a course
                    marks = conn.execute('''
                        SELECT s.name as student_name, s.USN, m.name as assessment, m.marks1 as mark
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        JOIN info_student s ON sc.student_id = s.USN
                        JOIN info_assign a ON (a.course_id = c.id AND a.teacher_id = ?)
                        WHERE c.name = ?
                        ORDER BY s.name, m.name
                    ''', (session['teacher_id'], course_name)).fetchall()
                    
                    if marks:
                        response = f"📝 Marks for all students in {course_name}:\n\n"
                        current_student = None
                        for row in marks:
                            if row['student_name'] != current_student:
                                response += f"👨‍🎓 {row['student_name']} ({row['USN']}):\n"
                                current_student = row['student_name']
                            response += f"  📌 {row['assessment']}: {row['mark']}\n"
                    else:
                        response = f"No marks found for students in {course_name}."
                
                else:
                    # General marks information for teacher
                    response = "You can query marks by:\n"
                    response += "- Asking about a specific student's marks (e.g., 'What are John's marks?')\n"
                    response += "- Asking about marks for a specific course (e.g., 'Show marks for Database Systems')\n"
                    response += "- Asking about a specific assessment (e.g., 'Show midterm marks for my class')"

        # CLASS INFORMATION FOR TEACHERS
        elif any(word in user_message for word in ['class', 'classes', 'students']) and session.get('user_type') == 'teacher':
            course_name = None
            for word in user_message.split():
                course = conn.execute('SELECT name FROM info_course WHERE LOWER(name) LIKE ?', (f'%{word}%',)).fetchone()
                if course:
                    course_name = course['name']
                    break
            
            if course_name:
                # Get students in a specific class/course
                students = conn.execute('''
                    SELECT s.USN, s.name
                    FROM info_student s
                    JOIN info_class c ON s.class_id_id = c.id
                    JOIN info_assign a ON c.id = a.class_id_id
                    JOIN info_course co ON a.course_id = co.id
                    WHERE co.name = ? AND a.teacher_id = ?
                    ORDER BY s.name
                ''', (course_name, session['teacher_id'])).fetchall()
                
                if students:
                    response = f"👨‍🎓 Students in your {course_name} class:\n\n"
                    for student in students:
                        response += f"- {student['name']} ({student['USN']})\n"
                else:
                    response = f"No students found for your {course_name} class."
            else:
                # Get all classes taught by teacher
                classes = conn.execute('''
                    SELECT DISTINCT c.name as course_name, cl.section, cl.sem
                    FROM info_assign a
                    JOIN info_course c ON a.course_id = c.id
                    JOIN info_class cl ON a.class_id_id = cl.id
                    WHERE a.teacher_id = ?
                    ORDER BY cl.sem, cl.section, c.name
                ''', (session['teacher_id'],)).fetchall()
                
                if classes:
                    response = "📚 Classes you teach:\n\n"
                    for cls in classes:
                        response += f"- {cls['course_name']} (Sem {cls['sem']}, Section {cls['section']})\n"
                else:
                    response = "You are not currently assigned to any classes."

        # If not an academic query, use Gemini for general questions
        if not response:
            system_prompt = f"""
            You are an AI Academic Assistant for {user_context}.
            For academic queries like timetable, attendance, or marks, the system provides actual data from the database.
            For other questions, provide helpful information.
            """
            
            gemini_response = model.generate_content([system_prompt, user_message])
            response = gemini_response.text
        
        conn.close()
        return jsonify({'response': response})
    
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': str(e)}), 500
    
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': str(e)}), 500
# Add Server-Sent Events route for streaming
@app.route('/chat/stream')
def chat_stream():
    def generate():
        conn = get_db_connection()
        
        # Get user context (similar to chat_send)
        user_context = ""
        if session.get('user_type') == 'student':
            student = conn.execute('SELECT * FROM info_student WHERE USN = ?', (session['student_usn'],)).fetchone()
            user_context = f"Student: {student['name']} (USN: {student['USN']})"
        elif session.get('user_type') == 'teacher':
            teacher = conn.execute('SELECT * FROM info_teacher WHERE id = ?', (session['teacher_id'],)).fetchone()
            user_context = f"Teacher: {teacher['name']}"
        
        conn.close()
        
        system_prompt = f"""
        You are an AI Academic Assistant. {user_context}
        Answer academic and general questions helpfully.
        """
        
        user_message = request.args.get('message', '')
        response = model.generate_content([system_prompt, user_message], stream=True)
        
        for chunk in response:
            yield f"data: {chunk.text}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')
if __name__ == '__main__':
    app.run(debug=True)