"""
OSI Stress Assessment — Flask App v4  (SQLite backend)
=======================================================
Replaces all CSV file I/O with a single SQLite database: osi.db
Three tables:
  users          — credentials & registration info
  demographics   — one row per user (upsert on re-submit)
  stress_results — one row per user (upsert on re-submit)

Everything else (routes, Gemini chat, keyword fallback, templates)
is identical to the original app.py.
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json, os, hashlib, sqlite3, urllib.request, urllib.error
from datetime import datetime
from contextlib import contextmanager

app = Flask(__name__)
app.secret_key = 'osi_stress_2024_secure'

# ── Database path ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, 'osi.db')

# ── Gemini API Key ────────────────────────────────────────────────────────────
GEMINI_API_KEY = 'AIzaSyAHjvTD1xHxnCcFkiSppjJRiDkPNIjPGRY'

# ── Load MODEL TOP 3 once at startup ─────────────────────────────────────────
MODEL_TOP3_PATH = os.path.join(BASE_DIR, 'model_top3_subscales.pkl')
try:
    import joblib
    MODEL_TOP3 = joblib.load(MODEL_TOP3_PATH)
    print(f"[OSI] Loaded model top 3 from pkl: {MODEL_TOP3}")
except Exception:
    MODEL_TOP3 = ['Sub-Scale I', 'Sub-Scale III', 'Sub-Scale XI']
    print(f"[OSI] model_top3_subscales.pkl not found — using default: {MODEL_TOP3}")


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE SETUP
# ══════════════════════════════════════════════════════════════════════════════

@contextmanager
def get_db():
    """Yield a sqlite3 connection; commit on success, rollback on error."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe for concurrent reads
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't already exist."""
    with get_db() as conn:
        conn.executescript("""
        -- ── users ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS users (
            username        TEXT PRIMARY KEY,
            password        TEXT NOT NULL,
            fullname        TEXT NOT NULL,
            email           TEXT NOT NULL,
            registered      TEXT NOT NULL
        );

        -- ── demographics ──────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS demographics (
            username        TEXT PRIMARY KEY REFERENCES users(username),
            timestamp       TEXT,
            full_name       TEXT,
            email           TEXT,
            contact         TEXT,
            institute       TEXT,
            teaching_level  TEXT,
            gender          TEXT,
            marital_status  TEXT,
            age_group       TEXT,
            education       TEXT,
            designation     TEXT,
            employment_type TEXT,
            experience      TEXT,
            tenure          TEXT
        );

        -- ── stress_results ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS stress_results (
            username        TEXT PRIMARY KEY REFERENCES users(username),
            timestamp       TEXT,
            total_score     INTEGER,
            overall_level   TEXT,
            assessment_type TEXT,

            -- 12 subscales × 3 columns
            sub1  TEXT, sub1_score  INTEGER, sub1_level  TEXT,
            sub2  TEXT, sub2_score  INTEGER, sub2_level  TEXT,
            sub3  TEXT, sub3_score  INTEGER, sub3_level  TEXT,
            sub4  TEXT, sub4_score  INTEGER, sub4_level  TEXT,
            sub5  TEXT, sub5_score  INTEGER, sub5_level  TEXT,
            sub6  TEXT, sub6_score  INTEGER, sub6_level  TEXT,
            sub7  TEXT, sub7_score  INTEGER, sub7_level  TEXT,
            sub8  TEXT, sub8_score  INTEGER, sub8_level  TEXT,
            sub9  TEXT, sub9_score  INTEGER, sub9_level  TEXT,
            sub10 TEXT, sub10_score INTEGER, sub10_level TEXT,
            sub11 TEXT, sub11_score INTEGER, sub11_level TEXT,
            sub12 TEXT, sub12_score INTEGER, sub12_level TEXT,

            -- personal top-3 concern areas
            top1_subscale TEXT, top1_label TEXT, top1_score INTEGER, top1_level TEXT,
            top2_subscale TEXT, top2_label TEXT, top2_score INTEGER, top2_level TEXT,
            top3_subscale TEXT, top3_label TEXT, top3_score INTEGER, top3_level TEXT,

            -- model top-3 (fixed from pkl)
            model_top1 TEXT, model_top2 TEXT, model_top3 TEXT
        );
        """)
    print(f"[OSI] Database ready at {DB_PATH}")


# ══════════════════════════════════════════════════════════════════════════════
#  DB HELPER FUNCTIONS  (replace the old CSV helpers)
# ══════════════════════════════════════════════════════════════════════════════

def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()


def get_user(username):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def create_user(username, password, fullname, email):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password, fullname, email, registered) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, hash_pw(password), fullname, email, ts)
        )


def get_prev(username):
    """Return (stress_dict, demo_dict) for a user, or (None, None)."""
    with get_db() as conn:
        s = conn.execute(
            "SELECT * FROM stress_results WHERE username = ?", (username,)
        ).fetchone()
        d = conn.execute(
            "SELECT * FROM demographics WHERE username = ?", (username,)
        ).fetchone()
    return (dict(s) if s else None), (dict(d) if d else None)


def upsert_demographics(username, demo: dict):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as conn:
        conn.execute("""
            INSERT INTO demographics
                (username, timestamp, full_name, email, contact, institute,
                 teaching_level, gender, marital_status, age_group,
                 education, designation, employment_type, experience, tenure)
            VALUES
                (:username, :timestamp, :full_name, :email, :contact, :institute,
                 :teaching_level, :gender, :marital_status, :age_group,
                 :education, :designation, :employment_type, :experience, :tenure)
            ON CONFLICT(username) DO UPDATE SET
                timestamp       = excluded.timestamp,
                full_name       = excluded.full_name,
                email           = excluded.email,
                contact         = excluded.contact,
                institute       = excluded.institute,
                teaching_level  = excluded.teaching_level,
                gender          = excluded.gender,
                marital_status  = excluded.marital_status,
                age_group       = excluded.age_group,
                education       = excluded.education,
                designation     = excluded.designation,
                employment_type = excluded.employment_type,
                experience      = excluded.experience,
                tenure          = excluded.tenure
        """, {
            'username':        username,
            'timestamp':       ts,
            'full_name':       demo.get('full_name', ''),
            'email':           demo.get('email', ''),
            'contact':         demo.get('contact', ''),
            'institute':       demo.get('institute', ''),
            'teaching_level':  demo.get('teaching_level', ''),
            'gender':          demo.get('gender', ''),
            'marital_status':  demo.get('marital_status', ''),
            'age_group':       demo.get('age_group', ''),
            'education':       demo.get('education', ''),
            'designation':     demo.get('designation', ''),
            'employment_type': demo.get('employment_type', ''),
            'experience':      demo.get('experience', ''),
            'tenure':          demo.get('tenure', ''),
        })


def upsert_stress(username, stress: dict, model_top3: list):
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    subs = stress.get('subscales', [])
    top3 = stress.get('top3', [])

    def sub(i, key):
        return subs[i-1].get(key, '') if i <= len(subs) else ''

    def top(i, key):
        return top3[i-1].get(key, '') if i <= len(top3) else ''

    with get_db() as conn:
        conn.execute("""
            INSERT INTO stress_results (
                username, timestamp, total_score, overall_level, assessment_type,
                sub1,  sub1_score,  sub1_level,
                sub2,  sub2_score,  sub2_level,
                sub3,  sub3_score,  sub3_level,
                sub4,  sub4_score,  sub4_level,
                sub5,  sub5_score,  sub5_level,
                sub6,  sub6_score,  sub6_level,
                sub7,  sub7_score,  sub7_level,
                sub8,  sub8_score,  sub8_level,
                sub9,  sub9_score,  sub9_level,
                sub10, sub10_score, sub10_level,
                sub11, sub11_score, sub11_level,
                sub12, sub12_score, sub12_level,
                top1_subscale, top1_label, top1_score, top1_level,
                top2_subscale, top2_label, top2_score, top2_level,
                top3_subscale, top3_label, top3_score, top3_level,
                model_top1, model_top2, model_top3
            ) VALUES (
                :username, :timestamp, :total_score, :overall_level, :assessment_type,
                :sub1,  :sub1_score,  :sub1_level,
                :sub2,  :sub2_score,  :sub2_level,
                :sub3,  :sub3_score,  :sub3_level,
                :sub4,  :sub4_score,  :sub4_level,
                :sub5,  :sub5_score,  :sub5_level,
                :sub6,  :sub6_score,  :sub6_level,
                :sub7,  :sub7_score,  :sub7_level,
                :sub8,  :sub8_score,  :sub8_level,
                :sub9,  :sub9_score,  :sub9_level,
                :sub10, :sub10_score, :sub10_level,
                :sub11, :sub11_score, :sub11_level,
                :sub12, :sub12_score, :sub12_level,
                :top1_subscale, :top1_label, :top1_score, :top1_level,
                :top2_subscale, :top2_label, :top2_score, :top2_level,
                :top3_subscale, :top3_label, :top3_score, :top3_level,
                :model_top1, :model_top2, :model_top3
            )
            ON CONFLICT(username) DO UPDATE SET
                timestamp       = excluded.timestamp,
                total_score     = excluded.total_score,
                overall_level   = excluded.overall_level,
                assessment_type = excluded.assessment_type,
                sub1  = excluded.sub1,  sub1_score  = excluded.sub1_score,  sub1_level  = excluded.sub1_level,
                sub2  = excluded.sub2,  sub2_score  = excluded.sub2_score,  sub2_level  = excluded.sub2_level,
                sub3  = excluded.sub3,  sub3_score  = excluded.sub3_score,  sub3_level  = excluded.sub3_level,
                sub4  = excluded.sub4,  sub4_score  = excluded.sub4_score,  sub4_level  = excluded.sub4_level,
                sub5  = excluded.sub5,  sub5_score  = excluded.sub5_score,  sub5_level  = excluded.sub5_level,
                sub6  = excluded.sub6,  sub6_score  = excluded.sub6_score,  sub6_level  = excluded.sub6_level,
                sub7  = excluded.sub7,  sub7_score  = excluded.sub7_score,  sub7_level  = excluded.sub7_level,
                sub8  = excluded.sub8,  sub8_score  = excluded.sub8_score,  sub8_level  = excluded.sub8_level,
                sub9  = excluded.sub9,  sub9_score  = excluded.sub9_score,  sub9_level  = excluded.sub9_level,
                sub10 = excluded.sub10, sub10_score = excluded.sub10_score, sub10_level = excluded.sub10_level,
                sub11 = excluded.sub11, sub11_score = excluded.sub11_score, sub11_level = excluded.sub11_level,
                sub12 = excluded.sub12, sub12_score = excluded.sub12_score, sub12_level = excluded.sub12_level,
                top1_subscale = excluded.top1_subscale, top1_label = excluded.top1_label,
                top1_score    = excluded.top1_score,    top1_level = excluded.top1_level,
                top2_subscale = excluded.top2_subscale, top2_label = excluded.top2_label,
                top2_score    = excluded.top2_score,    top2_level = excluded.top2_level,
                top3_subscale = excluded.top3_subscale, top3_label = excluded.top3_label,
                top3_score    = excluded.top3_score,    top3_level = excluded.top3_level,
                model_top1    = excluded.model_top1,
                model_top2    = excluded.model_top2,
                model_top3    = excluded.model_top3
        """, {
            'username':       username,
            'timestamp':      ts,
            'total_score':    stress.get('total_score', 0),
            'overall_level':  stress.get('overall_level', ''),
            'assessment_type':stress.get('assessment_type', 'advanced'),
            'sub1':  sub(1,'name'),  'sub1_score':  sub(1,'score'),  'sub1_level':  sub(1,'level'),
            'sub2':  sub(2,'name'),  'sub2_score':  sub(2,'score'),  'sub2_level':  sub(2,'level'),
            'sub3':  sub(3,'name'),  'sub3_score':  sub(3,'score'),  'sub3_level':  sub(3,'level'),
            'sub4':  sub(4,'name'),  'sub4_score':  sub(4,'score'),  'sub4_level':  sub(4,'level'),
            'sub5':  sub(5,'name'),  'sub5_score':  sub(5,'score'),  'sub5_level':  sub(5,'level'),
            'sub6':  sub(6,'name'),  'sub6_score':  sub(6,'score'),  'sub6_level':  sub(6,'level'),
            'sub7':  sub(7,'name'),  'sub7_score':  sub(7,'score'),  'sub7_level':  sub(7,'level'),
            'sub8':  sub(8,'name'),  'sub8_score':  sub(8,'score'),  'sub8_level':  sub(8,'level'),
            'sub9':  sub(9,'name'),  'sub9_score':  sub(9,'score'),  'sub9_level':  sub(9,'level'),
            'sub10': sub(10,'name'), 'sub10_score': sub(10,'score'), 'sub10_level': sub(10,'level'),
            'sub11': sub(11,'name'), 'sub11_score': sub(11,'score'), 'sub11_level': sub(11,'level'),
            'sub12': sub(12,'name'), 'sub12_score': sub(12,'score'), 'sub12_level': sub(12,'level'),
            'top1_subscale': top(1,'name'), 'top1_label': top(1,'label'),
            'top1_score':    top(1,'score'), 'top1_level': top(1,'level'),
            'top2_subscale': top(2,'name'), 'top2_label': top(2,'label'),
            'top2_score':    top(2,'score'), 'top2_level': top(2,'level'),
            'top3_subscale': top(3,'name'), 'top3_label': top(3,'label'),
            'top3_score':    top(3,'score'), 'top3_level': top(3,'level'),
            'model_top1': model_top3[0] if len(model_top3) > 0 else '',
            'model_top2': model_top3[1] if len(model_top3) > 1 else '',
            'model_top3': model_top3[2] if len(model_top3) > 2 else '',
        })


# ══════════════════════════════════════════════════════════════════════════════
#  MIGRATION HELPER  — import existing CSV data into SQLite on first run
# ══════════════════════════════════════════════════════════════════════════════

def migrate_csv_to_db():
    """
    One-time import of data/users.csv, demographics.csv, stress_results.csv
    into the SQLite database.  Safe to call on every startup — skips rows
    that are already present.
    """
    import csv

    DATA_DIR    = os.path.join(BASE_DIR, 'data')
    LOGIN_FILE  = os.path.join(DATA_DIR, 'users.csv')
    DEMO_FILE   = os.path.join(DATA_DIR, 'demographics.csv')
    STRESS_FILE = os.path.join(DATA_DIR, 'stress_results.csv')

    def read(path):
        if not os.path.exists(path):
            return []
        with open(path, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    with get_db() as conn:
        # users
        for r in read(LOGIN_FILE):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO users "
                    "(username, password, fullname, email, registered) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (r['username'], r['password'],
                     r.get('fullname',''), r.get('email',''),
                     r.get('registered',''))
                )
            except Exception as e:
                print(f"[migrate] user skip: {e}")

        # demographics
        for r in read(DEMO_FILE):
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO demographics
                    (username,timestamp,full_name,email,contact,institute,
                     teaching_level,gender,marital_status,age_group,
                     education,designation,employment_type,experience,tenure)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (r.get('username'),r.get('timestamp'),r.get('full_name'),
                      r.get('email'),r.get('contact'),r.get('institute'),
                      r.get('teaching_level'),r.get('gender'),r.get('marital_status'),
                      r.get('age_group'),r.get('education'),r.get('designation'),
                      r.get('employment_type'),r.get('experience'),r.get('tenure')))
            except Exception as e:
                print(f"[migrate] demo skip: {e}")

        # stress_results — wide table, map all columns directly
        cols = [
            'username','timestamp','total_score','overall_level','assessment_type',
            'sub1','sub1_score','sub1_level','sub2','sub2_score','sub2_level',
            'sub3','sub3_score','sub3_level','sub4','sub4_score','sub4_level',
            'sub5','sub5_score','sub5_level','sub6','sub6_score','sub6_level',
            'sub7','sub7_score','sub7_level','sub8','sub8_score','sub8_level',
            'sub9','sub9_score','sub9_level','sub10','sub10_score','sub10_level',
            'sub11','sub11_score','sub11_level','sub12','sub12_score','sub12_level',
            'top1_subscale','top1_label','top1_score','top1_level',
            'top2_subscale','top2_label','top2_score','top2_level',
            'top3_subscale','top3_label','top3_score','top3_level',
            'model_top1','model_top2','model_top3'
        ]
        placeholders = ','.join('?' * len(cols))
        for r in read(STRESS_FILE):
            try:
                conn.execute(
                    f"INSERT OR IGNORE INTO stress_results ({','.join(cols)}) "
                    f"VALUES ({placeholders})",
                    [r.get(c, '') for c in cols]
                )
            except Exception as e:
                print(f"[migrate] stress skip: {e}")

    print("[OSI] CSV → SQLite migration complete.")


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI CHAT  (unchanged from original)
# ══════════════════════════════════════════════════════════════════════════════

def build_system_prompt(stress_data, demo_data):
    name        = demo_data.get('full_name', 'the user') if demo_data else 'the user'
    designation = demo_data.get('designation', 'an academician') if demo_data else 'an academician'
    experience  = demo_data.get('experience', 'unknown') if demo_data else 'unknown'
    total_score = stress_data.get('total_score', 'unknown') if stress_data else 'unknown'
    overall_lvl = stress_data.get('overall_level', 'unknown') if stress_data else 'unknown'
    assess_type = stress_data.get('assessment_type', 'advanced') if stress_data else 'advanced'

    subscale_info = ''
    if stress_data:
        sub_lines = []
        for i in range(1, 13):
            sn = stress_data.get(f'sub{i}', '')
            sc = stress_data.get(f'sub{i}_score', '')
            sl = stress_data.get(f'sub{i}_level', '')
            if sn and sc and sl:
                sub_lines.append(f"  - {sn}: Score={sc}, Level={sl}")
        subscale_info = '\n'.join(sub_lines)

    top3_info = ''
    if stress_data:
        top_lines = []
        for i in range(1, 4):
            t_name  = stress_data.get(f'top{i}_subscale', '')
            t_label = stress_data.get(f'top{i}_label', '')
            t_score = stress_data.get(f'top{i}_score', '')
            t_level = stress_data.get(f'top{i}_level', '')
            if t_name:
                top_lines.append(f"  #{i}: {t_name} ({t_label}) — Score={t_score}, Level={t_level}")
        top3_info = '\n'.join(top_lines)

    return f"""You are an expert occupational stress counsellor and psychologist specialising in the Occupational Stress Index (OSI) by Srivastava & Singh (1981). You are embedded in an OSI stress assessment web application used by academicians in India.

YOUR ROLE:
- Provide empathetic, evidence-based guidance on occupational stress
- Answer questions about the user's OSI assessment results
- Give practical, actionable coping strategies
- Explain OSI sub-scales and what scores mean
- Recommend professional help when stress is High

STRICT RULES:
- ONLY answer questions related to: occupational stress, mental health at work, OSI scores and sub-scales, burnout, work-life balance, coping strategies, mindfulness, sleep, exercise, anxiety, and general well-being
- If asked ANYTHING unrelated, politely say: "I'm only able to help with occupational stress and well-being topics."
- Never provide medical diagnoses
- Always recommend professional help for High stress or severe symptoms
- Keep responses concise (3-5 sentences max) unless a detailed explanation is needed
- Be warm, empathetic and encouraging

USER'S OSI ASSESSMENT DATA:
- Name: {name}
- Designation: {designation}
- Experience: {experience}
- Assessment Type: {assess_type}
- Total Stress Score: {total_score}
- Overall Stress Level: {overall_lvl}
- Assessment Scale: {'12-60 (Basic screening)' if assess_type == 'basic' else '46-230 (Full OSI)'}

SUBSCALE SCORES:
{subscale_info if subscale_info else '  (No assessment data available yet)'}

PERSONAL TOP 3 HIGH-STRESS AREAS:
{top3_info if top3_info else '  (No assessment data available yet)'}

OSI SCORE INTERPRETATION:
- Full OSI: Low=46-122, Moderate=123-155, High=156-230
- Sub-scale levels: Low, Moderate, High based on normative ranges

HELPLINES (India):
- iCall-TISS: 9152987821
- Vandrevala Foundation: 1860-2662-345
- NIMHANS: 080-46110007
- Fortis Helpline: 8376804102

Use the user's actual scores to give personalised, specific advice."""


def call_gemini(user_message, conversation_history, system_prompt):
    if not GEMINI_API_KEY or GEMINI_API_KEY == 'PASTE_YOUR_GEMINI_API_KEY_HERE':
        return None, "API key not configured"

    contents = []
    for turn in conversation_history:
        contents.append({"role": turn["role"], "parts": [{"text": turn["text"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 400, "topP": 0.9},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        data = json.dumps(payload).encode('utf-8')
        req  = urllib.request.Request(url, data=data,
                                      headers={'Content-Type': 'application/json'},
                                      method='POST')
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        reply = result['candidates'][0]['content']['parts'][0]['text']
        return reply.strip(), None
    except urllib.error.HTTPError as e:
        return None, f"API error {e.code}: {e.read().decode('utf-8')}"
    except Exception as e:
        return None, str(e)


KEYWORD_RESPONSES = [
    (['what is stress','define stress','meaning of stress'],
     "Stress is your body's natural response to pressure or demands. Short-term stress can boost performance, but chronic occupational stress leads to burnout, health problems, and reduced productivity."),
    (['osi','occupational stress index','srivastava'],
     "The Occupational Stress Index (OSI) by Srivastava & Singh (1981) measures work-related stress across 12 sub-scales. Total scores range from 46–230: Low (46–122), Moderate (123–155), High (156–230)."),
    (['my score','total score','stress score','what is my'],
     "Your stress score is shown on the StressOmeter above. Low: 46–122 | Moderate: 123–155 | High: 156–230. Retake the assessment to update your score."),
    (['low stress','score is low'],
     "Low stress means your occupational stress is well-managed. Maintain healthy routines, clear boundaries, regular exercise, and peer support to keep it that way. Great work!"),
    (['moderate stress','score is moderate'],
     "Moderate stress means some areas need attention. Identify your top stressors from the subscale table, practice mindfulness daily, and have an honest conversation about workload with your supervisor."),
    (['high stress','score is high','very stressed'],
     "High stress needs immediate attention. Please consult a mental health professional. Use the coping strategies in your report and reach out to iCall (9152987821) for support."),
    (['role overload','too much work','overloaded','workload'],
     "Role overload occurs when tasks exceed capacity. Try time-blocking your day, saying no to non-essential work, delegating where possible, and discussing realistic deadlines with your manager."),
    (['role ambiguity','unclear','confused about job','job expectations'],
     "Role ambiguity means unclear expectations. Ask your supervisor for written KPIs, schedule regular 1-on-1s, and document your key responsibilities to bring clarity."),
    (['role conflict','conflicting demands','contradictory'],
     "Role conflict happens when people pull you in different directions. Document conflicts, escalate to a neutral party, and establish priority frameworks with stakeholders."),
    (['powerless','no authority','no control'],
     "Powerlessness at work increases stress. Identify small areas where you can take ownership, and have a candid conversation with your manager about decision-making authority."),
    (['peer relation','colleague','coworker','team'],
     "Poor peer relations significantly increase stress. Try one-on-one conversations, active listening, and team-building to improve relationships."),
    (['burnout','burnt out','exhausted','exhaustion'],
     "Burnout is chronic stress that leads to exhaustion, cynicism, and reduced effectiveness. Please seek professional help immediately if you feel burnt out."),
    (['sleep','insomnia','cant sleep','not sleeping'],
     "Sleep is critical for stress management. Aim for 7–8 hours, keep a consistent bedtime, avoid screens 1 hour before sleep, and try relaxation techniques."),
    (['exercise','physical activity','workout','walk'],
     "Exercise is one of the most effective stress relievers. Even 20–30 minutes of walking or yoga significantly reduces cortisol and boosts mood-enhancing endorphins."),
    (['mindful','mindfulness','meditat'],
     "Mindfulness involves focusing on the present without judgment. Try 10 minutes of guided meditation or body scan daily. Apps like Headspace or Calm can help."),
    (['breath','breathing','breathe'],
     "Deep breathing activates your parasympathetic nervous system. Try box breathing: inhale 4 counts → hold 4 → exhale 4 → hold 4. Repeat 5 times."),
    (['anxiety','anxious','worried','worry'],
     "Anxiety and stress often go together. Ground yourself with the 5-4-3-2-1 technique: name 5 things you see, 4 you feel, 3 you hear, 2 you smell, 1 you taste."),
    (['helpline','help line','contact','call','number','support'],
     "Professional support helplines in India: iCall-TISS: 9152987821 | Vandrevala Foundation: 1860-2662-345 | NIMHANS: 080-46110007 | Fortis Helpline: 8376804102."),
    (['work life','work-life','balance'],
     "Work-life balance is essential. Set a firm end-of-work time, protect personal calendar time, take full lunch breaks, and disconnect during weekends."),
    (['salary','pay','compensation','underpaid'],
     "Feeling underpaid increases stress significantly. Research market benchmarks for your role and schedule a structured conversation with HR about compensation."),
    (['chatbot','who are you','what can you do','help'],
     "I'm your OSI Stress Assistant powered by AI! I can answer questions about your stress scores, subscales, burnout, coping strategies, mindfulness, sleep, exercise, and helplines. What would you like to know?"),
]

def keyword_fallback(msg):
    msg_lower = msg.lower()
    for keywords, reply in KEYWORD_RESPONSES:
        if any(kw in msg_lower for kw in keywords):
            return reply
    return "I can only answer questions related to occupational stress and the OSI assessment. Try asking about your score, burnout, mindfulness, sleep, exercise, specific subscales, or helplines."


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES  (logic identical to original; only data layer calls changed)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    if 'username' in session: return redirect(url_for('home'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    err = ''
    if request.method == 'POST':
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '').strip()
        user = get_user(u)
        if user and user['password'] == hash_pw(p):
            session['username'] = u
            session['fullname'] = user['fullname']
            return redirect(url_for('home'))
        err = 'Invalid username or password.'
    return render_template('login.html', error=err)


@app.route('/register', methods=['GET', 'POST'])
def register():
    err = ''
    if request.method == 'POST':
        u  = request.form.get('username', '').strip()
        p  = request.form.get('password', '').strip()
        p2 = request.form.get('confirm',  '').strip()
        fn = request.form.get('fullname', '').strip()
        em = request.form.get('email',    '').strip()
        if not u or not p or not fn or not em:
            err = 'All fields are required.'
        elif p != p2:
            err = 'Passwords do not match.'
        elif get_user(u):
            err = 'Username already exists. Please choose another.'
        else:
            create_user(u, p, fn, em)
            session['username'] = u
            session['fullname'] = fn
            return redirect(url_for('home'))
    return render_template('register.html', error=err)


@app.route('/home')
def home():
    if 'username' not in session: return redirect(url_for('login'))
    s, _ = get_prev(session['username'])
    return render_template('home.html',
        fullname=session['fullname'],
        username=session['username'],
        has_prev=(s is not None))


@app.route('/basic')
def basic():
    if 'username' not in session: return redirect(url_for('login'))
    return render_template('basic.html', fullname=session['fullname'])


@app.route('/advanced')
def advanced():
    if 'username' not in session: return redirect(url_for('login'))
    return render_template('advanced.html',
        fullname=session['fullname'],
        username=session['username'],
        model_top3=json.dumps(MODEL_TOP3))


@app.route('/result')
def result():
    if 'username' not in session: return redirect(url_for('login'))
    s, d = get_prev(session['username'])
    if not s: return redirect(url_for('home'))
    return render_template('result.html',
        fullname=session['fullname'],
        stress=s, demo=d,
        model_top3=MODEL_TOP3,
        assessment_type=s.get('assessment_type', 'advanced'))


@app.route('/previous')
def previous():
    if 'username' not in session: return redirect(url_for('login'))
    s, d = get_prev(session['username'])
    if not s: return redirect(url_for('home'))
    return render_template('result.html',
        fullname=session['fullname'],
        stress=s, demo=d,
        model_top3=MODEL_TOP3,
        assessment_type=s.get('assessment_type', 'advanced'),
        is_previous=True)


@app.route('/save', methods=['POST'])
def save():
    if 'username' not in session:
        return jsonify({'ok': False, 'msg': 'Not logged in'}), 401
    data  = request.get_json()
    uname = session['username']

    try:
        upsert_demographics(uname, data.get('demo', {}))
        upsert_stress(uname, data.get('stress', {}), MODEL_TOP3)
        return jsonify({'ok': True})
    except Exception as e:
        print(f"[save error] {e}")
        return jsonify({'ok': False, 'msg': str(e)}), 500


@app.route('/chat', methods=['POST'])
def chat():
    if 'username' not in session:
        return jsonify({'reply': 'Please login first.'}), 401

    data         = request.get_json()
    user_message = data.get('message', '').strip()
    conv_history = data.get('history', [])

    if not user_message:
        return jsonify({'reply': 'Please type a message.'})

    stress_data, demo_data = get_prev(session['username'])

    if GEMINI_API_KEY and GEMINI_API_KEY != 'PASTE_YOUR_GEMINI_API_KEY_HERE':
        system_prompt = build_system_prompt(stress_data, demo_data)
        reply, error  = call_gemini(user_message, conv_history, system_prompt)
        if reply:
            return jsonify({'reply': reply, 'source': 'ai'})
        print(f"[Gemini Error] {error} — falling back to keyword bot")
        return jsonify({'reply': keyword_fallback(user_message), 'source': 'keyword'})

    return jsonify({'reply': keyword_fallback(user_message), 'source': 'keyword'})


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    init_db()           # create tables if not present
    migrate_csv_to_db() # import existing CSV data (safe to re-run)
    app.run(debug=True, port=5050)
