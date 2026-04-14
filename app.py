from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json, csv, os, hashlib, urllib.request, urllib.error
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'osi_stress_2024_secure'

DATA_DIR    = os.path.join(os.path.dirname(__file__), 'data')
LOGIN_FILE  = os.path.join(DATA_DIR, 'users.csv')
DEMO_FILE   = os.path.join(DATA_DIR, 'demographics.csv')
STRESS_FILE = os.path.join(DATA_DIR, 'stress_results.csv')
os.makedirs(DATA_DIR, exist_ok=True)

# ── Gemini API Key — paste your free key from aistudio.google.com ────────────
GEMINI_API_KEY = 'AIzaSyAHjvTD1xHxnCcFkiSppjJRiDkPNIjPGRY'

# ── Load MODEL TOP 3 once at startup ────────────────────────────────────────
MODEL_TOP3_PATH = os.path.join(os.path.dirname(__file__), 'model_top3_subscales.pkl')
try:
    import joblib
    MODEL_TOP3 = joblib.load(MODEL_TOP3_PATH)
    print(f"[OSI] Loaded model top 3 from pkl: {MODEL_TOP3}")
except Exception:
    MODEL_TOP3 = ['Sub-Scale I', 'Sub-Scale III', 'Sub-Scale XI']
    print(f"[OSI] model_top3_subscales.pkl not found — using default: {MODEL_TOP3}")

# ── CSV helpers ──────────────────────────────────────────────────────────────
def read_csv(path):
    if not os.path.exists(path): return []
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def append_csv(path, row, headers):
    wh = not os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if wh: w.writeheader()
        w.writerow(row)

def update_csv(path, key, val, row, headers):
    rows = read_csv(path)
    done = False
    for i, r in enumerate(rows):
        if r.get(key) == val:
            rows[i] = row; done = True; break
    if not done:
        rows.append(row)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader(); w.writerows(rows)

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()

def get_user(u):
    for r in read_csv(LOGIN_FILE):
        if r.get('username') == u: return r
    return None

def get_prev(u):
    s = next((r for r in read_csv(STRESS_FILE) if r.get('username') == u), None)
    d = next((r for r in read_csv(DEMO_FILE)   if r.get('username') == u), None)
    return s, d

# ── Gemini Chat ──────────────────────────────────────────────────────────────
def build_system_prompt(stress_data, demo_data):
    """Build a rich system prompt from the user's OSI assessment data."""

    # Extract key info safely
    name        = demo_data.get('full_name', 'the user') if demo_data else 'the user'
    designation = demo_data.get('designation', 'an academician') if demo_data else 'an academician'
    experience  = demo_data.get('experience', 'unknown') if demo_data else 'unknown'
    total_score = stress_data.get('total_score', 'unknown') if stress_data else 'unknown'
    overall_lvl = stress_data.get('overall_level', 'unknown') if stress_data else 'unknown'
    assess_type = stress_data.get('assessment_type', 'advanced') if stress_data else 'advanced'

    # Build subscale summary
    subscale_info = ''
    if stress_data:
        sub_lines = []
        for i in range(1, 13):
            name_key  = f'sub{i}'
            score_key = f'sub{i}_score'
            level_key = f'sub{i}_level'
            sn = stress_data.get(name_key, '')
            sc = stress_data.get(score_key, '')
            sl = stress_data.get(level_key, '')
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

    system_prompt = f"""You are an expert occupational stress counsellor and psychologist specialising in the Occupational Stress Index (OSI) by Srivastava & Singh (1981). You are embedded in an OSI stress assessment web application used by academicians in India.

YOUR ROLE:
- Provide empathetic, evidence-based guidance on occupational stress
- Answer questions about the user's OSI assessment results
- Give practical, actionable coping strategies
- Explain OSI sub-scales and what scores mean
- Recommend professional help when stress is High

STRICT RULES:
- ONLY answer questions related to: occupational stress, mental health at work, OSI scores and sub-scales, burnout, work-life balance, coping strategies, mindfulness, sleep, exercise, anxiety, and general well-being
- If asked ANYTHING unrelated (politics, coding, general knowledge, etc.), politely say: "I'm only able to help with occupational stress and well-being topics. Please ask me about your stress assessment or coping strategies."
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
- Basic Screening: Low=46-122, Moderate=123-155, High=156-230
- Sub-scale levels: Low, Moderate, High based on normative ranges

HELPLINES (India):
- iCall-TISS: 9152987821
- Vandrevala Foundation: 1860-2662-345
- NIMHANS: 080-46110007
- Fortis Helpline: 8376804102

Use the user's actual scores to give personalised, specific advice. Reference their high-stress subscales by name when relevant."""

    return system_prompt


def call_gemini(user_message, conversation_history, system_prompt):
    """Call Gemini API and return reply text."""

    if not GEMINI_API_KEY or GEMINI_API_KEY == 'PASTE_YOUR_GEMINI_API_KEY_HERE':
        return None, "API key not configured"

    # Build contents array — Gemini uses alternating user/model roles
    contents = []

    # Add conversation history
    for turn in conversation_history:
        contents.append({
            "role": turn["role"],
            "parts": [{"text": turn["text"]}]
        })

    # Add current user message
    contents.append({
        "role": "user",
        "parts": [{"text": user_message}]
    })

    payload = {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 400,
            "topP": 0.9
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
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

        # Extract text from response
        reply = result['candidates'][0]['content']['parts'][0]['text']
        return reply.strip(), None

    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8')
        return None, f"API error {e.code}: {err_body}"
    except Exception as e:
        return None, str(e)


# ── Keyword fallback (used when API key not set or API fails) ─────────────────
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


# ── Routes ───────────────────────────────────────────────────────────────────
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
            append_csv(LOGIN_FILE,
                {'username': u, 'password': hash_pw(p), 'fullname': fn, 'email': em,
                 'registered': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
                ['username', 'password', 'fullname', 'email', 'registered'])
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
        assessment_type=s.get('assessment_type','advanced') if s else 'advanced')

@app.route('/previous')
def previous():
    if 'username' not in session: return redirect(url_for('login'))
    s, d = get_prev(session['username'])
    if not s: return redirect(url_for('home'))
    return render_template('result.html',
        fullname=session['fullname'],
        stress=s, demo=d,
        model_top3=MODEL_TOP3,
        assessment_type=s.get('assessment_type','advanced') if s else 'advanced',
        is_previous=True)

@app.route('/save', methods=['POST'])
def save():
    if 'username' not in session:
        return jsonify({'ok': False, 'msg': 'Not logged in'}), 401
    data  = request.get_json()
    uname = session['username']
    ts    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    demo = data.get('demo', {})
    dh = ['username','timestamp','full_name','email','contact','institute',
          'teaching_level','gender','marital_status','age_group',
          'education','designation','employment_type','experience','tenure']
    dr = {h: demo.get(h.replace('_','_'), '') for h in dh}
    dr['username']        = uname
    dr['timestamp']       = ts
    dr['full_name']       = demo.get('full_name', '')
    dr['email']           = demo.get('email', '')
    dr['contact']         = demo.get('contact', '')
    dr['institute']       = demo.get('institute', '')
    dr['teaching_level']  = demo.get('teaching_level', '')
    dr['gender']          = demo.get('gender', '')
    dr['marital_status']  = demo.get('marital_status', '')
    dr['age_group']       = demo.get('age_group', '')
    dr['education']       = demo.get('education', '')
    dr['designation']     = demo.get('designation', '')
    dr['employment_type'] = demo.get('employment_type', '')
    dr['experience']      = demo.get('experience', '')
    dr['tenure']          = demo.get('tenure', '')
    update_csv(DEMO_FILE, 'username', uname, dr, dh)

    stress = data.get('stress', {})
    sh = ['username','timestamp','total_score','overall_level','assessment_type',
          'sub1','sub1_score','sub1_level','sub2','sub2_score','sub2_level',
          'sub3','sub3_score','sub3_level','sub4','sub4_score','sub4_level',
          'sub5','sub5_score','sub5_level','sub6','sub6_score','sub6_level',
          'sub7','sub7_score','sub7_level','sub8','sub8_score','sub8_level',
          'sub9','sub9_score','sub9_level','sub10','sub10_score','sub10_level',
          'sub11','sub11_score','sub11_level','sub12','sub12_score','sub12_level',
          'top1_subscale','top1_label','top1_score','top1_level',
          'top2_subscale','top2_label','top2_score','top2_level',
          'top3_subscale','top3_label','top3_score','top3_level',
          'model_top1','model_top2','model_top3']
    subs = stress.get('subscales', [])
    top3 = stress.get('top3', [])
    sr = {'username': uname, 'timestamp': ts,
          'total_score': stress.get('total_score', ''),
          'overall_level': stress.get('overall_level', ''),
          'assessment_type': stress.get('assessment_type', 'advanced')}
    for i, s in enumerate(subs, 1):
        sr[f'sub{i}']       = s.get('name', '')
        sr[f'sub{i}_score'] = s.get('score', '')
        sr[f'sub{i}_level'] = s.get('level', '')
    for i, t in enumerate(top3, 1):
        sr[f'top{i}_subscale'] = t.get('name', '')
        sr[f'top{i}_label']    = t.get('label', '')
        sr[f'top{i}_score']    = t.get('score', '')
        sr[f'top{i}_level']    = t.get('level', '')
    for i, m in enumerate(MODEL_TOP3[:3], 1):
        sr[f'model_top{i}'] = m
    update_csv(STRESS_FILE, 'username', uname, sr, sh)
    return jsonify({'ok': True})


# ── AI Chat Route ─────────────────────────────────────────────────────────────
@app.route('/chat', methods=['POST'])
def chat():
    if 'username' not in session:
        return jsonify({'reply': 'Please login first.'}), 401

    data            = request.get_json()
    user_message    = data.get('message', '').strip()
    # Conversation history sent from frontend: list of {role, text}
    conv_history    = data.get('history', [])

    if not user_message:
        return jsonify({'reply': 'Please type a message.'})

    # Get user's stress and demo data for personalised context
    stress_data, demo_data = get_prev(session['username'])

    # Try Gemini API first
    if GEMINI_API_KEY and GEMINI_API_KEY != 'PASTE_YOUR_GEMINI_API_KEY_HERE':
        system_prompt = build_system_prompt(stress_data, demo_data)
        reply, error  = call_gemini(user_message, conv_history, system_prompt)
        if reply:
            return jsonify({'reply': reply, 'source': 'ai'})
        else:
            # API failed — fall back to keyword bot
            print(f"[Gemini Error] {error} — falling back to keyword bot")
            return jsonify({'reply': keyword_fallback(user_message), 'source': 'keyword'})
    else:
        # No API key configured — use keyword bot
        return jsonify({'reply': keyword_fallback(user_message), 'source': 'keyword'})


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, port=5050)
