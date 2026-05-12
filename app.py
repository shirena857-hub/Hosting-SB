from flask import Flask, render_template, request, redirect, url_for, session
import os
import zipfile
import subprocess
import signal
import shutil

app = Flask(__name__)
app.secret_key = "mr_ghost_secret_key_123" # সেশনের জন্য এটি প্রয়োজন

UPLOAD_FOLDER = "uploads"
MAX_RUNNING = 100

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# প্রসেসগুলো এখন ইউজার অনুযায়ী ট্র্যাক করা হবে: {(username, app_name): process}
processes = {}

# ---------- Helper Functions ----------

def get_user_upload_path():
    """বর্তমানে লগইন করা ইউজারের ফোল্ডার পাথ রিটার্ন করবে"""
    user_dir = os.path.join(UPLOAD_FOLDER, session['username'])
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def extract_zip(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(extract_to)

def install_requirements(path):
    req = os.path.join(path, "requirements.txt")
    if os.path.exists(req):
        subprocess.call(["pip", "install", "-r", req])

def find_main_file(path):
    for f in ["main.py", "app.py", "bot.py"]:
        if os.path.exists(os.path.join(path, f)):
            return f
    return None

def start_app(app_name):
    user_dir = get_user_upload_path()
    app_dir = os.path.join(user_dir, app_name)
    zip_path = os.path.join(app_dir, "app.zip")
    extract_dir = os.path.join(app_dir, "extracted")
    log_path = os.path.join(app_dir, "logs.txt")

    if not os.path.exists(extract_dir):
        if os.path.exists(zip_path):
            extract_zip(zip_path, extract_dir)
            install_requirements(extract_dir)
        else:
            return

    main_file = find_main_file(extract_dir)
    if not main_file:
        return

    log = open(log_path, "a")
    p = subprocess.Popen(
        ["python3", main_file],
        cwd=extract_dir,
        stdout=log,
        stderr=log
    )
    # ইউজারনেম এবং অ্যাপ নাম দিয়ে প্রসেস সেভ রাখা
    processes[(session['username'], app_name)] = p

def stop_app(app_name):
    key = (session['username'], app_name)
    p = processes.get(key)
    if p:
        p.send_signal(signal.SIGTERM)
        processes.pop(key, None)

# ---------- Routes ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        if username:
            session['username'] = username # এখানে পাসওয়ার্ড সিস্টেম যোগ করা যাবে
            return redirect(url_for("index"))
    return '''
        <body style="background:#0d0d0d; color:white; text-align:center; padding-top:100px; font-family:Arial;">
            <h2>🔥 SB  LOGIN PANEL 🔥</h2>
            <form method="post">
                <input type="text" name="username" placeholder="Enter Username" required 
                       style="padding:10px; border-radius:5px; border:none;"><br><br>
                <button type="submit" style="padding:10px 20px; background:#00ffcc; border:none; border-radius:5px;">Enter Panel</button>
            </form>
        </body>
    '''

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
def index():
    if 'username' not in session:
        return redirect(url_for("login"))

    user_dir = get_user_upload_path()

    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename.endswith(".zip"):
            app_name = file.filename.replace(".zip", "")
            app_dir = os.path.join(user_dir, app_name)
            os.makedirs(app_dir, exist_ok=True)
            file.save(os.path.join(app_dir, "app.zip"))

    apps = []
    if os.path.exists(user_dir):
        for name in os.listdir(user_dir):
            app_dir = os.path.join(user_dir, name)
            if not os.path.isdir(app_dir): continue
            
            log_file = os.path.join(app_dir, "logs.txt")
            log_data = ""

            if os.path.exists(log_file):
                with open(log_file, "r", errors="ignore") as f:
                    log_data = f.read()[-3000:]

            apps.append({
                "name": name,
                "running": (session['username'], name) in processes,
                "log": log_data
            })

    return render_template("index.html", apps=apps)

@app.route("/run/<name>")
def run(name):
    if 'username' not in session: return redirect(url_for("login"))
    if (session['username'], name) not in processes and len(processes) < MAX_RUNNING:
        start_app(name)
    return redirect(url_for("index"))

@app.route("/stop/<name>")
def stop(name):
    if 'username' not in session: return redirect(url_for("login"))
    stop_app(name)
    return redirect(url_for("index"))

@app.route("/restart/<name>")
def restart(name):
    if 'username' not in session: return redirect(url_for("login"))
    stop_app(name)
    start_app(name)
    return redirect(url_for("index"))

@app.route("/delete/<name>")
def delete(name):
    if 'username' not in session: return redirect(url_for("login"))
    stop_app(name)
    user_dir = get_user_upload_path()
    app_dir = os.path.join(user_dir, name)
    if os.path.exists(app_dir):
        shutil.rmtree(app_dir)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8030)
