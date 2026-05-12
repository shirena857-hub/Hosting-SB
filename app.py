from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import zipfile
import subprocess
import signal
import shutil
import json
from datetime import datetime
import sys
import time
import threading
import atexit

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "JUBAYER_hosting_secret_key_2024")

UPLOAD_FOLDER = "servers"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Track processes by user and server name
processes = {}
server_configs = {}

# Cleanup function for server deletion
def force_delete_directory(path, max_retries=5, delay=1):
    """Force delete directory with retries"""
    for i in range(max_retries):
        try:
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
                return True
        except Exception as e:
            print(f"Attempt {i+1} failed: {str(e)}")
            time.sleep(delay)
    return False

# Cleanup on exit
@atexit.register
def cleanup_on_exit():
    """Cleanup all processes when app exits"""
    for (username, server_name), process in list(processes.items()):
        try:
            if process.poll() is None:
                process.terminate()
                time.sleep(0.5)
                if process.poll() is None:
                    process.kill()
        except:
            pass

# ---------- Helper Functions ----------

def get_user_server_path():
    """Get the current user's server folder path"""
    if 'username' not in session:
        return None
    user_dir = os.path.join(UPLOAD_FOLDER, session['username'])
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def extract_zip(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(extract_to)

def install_requirements(path):
    req = os.path.join(path, "requirements.txt")
    if os.path.exists(req):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )
            if result.returncode != 0:
                print(f"Requirements installation failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            print("Requirements installation timed out")
        except Exception as e:
            print(f"Error installing requirements: {str(e)}")

def find_main_file(path):
    """Find the main Python file in a directory"""
    # Check for common main files
    common_files = ["main.py", "app.py", "bot.py", "server.py", "index.py", "start.py"]
    for filename in common_files:
        if os.path.exists(os.path.join(path, filename)):
            return filename
    
    # If no common file found, look for any Python file
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith('.py') and not file.startswith('_'):
                # Check if file looks like a main file
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                        # Look for common patterns
                        if '__main__' in content or 'if __name__' in content:
                            return file
                except:
                    continue
    return None

def save_server_config(username, server_name, config):
    config_path = os.path.join(UPLOAD_FOLDER, username, server_name, "config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

def load_server_config(username, server_name):
    config_path = os.path.join(UPLOAD_FOLDER, username, server_name, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"status": "stopped", "type": "web", "port": 8080, "created_at": str(datetime.now())}

def start_server(server_name):
    try:
        user_dir = get_user_server_path()
        if not user_dir:
            return False
            
        server_dir = os.path.join(user_dir, server_name)
        
        # Check if server directory exists
        if not os.path.exists(server_dir):
            print(f"[ERROR] Server directory not found: {server_dir}")
            return False
        
        config = load_server_config(session['username'], server_name)
        log_path = os.path.join(server_dir, "logs.txt")
        
        # Open log file
        with open(log_path, 'a', encoding='utf-8') as log:
            log.write(f"\n{'='*60}\n")
            log.write(f"Starting server: {server_name} at {datetime.now()}\n")
            
            # Check for ZIP file
            zip_path = os.path.join(server_dir, "server.zip")
            extract_dir = os.path.join(server_dir, "extracted")
            
            if os.path.exists(zip_path):
                log.write(f"Found ZIP file: {zip_path}\n")
                if not os.path.exists(extract_dir):
                    os.makedirs(extract_dir, exist_ok=True)
                    log.write(f"Extracting to: {extract_dir}\n")
                    try:
                        extract_zip(zip_path, extract_dir)
                        install_requirements(extract_dir)
                    except Exception as e:
                        log.write(f"Error extracting/installing: {str(e)}\n")
                working_dir = extract_dir
            else:
                # No ZIP, use server directory directly
                log.write("No ZIP file found, using server directory directly\n")
                working_dir = server_dir
                install_requirements(working_dir)
            
            # Find main file
            main_file = find_main_file(working_dir)
            if not main_file:
                # Create a simple test file if none exists
                test_file = os.path.join(working_dir, "test_server.py")
                if not os.path.exists(test_file):
                    with open(test_file, 'w') as f:
                        f.write("""
from flask import Flask
import time

app = Flask(__name__)

@app.route('/')
def home():
    return f"<h1>SB Hosting Test Server</h1><p>Running at {time.ctime()}</p>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
""")
                main_file = "test_server.py"
            
            log.write(f"Found main file: {main_file}\n")
            
            # Determine Python command
            python_cmd = "python3" if shutil.which("python3") else "python"
            
            # Prepare command
            cmd = [python_cmd, main_file]
            
            # Add port if it's a web server
            if config.get('type') == 'web':
                port = config.get('port', 8080)
                log.write(f"Web server starting on port: {port}\n")
            
            log.write(f"Command: {' '.join(cmd)}\n")
            log.write(f"Working directory: {working_dir}\n")
            log.write(f"{'='*60}\n")
        
        # Open log file for subprocess
        log_file = open(log_path, 'a', encoding='utf-8')
        
        # Start the process
        try:
            p = subprocess.Popen(
                cmd,
                cwd=working_dir,
                stdout=log_file,
                stderr=log_file,
                shell=False,
                start_new_session=True  # Important for proper cleanup
            )
            
            # Store the process
            processes[(session['username'], server_name)] = p
            
            # Update config
            config['status'] = 'running'
            config['pid'] = p.pid
            config['started_at'] = str(datetime.now())
            save_server_config(session['username'], server_name, config)
            
            # Start a thread to monitor the process
            def monitor_process(proc, key, server_dir_path):
                proc.wait()
                # Cleanup after process ends
                if key in processes:
                    processes.pop(key, None)
                # Update config
                config = load_server_config(session['username'], server_name)
                config['status'] = 'stopped'
                config.pop('pid', None)
                save_server_config(session['username'], server_name, config)
            
            monitor_thread = threading.Thread(
                target=monitor_process,
                args=(p, (session['username'], server_name), server_dir),
                daemon=True
            )
            monitor_thread.start()
            
            return True
                
        except Exception as e:
            log_file.write(f"[ERROR] Failed to start process: {str(e)}\n")
            log_file.close()
            return False
            
    except Exception as e:
        print(f"[ERROR in start_server]: {str(e)}")
        return False

def stop_server(server_name):
    key = (session['username'], server_name)
    p = processes.get(key)
    
    if p:
        try:
            print(f"[INFO] Stopping server {server_name} with PID: {p.pid}")
            
            # Try graceful termination
            p.terminate()
            time.sleep(2)
            
            # Force kill if still running
            if p.poll() is None:
                p.kill()
                time.sleep(1)
            
            # Remove from processes dict
            processes.pop(key, None)
            
            # Update config
            config = load_server_config(session['username'], server_name)
            config['status'] = 'stopped'
            config.pop('pid', None)
            save_server_config(session['username'], server_name, config)
            
            return True
        except Exception as e:
            print(f"[ERROR stopping server]: {str(e)}")
            return False
    
    return True

# ---------- Routes ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        if username:
            session['username'] = username
            return redirect(url_for("dashboard"))
    return '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>SB Hosting | Login</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                background-color: #050505;
                color: #ffffff;
                font-family: 'Montserrat', sans-serif;
                overflow: hidden;
                height: 100vh;
                width: 100vw;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }
            .login-container {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                text-align: center;
                z-index: 1;
                width: 90%;
                max-width: 400px;
                padding: 40px;
                background: rgba(0, 0, 0, 0.8);
                border-radius: 20px;
                box-shadow: 0 0 50px rgba(157, 0, 255, 0.3);
                backdrop-filter: blur(10px);
            }
            .logo {
                font-size: 2.5rem;
                font-weight: 700;
                margin-bottom: 10px;
                background: linear-gradient(to right, #9d00ff, #00d4ff);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-transform: uppercase;
                letter-spacing: 3px;
            }
            .tagline {
                color: #aaa;
                margin-bottom: 30px;
                font-size: 0.9rem;
            }
            input[type="text"] {
                width: 100%;
                padding: 15px;
                margin-bottom: 20px;
                border: 2px solid #9d00ff;
                border-radius: 10px;
                background: rgba(255, 255, 255, 0.05);
                color: white;
                font-size: 1rem;
                transition: all 0.3s;
            }
            input[type="text"]:focus {
                outline: none;
                border-color: #00d4ff;
                box-shadow: 0 0 15px rgba(0, 212, 255, 0.5);
            }
            button {
                width: 100%;
                padding: 15px;
                border: 2px solid #9d00ff;
                background: linear-gradient(45deg, #9d00ff, #00d4ff);
                color: white;
                border-radius: 10px;
                font-size: 1rem;
                font-weight: bold;
                cursor: pointer;
                transition: all 0.3s;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 20px rgba(157, 0, 255, 0.4);
            }
            .particles {
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: -1;
            }
        </style>
    </head>
    <body>
        <div class="particles" id="particles"></div>
        <div class="login-container">
            <div class="logo">JUBAYER Hosting</div>
            <div class="tagline">Unlimited Server Hosting - Free Forever</div>
            <form method="POST">
                <input type="text" name="username" placeholder="Enter Username" required>
                <button type="submit">Enter Dashboard</button>
            </form>
            <div style="margin-top: 20px; color: #666; font-size: 0.8rem;">
                <p>No password required! Just enter a username.</p>
            </div>
        </div>
        <script>
            // Simple particles background
            const canvas = document.getElementById('particles');
            const ctx = canvas.getContext('2d');
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
            
            let particles = [];
            for(let i = 0; i < 50; i++) {
                particles.push({
                    x: Math.random() * canvas.width,
                    y: Math.random() * canvas.height,
                    radius: Math.random() * 2 + 1,
                    speedX: Math.random() * 0.5 - 0.25,
                    speedY: Math.random() * 0.5 - 0.25
                });
            }
            
            function animate() {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.fillStyle = 'rgba(157, 0, 255, 0.5)';
                
                particles.forEach(p => {
                    p.x += p.speedX;
                    p.y += p.speedY;
                    
                    if(p.x < 0) p.x = canvas.width;
                    if(p.x > canvas.width) p.x = 0;
                    if(p.y < 0) p.y = canvas.height;
                    if(p.y > canvas.height) p.y = 0;
                    
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                    ctx.fill();
                });
                
                requestAnimationFrame(animate);
            }
            animate();
        </script>
    </body>
    </html>
    '''

@app.route("/logout")
def logout():
    # Stop all user's servers
    if 'username' in session:
        username = session['username']
        servers_to_stop = [(user, name) for (user, name) in list(processes.keys()) if user == username]
        for (user, server_name) in servers_to_stop:
            stop_server(server_name)
        time.sleep(1)  # Wait for processes to stop
    
    session.clear()
    return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
def dashboard():
    if 'username' not in session:
        return redirect(url_for("login"))

    user_dir = get_user_server_path()
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "create_server":
            server_name = request.form.get("server_name", "").strip()
            server_type = request.form.get("server_type", "web")
            port = request.form.get("port", "8080")
            
            if server_name:
                # Clean server name for filesystem
                safe_name = server_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
                server_dir = os.path.join(user_dir, safe_name)
                
                if not os.path.exists(server_dir):
                    os.makedirs(server_dir, exist_ok=True)
                    
                    # Save initial config
                    config = {
                        "name": server_name,
                        "display_name": server_name,
                        "safe_name": safe_name,
                        "type": server_type,
                        "port": int(port) if port.isdigit() else 8080,
                        "status": "stopped",
                        "created_at": str(datetime.now())
                    }
                    save_server_config(session['username'], safe_name, config)
                    
                    # Handle file upload
                    file = request.files.get("server_files")
                    if file and file.filename:
                        filename = file.filename
                        if filename.endswith(".zip"):
                            file.save(os.path.join(server_dir, "server.zip"))
                        else:
                            # Save individual file
                            file.save(os.path.join(server_dir, filename))

    # Get all servers for current user
    servers = []
    if os.path.exists(user_dir):
        for folder_name in os.listdir(user_dir):
            server_dir = os.path.join(user_dir, folder_name)
            if not os.path.isdir(server_dir): 
                continue
            
            config = load_server_config(session['username'], folder_name)
            
            # Get log data
            log_file = os.path.join(server_dir, "logs.txt")
            log_data = ""
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", errors="ignore", encoding='utf-8') as f:
                        content = f.read()
                        log_data = content[-2000:] if len(content) > 2000 else content
                except:
                    log_data = "Error reading log file"

            # Check for files
            has_files = (
                os.path.exists(os.path.join(server_dir, "server.zip")) or 
                any(f.endswith('.py') for f in os.listdir(server_dir) if os.path.isfile(os.path.join(server_dir, f)))
            )

            servers.append({
                "name": folder_name,
                "display_name": config.get("display_name", folder_name),
                "running": (session['username'], folder_name) in processes,
                "log": log_data,
                "config": config,
                "has_files": has_files,
                "created_at": config.get("created_at", "Unknown")
            })

    # Sort servers by creation date (newest first)
    servers.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return render_template("dashboard.html", servers=servers)

@app.route("/api/server/<action>/<name>", methods=["POST"])
def server_action(action, name):
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401
    
    # Clean the server name
    server_name = name.strip()
    
    if action == "start":
        # Check if server exists
        user_dir = get_user_server_path()
        server_dir = os.path.join(user_dir, server_name)
        
        if not os.path.exists(server_dir):
            return jsonify({"error": f"Server '{server_name}' not found"}), 404
        
        if (session['username'], server_name) in processes:
            return jsonify({"error": "Server is already running"}), 400
        
        if start_server(server_name):
            return jsonify({
                "success": True, 
                "message": f"Server '{server_name}' started successfully"
            })
        else:
            return jsonify({
                "error": f"Failed to start server '{server_name}'. Check logs for details."
            }), 400
    
    elif action == "stop":
        if stop_server(server_name):
            return jsonify({
                "success": True, 
                "message": f"Server '{server_name}' stopped"
            })
        else:
            return jsonify({
                "error": f"Failed to stop server '{server_name}'"
            }), 400
    
    elif action == "restart":
        stop_server(server_name)
        time.sleep(2)
        if start_server(server_name):
            return jsonify({
                "success": True, 
                "message": f"Server '{server_name}' restarted"
            })
        else:
            return jsonify({
                "error": f"Failed to restart server '{server_name}'"
            }), 400
    
    elif action == "delete":
        # First stop the server
        stop_server(server_name)
        time.sleep(1)
        
        user_dir = get_user_server_path()
        server_dir = os.path.join(user_dir, server_name)
        
        if os.path.exists(server_dir):
            try:
                # Force delete with retries
                if force_delete_directory(server_dir):
                    # Remove from processes if still there
                    key = (session['username'], server_name)
                    if key in processes:
                        processes.pop(key, None)
                    return jsonify({
                        "success": True, 
                        "message": f"Server '{server_name}' deleted successfully"
                    })
                else:
                    return jsonify({
                        "error": f"Failed to delete server directory after multiple attempts"
                    }), 400
            except Exception as e:
                return jsonify({
                    "error": f"Failed to delete server: {str(e)}"
                }), 400
        else:
            return jsonify({
                "error": f"Server '{server_name}' not found"
            }), 404
    
    return jsonify({"error": "Invalid action"}), 400

@app.route("/api/logs/<name>")
def get_logs(name):
    if 'username' not in session:
        return "", 401
    
    server_name = name.strip()
    user_dir = get_user_server_path()
    server_dir = os.path.join(user_dir, server_name)
    log_file = os.path.join(server_dir, "logs.txt")
    
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", errors="ignore", encoding='utf-8') as f:
                content = f.read()
                return content[-10000:] if len(content) > 10000 else content
        except:
            return "Error reading log file"
    return "No logs available"

@app.route("/api/servers")
def get_servers():
    if 'username' not in session:
        return jsonify([])
    
    user_dir = get_user_server_path()
    servers = []
    
    if os.path.exists(user_dir):
        for folder_name in os.listdir(user_dir):
            server_dir = os.path.join(user_dir, folder_name)
            if os.path.isdir(server_dir):
                config = load_server_config(session['username'], folder_name)
                servers.append({
                    "name": folder_name,
                    "display_name": config.get("display_name", folder_name),
                    "running": (session['username'], folder_name) in processes,
                    "config": config
                })
    
    return jsonify(servers)

@app.route("/api/stats")
def get_stats():
    if 'username' not in session:
        return jsonify({})
    
    user_dir = get_user_server_path()
    total_servers = 0
    running_servers = 0
    
    if os.path.exists(user_dir):
        for folder_name in os.listdir(user_dir):
            server_dir = os.path.join(user_dir, folder_name)
            if os.path.isdir(server_dir):
                total_servers += 1
                if (session['username'], folder_name) in processes:
                    running_servers += 1
    
    return jsonify({
        "total_servers": total_servers,
        "running_servers": running_servers,
        "unlimited": True,
        "message": "Unlimited servers available!"
    })

@app.route("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return jsonify({
        "status": "healthy",
        "timestamp": str(datetime.now()),
        "total_processes": len(processes)
    })

# Static file serving for uploaded content
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == "__main__":
    # Create necessary directories
    os.makedirs("templates", exist_ok=True)
    
    # Get port from environment (for deployment) or use 8030
    port = int(os.environ.get("PORT", 8080))
    
    # Print startup info
    print(f"""
    ╔══════════════════════════════════════════╗
    ║      SB Hosting - Unlimited Servers    ║
    ║           Free Forever Edition           ║
    ╠══════════════════════════════════════════╣
    ║  • Server: http://0.0.0.0:{port}        ║
    ║  • Upload Folder: {UPLOAD_FOLDER}       ║
    ║  • No Server Limits!                    ║
    ║  • Press Ctrl+C to stop                 ║
    ╚══════════════════════════════════════════╝
    """)
    
    app.run(host="0.0.0.0", port=port, debug=True)