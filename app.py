import os
import json
import threading
import time
import subprocess
import sys
import shutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.secret_key = "bot_manager_secret_key_2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ========== CONFIGURATION ==========
TEMPLATE_DIR = os.path.join(os.getcwd(), "tcp")
if not os.path.exists(TEMPLATE_DIR):
    TEMPLATE_DIR = os.getcwd()
    print(f"WARNING: 'tcp' folder not found. Using current directory: {TEMPLATE_DIR}")

BOTS_FILE = "bots.json"
WORKSPACE_DIR = "bot_workspaces"
os.makedirs(WORKSPACE_DIR, exist_ok=True)

running_bots = {}  # bot_id -> subprocess.Popen object

# ========== HELPERS ==========
def load_bots():
    if not os.path.exists(BOTS_FILE): return {}
    with open(BOTS_FILE) as f: return json.load(f)

def save_bots(bots):
    with open(BOTS_FILE, 'w') as f: json.dump(bots, f, indent=2)

def copy_bot_source(dest_dir):
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    for item in os.listdir(TEMPLATE_DIR):
        src = os.path.join(TEMPLATE_DIR, item)
        dst = os.path.join(dest_dir, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    bot_txt = os.path.join(dest_dir, "bot.txt")
    if not os.path.exists(bot_txt):
        with open(bot_txt, "w") as f:
            f.write("uid=\npassword=")

def ensure_bot_workspace(bot_id):
    workspace = os.path.join(WORKSPACE_DIR, bot_id)
    if not os.path.exists(workspace):
        copy_bot_source(workspace)
    return workspace

def stream_and_log(proc, bot_id, log_file_path):
    """Read lines from subprocess stdout, emit via socket and write to file."""
    try:
        with open(log_file_path, 'a', encoding='utf-8') as log_file:
            for line in iter(proc.stdout.readline, ''):
                if line:
                    # Emit to frontend
                    socketio.emit('new_log', {'bot_id': bot_id, 'data': line.strip()})
                    # Write to file
                    log_file.write(line)
                    log_file.flush()
    except Exception as e:
        print(f"Log error for {bot_id}: {e}")
    finally:
        if bot_id in running_bots and running_bots[bot_id] == proc:
            del running_bots[bot_id]
            bots = load_bots()
            if bot_id in bots:
                bots[bot_id]['running'] = False
                save_bots(bots)
            socketio.emit('status_update', {'bot_id': bot_id, 'running': False})

# ========== AUTO RESTART MONITOR ==========
def auto_restart_monitor():
    while True:
        now = datetime.now()
        to_restart = []
        bots = load_bots()
        for bot_id, info in bots.items():
            if info.get('auto_restart') and info.get('running') and info.get('last_start'):
                last_start = datetime.fromisoformat(info['last_start'])
                if now - last_start >= timedelta(hours=24):
                    to_restart.append(bot_id)
        for bot_id in to_restart:
            if bot_id in running_bots:
                proc = running_bots[bot_id]
                proc.terminate()
                time.sleep(1)
                if proc.poll() is None:
                    proc.kill()
                del running_bots[bot_id]
                bot = load_bots().get(bot_id)
                if bot and bot.get('uid') and bot.get('password'):
                    workspace = ensure_bot_workspace(bot_id)
                    with open(os.path.join(workspace, "bot.txt"), "w") as f:
                        f.write(f"uid={bot['uid']}\npassword={bot['password']}")
                    log_file_path = os.path.join(workspace, "stdout.log")
                    # Open file in append mode (but we'll start fresh on restart)
                    # Actually we want to keep logs, so we'll append
                    new_proc = subprocess.Popen(
                        [sys.executable, '-u', 'main.py'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, cwd=workspace
                    )
                    running_bots[bot_id] = new_proc
                    bots[bot_id]['running'] = True
                    bots[bot_id]['last_start'] = datetime.now().isoformat()
                    save_bots(bots)
                    threading.Thread(target=stream_and_log, args=(new_proc, bot_id, log_file_path), daemon=True).start()
                    socketio.emit('status_update', {'bot_id': bot_id, 'running': True})
        time.sleep(60)

threading.Thread(target=auto_restart_monitor, daemon=True).start()

# ========== ROUTES ==========
@app.route('/')
def root():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if username:
            session['username'] = username
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ========== API ==========
@app.route('/api/bots')
def api_bots():
    if 'username' not in session:
        return jsonify({"error": "unauthorized"}), 401
    username = session['username']
    bots = load_bots()
    user_bots = {bid: info for bid, info in bots.items() if info.get('username') == username}
    for bid in user_bots:
        user_bots[bid]['running'] = bid in running_bots
    return jsonify(user_bots)

@app.route('/api/create_bot', methods=['POST'])
def create_bot():
    if 'username' not in session:
        return jsonify({"error": "unauthorized"}), 401
    data = request.json
    bot_name = data.get('bot_name', '').strip()
    if not bot_name:
        return jsonify({"error": "Bot name required"}), 400
    username = session['username']
    bot_id = f"{username}_{bot_name}_{int(time.time())}"
    bots = load_bots()
    bots[bot_id] = {
        "bot_id": bot_id,
        "username": username,
        "bot_name": bot_name,
        "uid": "",
        "password": "",
        "auto_restart": False,
        "running": False,
        "created_at": datetime.now().isoformat()
    }
    save_bots(bots)
    ensure_bot_workspace(bot_id)
    return jsonify({"status": "success", "bot_id": bot_id})

@app.route('/api/set_creds', methods=['POST'])
def set_creds():
    if 'username' not in session:
        return jsonify({"error": "unauthorized"}), 401
    data = request.json
    bot_id = data.get('bot_id')
    uid = data.get('uid', '').strip()
    password = data.get('password', '').strip()
    bots = load_bots()
    if bot_id not in bots or bots[bot_id]['username'] != session['username']:
        return jsonify({"error": "not found"}), 404
    bots[bot_id]['uid'] = uid
    bots[bot_id]['password'] = password
    save_bots(bots)
    workspace = ensure_bot_workspace(bot_id)
    with open(os.path.join(workspace, "bot.txt"), "w") as f:
        f.write(f"uid={uid}\npassword={password}")
    return jsonify({"status": "success"})

@app.route('/api/start_bot', methods=['POST'])
def start_bot():
    if 'username' not in session:
        return jsonify({"error": "unauthorized"}), 401
    bot_id = request.json.get('bot_id')
    bots = load_bots()
    if bot_id not in bots or bots[bot_id]['username'] != session['username']:
        return jsonify({"error": "not found"}), 404
    bot = bots[bot_id]
    if bot_id in running_bots:
        return jsonify({"error": "already running"}), 400
    if not bot.get('uid') or not bot.get('password'):
        return jsonify({"error": "Set UID and password first"}), 400
    workspace = ensure_bot_workspace(bot_id)
    with open(os.path.join(workspace, "bot.txt"), "w") as f:
        f.write(f"uid={bot['uid']}\npassword={bot['password']}")
    # Prepare log file – clear it on fresh start? We'll append.
    log_file_path = os.path.join(workspace, "stdout.log")
    # If you want to clear old logs on each start, uncomment next line:
    # if os.path.exists(log_file_path): open(log_file_path, 'w').close()
    proc = subprocess.Popen(
        [sys.executable, '-u', 'main.py'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, cwd=workspace
    )
    running_bots[bot_id] = proc
    bot['running'] = True
    bot['last_start'] = datetime.now().isoformat()
    save_bots(bots)
    threading.Thread(target=stream_and_log, args=(proc, bot_id, log_file_path), daemon=True).start()
    socketio.emit('status_update', {'bot_id': bot_id, 'running': True})
    return jsonify({"status": "success"})

@app.route('/api/stop_bot', methods=['POST'])
def stop_bot():
    if 'username' not in session:
        return jsonify({"error": "unauthorized"}), 401
    bot_id = request.json.get('bot_id')
    bots = load_bots()
    if bot_id not in bots or bots[bot_id]['username'] != session['username']:
        return jsonify({"error": "not found"}), 404
    if bot_id in running_bots:
        proc = running_bots[bot_id]
        proc.terminate()
        time.sleep(1)
        if proc.poll() is None:
            proc.kill()
        del running_bots[bot_id]
        bots[bot_id]['running'] = False
        save_bots(bots)
        socketio.emit('status_update', {'bot_id': bot_id, 'running': False})
    return jsonify({"status": "success"})

@app.route('/api/restart_bot', methods=['POST'])
def restart_bot():
    stop_bot()
    time.sleep(2)
    return start_bot()

@app.route('/api/delete_bot', methods=['DELETE'])
def delete_bot():
    if 'username' not in session:
        return jsonify({"error": "unauthorized"}), 401
    bot_id = request.args.get('bot_id')
    bots = load_bots()
    if bot_id not in bots or bots[bot_id]['username'] != session['username']:
        return jsonify({"error": "not found"}), 404
    if bot_id in running_bots:
        running_bots[bot_id].terminate()
        time.sleep(1)
        if running_bots[bot_id].poll() is None:
            running_bots[bot_id].kill()
        del running_bots[bot_id]
    del bots[bot_id]
    save_bots(bots)
    workspace = os.path.join(WORKSPACE_DIR, bot_id)
    if os.path.exists(workspace):
        shutil.rmtree(workspace, ignore_errors=True)
    return jsonify({"status": "success"})

@app.route('/api/toggle_auto_restart', methods=['POST'])
def toggle_auto_restart():
    if 'username' not in session:
        return jsonify({"error": "unauthorized"}), 401
    data = request.json
    bot_id = data.get('bot_id')
    value = data.get('value', False)
    bots = load_bots()
    if bot_id not in bots or bots[bot_id]['username'] != session['username']:
        return jsonify({"error": "not found"}), 404
    bots[bot_id]['auto_restart'] = value
    save_bots(bots)
    return jsonify({"status": "success"})

@app.route('/api/logs/<bot_id>')
def get_logs(bot_id):
    if 'username' not in session:
        return "Unauthorized", 401
    bots = load_bots()
    if bot_id not in bots or bots[bot_id]['username'] != session['username']:
        return "Not found", 404
    workspace = os.path.join(WORKSPACE_DIR, bot_id)
    log_file = os.path.join(workspace, "stdout.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()[-50000:]  # last 50000 characters
        except:
            return "Error reading log"
    return "No logs yet (start the bot to generate logs)"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)