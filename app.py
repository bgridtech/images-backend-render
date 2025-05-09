# >>> MONKEY PATCHING MUST COME FIRST <<<
import eventlet
eventlet.monkey_patch()

# THEN IMPORT EVERYTHING ELSE
import os
import base64
import requests
import psycopg2
from datetime import datetime
from flask import Flask, render_template, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Flask + SocketIO setup
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

# GitHub config
REPO_OWNER = "bgridtech"
REPO_LIST = ["images", "images1", "images2", "images3"]
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# NeonDB config
DB_PARAMS = {
    "host": "ep-crimson-pine-a1dwinf0-pooler.ap-southeast-1.aws.neon.tech",
    "dbname": "neondb",
    "user": "neondb_owner",
    "password": os.getenv("NEON_PASS"),
    "sslmode": "require"
}

# Track uploads by session
uploads = {}

def get_db_conn():
    return psycopg2.connect(**DB_PARAMS)

def get_next_repo_index():
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT num FROM save LIMIT 1;")
            current = cur.fetchone()[0]
            idx = current % len(REPO_LIST)
            next_idx = (current + 1) % len(REPO_LIST)
            cur.execute("UPDATE save SET num = %s;", (next_idx,))
        conn.commit()
    return idx

def record_detail(filename, url, repo):
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO details (filename, url, repo) VALUES (%s, %s, %s);",
                (filename, url, repo)
            )
        conn.commit()

def upload_to_github(repo, orig_filename, data_bytes):
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    ts_filename = f"{timestamp}_{orig_filename}"
    path = f"uploads/{ts_filename}"
    api_url = f"https://api.github.com/repos/{REPO_OWNER}/{repo}/contents/{path}"
    payload = {
        "message": f"Upload {ts_filename}",
        "content": base64.b64encode(data_bytes).decode("utf-8"),
        "branch": "main"
    }
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    # GitHub upload
    resp = requests.put(api_url, json=payload, headers=headers)
    resp.raise_for_status()

    raw_url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{repo}/main/{path}"
    return ts_filename, raw_url

@app.route("/")
def index():
    return render_template("index.html")

# === FRONTEND MATCHING EVENTS ===

@socketio.on("upload_chunk")
def handle_upload_chunk(data):
    sid = request.sid
    chunk_b64 = data['chunk']
    try:
        chunk_bytes = base64.b64decode(chunk_b64)
    except Exception:
        emit("upload_status", {"stage": "error", "message": "Invalid base64 chunk"})
        return

    if sid not in uploads:
        uploads[sid] = {'filename': None, 'chunks': []}
    
    uploads[sid]['chunks'].append(chunk_bytes)

@socketio.on("upload_done")
def handle_upload_done(data):
    sid = request.sid
    try:
        filename = data.get('filename')
        if sid not in uploads or not uploads[sid]['chunks']:
            emit("upload_status", {"stage": "error", "message": "No chunks received"})
            return

        uploads[sid]['filename'] = filename
        file_chunks = uploads[sid]['chunks']
        full_data = b''.join(file_chunks)
        del uploads[sid]

        # Choose GitHub repo
        idx = get_next_repo_index()
        repo = REPO_LIST[idx]

        # Notify frontend
        emit("upload_status", {"stage": "github_started"})

        # Upload to GitHub
        ts_name, raw_url = upload_to_github(repo, filename, full_data)

        # Save to database
        record_detail(ts_name, raw_url, repo)

        # Notify frontend
        emit("upload_status", {"stage": "done", "url": raw_url})

    except requests.HTTPError as e:
        err = e.response.json()
        emit("upload_status", {"stage": "error", "message": err})
    except Exception as e:
        emit("upload_status", {"stage": "error", "message": str(e)})

# === SERVER START ===
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
