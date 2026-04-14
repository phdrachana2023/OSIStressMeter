OSI STRESS ASSESSMENT — COMPLETE FLASK APP v3
=============================================

HOW TO RUN:
  1.  pip install flask joblib
  2.  python app.py
  3.  Open http://localhost:5050

IMPORTANT — MODEL TOP 3 SETUP:
  After training your ML model, run this in your training notebook:

      import joblib
      importances = pd.Series(rf_model.feature_importances_, index=selected_features)
      top3 = importances.nlargest(3).index.tolist()
      joblib.dump(top3, 'model_top3_subscales.pkl')

  Place model_top3_subscales.pkl in the same folder as app.py.
  If not found, app defaults to: ['Sub-Scale I','Sub-Scale VII','Sub-Scale X']

FILE STRUCTURE:
  app.py                    All routes + logic
  model_top3_subscales.pkl  Your trained model's top 3 (place here)
  templates/
    login.html              Login page
    register.html           Registration
    home.html               Dashboard with OSI info + action buttons
    basic.html              Basic assessment (Under Construction)
    advanced.html           46-question OSI assessment
    result.html             Results: StressOmeter + subscales + chatbot
  data/
    users.csv               User credentials (auto-created)
    demographics.csv        Demographic data per user
    stress_results.csv      Stress scores per user

USER FLOW:
  Register / Login
    → Home (OSI info + 2 or 3 buttons)
      → Basic Assessment    : Under Construction
      → Advanced Assessment : 46Q form → Submit → Results page
      → Previous Assessment : (shown only after first assessment)

RESULTS PAGE:
  - StressOmeter (animated SVG needle)
  - 12 subscale scores table
  - ML model's fixed top 3 concern areas (loaded from pkl — never changes)
  - Your personal top 3 + targeted recommendations
  - 8 general stress relief strategies
  - Professional helplines
  - 💬 Stress Chatbot (bottom-right)

CHATBOT TOPICS:
  stress, osi, score, burnout, sleep, exercise, mindfulness, breathing,
  role overload, ambiguity, conflict, powerlessness, peer relations,
  work-life balance, helplines, anxiety, recommendations
