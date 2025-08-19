# app.py
import os, sys, json, time, subprocess, threading, hashlib
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import psutil
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'ujala_secret_key_2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
SETTINGS_FILE = BASE_DIR / "ujala_settings.json"
ALLOWED_EXT = {'py'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
settings, running_processes, script_logs = {}, {}, {}

def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            settings = json.load(f)
    except:
        settings = {"scripts": [], "favourites": [], "recent": [], "theme": "dark", "monitored_folders": []}

def save_settings():
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

def discover_python_files():
    scripts = []
    for folder in settings.get("monitored_folders", []):
        if os.path.exists(folder):
            for root, dirs, files in os.walk(folder):      # ← yeh line
                for file in files:
                    if file.endswith('.py'):               # ← yeh line
                        fp = Path(root) / file
                        scripts.append({
                            "id": abs(hash(str(fp))) % 10**8,
                            "name": fp.stem,
                            "path": str(fp),
                            "description": f"Python script from {fp.relative_to(folder)}",
                            "category": f"📂 {Path(folder).name}",
                            "size": fp.stat().st_size,
                            "modified": datetime.fromtimestamp(fp.stat().st_mtime).isoformat()
                        })
    return scripts

def run_python_script(script_id, script_path, script_name):
    logs = script_logs.setdefault(script_id, [])

    def log(msg, typ="info"):
        entry = {"timestamp": datetime.now().strftime("%H:%M:%S"), "message": msg, "type": typ}
        logs.append(entry)
        socketio.emit('script_log', {'script_id': script_id, 'log': entry})

    log(f"🚀 Starting {script_name}...")
    try:
        proc = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}
        )

        running_processes[script_id] = {
            "process": proc,
            "name": script_name,
            "path": script_path,
            "start_time": datetime.now().isoformat()
        }
        socketio.emit('script_started', {'script_id': script_id})

        def read_stdout():
            for line in iter(proc.stdout.readline, ''):
                if line:
                    log(f"[OUT] {line.strip()}")

        def read_stderr():
            for line in iter(proc.stderr.readline, ''):
                if line:
                    log(f"[ERR] {line.strip()}", "error")

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()

        ret = proc.wait()
        log("✅ Completed successfully!" if ret == 0 else f"❌ Exited with code {ret}",
            "success" if ret == 0 else "error")
    except Exception as e:
        log(f"❌ Error: {e}", "error")
    finally:
        running_processes.pop(script_id, None)
        socketio.emit('script_stopped', {'script_id': script_id})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scripts')
def api_scripts():
    discovered = discover_python_files()
    manual = settings.get("scripts", [])
    return jsonify({"scripts": discovered + manual, "running": list(running_processes.keys()),
                    "favourites": settings.get("favourites", []), "recent": settings.get("recent", [])})

@app.route('/api/scripts/<int:sid>/run', methods=['POST'])
def api_run(sid):
    all_scripts = discover_python_files() + settings.get("scripts", [])
    script = next((s for s in all_scripts if s["id"] == sid), None)
    if not script or not Path(script["path"]).exists():
        return jsonify({"error": "not found"}), 404
    if sid in running_processes:
        return jsonify({"error": "already running"}), 400

    recent = settings.setdefault("recent", [])
    if script["name"] in recent:
        recent.remove(script["name"])
    recent.insert(0, script["name"])
    settings["recent"] = recent[:10]
    save_settings()

    threading.Thread(target=run_python_script, args=(sid, script["path"], script["name"]), daemon=True).start()
    return jsonify({"status": "success"})

@app.route('/api/scripts/<int:sid>/stop', methods=['POST'])
def api_stop(sid):
    info = running_processes.get(sid)
    if not info: return jsonify({"error": "not running"}), 400
    try:
        proc = info["process"]
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        running_processes.pop(sid, None)
        socketio.emit('script_stopped', {'script_id': sid})
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/scripts/<int:sid>/logs')
def api_logs(sid):
    return jsonify({"logs": script_logs.get(sid, [])})

@app.route('/api/scripts/upload', methods=['POST'])
def api_upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.py'):
        return jsonify({"error": "Bad file"}), 400
    filename = secure_filename(file.filename)
    fp = UPLOAD_FOLDER / filename
    file.save(fp)
    script = {"id": abs(hash(str(fp) + str(time.time()))) % 10**8, "name": fp.stem, "path": str(fp),
              "description": f"Uploaded: {filename}", "category": "📂 Uploaded", "manual": True}
    settings.setdefault("scripts", []).append(script)
    save_settings()
    return jsonify({"status": "success", "script": script})

@app.route('/api/folders', methods=['GET', 'POST'])
def manage_folders():
    if request.method == 'POST':
        data = request.json
        settings["monitored_folders"] = data.get("folders", [])
        save_settings()
        return jsonify({"status": "success"})
    return jsonify({"folders": settings.get("monitored_folders", [])})

load_settings()

if __name__ == '__main__':
    print("🚀 UJALA ready – http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
