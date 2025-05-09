import os
import base64
import requests
import psycopg2
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

# Flask setup
app = Flask(__name__)
CORS(app)

# GitHub config
REPO_OWNER = "bgridtech"
REPO_LIST = ["images", "images1", "images2", "images3"]
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Set in Vercel

# NeonDB config
DB_PARAMS = {
    "host": "ep-crimson-pine-a1dwinf0-pooler.ap-southeast-1.aws.neon.tech",
    "dbname": "neondb",
    "user": "neondb_owner",
    "password": os.getenv("NEON_PASS"),
    "sslmode": "require"
}

def get_db_conn():
    return psycopg2.connect(**DB_PARAMS)

def get_next_repo_index():
    """Read and increment repo index."""
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
    """Insert upload metadata into details table."""
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO details (filename, url, repo) VALUES (%s, %s, %s);",
                (filename, url, repo)
            )
        conn.commit()

def upload_to_github(repo, orig_filename, data_bytes):
    """
    Uploads image to GitHub. Returns (timestamped_filename, raw_url).
    """
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
    resp = requests.put(api_url, json=payload, headers=headers)
    resp.raise_for_status()
    raw_url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{repo}/main/{path}"
    return ts_filename, raw_url

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload_image():
    if "image" not in request.files:
        return jsonify({"error": "No image file sent"}), 400

    file = request.files["image"]
    data = file.read()
    orig_name = file.filename

    try:
        # Choose repo by round-robin
        idx = get_next_repo_index()
        repo = REPO_LIST[idx]

        # Upload image to GitHub
        ts_name, raw_url = upload_to_github(repo, orig_name, data)

        # Save metadata to NeonDB
        record_detail(ts_name, raw_url, repo)

        return jsonify({"url": raw_url}), 200

    except requests.HTTPError as e:
        return jsonify({"error": f"GitHub API error: {e.response.json()}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
