import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, date

from flask import Flask, g, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "todo.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):  # normalize legacy scheme
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
IS_PG = bool(DATABASE_URL)

if not IS_PG:
    from dotenv import load_dotenv
    load_dotenv()

if IS_PG:
    import psycopg
    from psycopg.rows import dict_row

TOKEN_ABSOLUTE_HOURS = 8
TOKEN_IDLE_MINUTES = 30
PBKDF2_ITERATIONS = 200_000

PRIORITIES = ("High", "Medium", "Low")
ROLES = ("admin", "user")

app = Flask(__name__, static_folder=None)


# ------------------------------------------------------------ db abstraction ---

def q(sql: str) -> str:
    return sql.replace("?", "%s") if IS_PG else sql


class DB:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        return self.conn.execute(q(sql), params)

    def insert(self, sql, params=()):
        """Run an INSERT and return the new row id on both backends."""
        if IS_PG:
            cur = self.conn.execute(q(sql + " RETURNING id"), params)
            return cur.fetchone()["id"]
        return self.conn.execute(sql, params).lastrowid

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def connect() -> DB:
    if IS_PG:
        return DB(psycopg.connect(DATABASE_URL, row_factory=dict_row))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return DB(conn)


def get_db() -> DB:
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


PK = ("INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY" if IS_PG
      else "INTEGER PRIMARY KEY AUTOINCREMENT")
USERNAME_COL = ("TEXT NOT NULL" if IS_PG
                else "TEXT NOT NULL UNIQUE COLLATE NOCASE")

SCHEMA_STATEMENTS = [
    f"""CREATE TABLE IF NOT EXISTS users (
        id            {PK},
        username      {USERNAME_COL},
        display_name  TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        salt          TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
        is_active     INTEGER NOT NULL DEFAULT 1,
        created_at    TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS sessions (
        token_hash  TEXT PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        last_active TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS tasks (
        id           {PK},
        title        TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        category     TEXT NOT NULL DEFAULT 'General',
        priority     TEXT NOT NULL DEFAULT 'Medium' CHECK (priority IN ('High','Medium','Low')),
        status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed')),
        due_date     TEXT,
        requested_by TEXT NOT NULL DEFAULT '',
        created_by   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        assigned_to  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        completed_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS notifications (
        id         {PK},
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        task_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
        message    TEXT NOT NULL,
        is_read    INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
]
if IS_PG:
    SCHEMA_STATEMENTS.append(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_ci "
        "ON users (LOWER(username))")


# ---------------------------------------------------------------- security ---

def hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                             PBKDF2_ITERATIONS)
    return dk.hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def current_user():
    """Validate the Bearer token; returns the user row or None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    db = get_db()
    row = db.execute(
        "SELECT s.token_hash, s.expires_at, s.last_active, u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (hash_token(token),),
    ).fetchone()
    if row is None or not row["is_active"]:
        return None
    now = datetime.utcnow()
    expires_at = datetime.fromisoformat(row["expires_at"])
    last_active = datetime.fromisoformat(row["last_active"])
    if now >= expires_at or (now - last_active) > timedelta(minutes=TOKEN_IDLE_MINUTES):
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        db.commit()
        return None
    db.execute("UPDATE sessions SET last_active = ? WHERE token_hash = ?",
               (now_iso(), row["token_hash"]))
    db.commit()
    return row


def require_auth(admin=False):
    user = current_user()
    if user is None:
        return None, (jsonify(error="Not authenticated or session expired"), 401)
    if admin and user["role"] != "admin":
        return None, (jsonify(error="Admin access required"), 403)
    return user, None


def user_public(row):
    return {"id": row["id"], "username": row["username"],
            "display_name": row["display_name"], "role": row["role"],
            "is_active": bool(row["is_active"]), "created_at": row["created_at"]}


def task_public(row):
    d = dict(row)
    d["overdue"] = bool(
        d["status"] == "pending" and d["due_date"]
        and d["due_date"] < date.today().isoformat()
    )
    return d


def notify(db, user_id, task_id, message):
    db.execute(
        "INSERT INTO notifications (user_id, task_id, message, created_at) "
        "VALUES (?,?,?,?)", (user_id, task_id, message, now_iso()))


TASK_SELECT = """
SELECT t.*, c.username AS created_by_name, c.display_name AS created_by_display,
       a.username AS assigned_to_name, a.display_name AS assigned_to_display
FROM tasks t
JOIN users c ON c.id = t.created_by
LEFT JOIN users a ON a.id = t.assigned_to
"""


# -------------------------------------------------------------------- auth ---

@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
                      (username,)).fetchone()
    if user is None or not user["is_active"] or not verify_password(
            password, user["salt"], user["password_hash"]):
        return jsonify(error="Invalid username or password"), 401
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = now + timedelta(hours=TOKEN_ABSOLUTE_HOURS)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_active) "
        "VALUES (?,?,?,?,?)",
        (hash_token(token), user["id"], now_iso(),
         expires.strftime("%Y-%m-%dT%H:%M:%S"), now_iso()))
    # opportunistic housekeeping: dead sessions + old read notifications
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    cutoff = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("DELETE FROM notifications WHERE is_read = 1 AND created_at < ?",
               (cutoff,))
    db.commit()
    return jsonify(token=token,
                   expires_at=expires.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                   idle_minutes=TOKEN_IDLE_MINUTES,
                   user=user_public(user))


@app.post("/api/auth/logout")
def logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token_hash = ?",
                   (hash_token(auth[7:].strip()),))
        db.commit()
    return jsonify(ok=True)


@app.get("/api/auth/me")
def me():
    user, err = require_auth()
    if err:
        return err
    return jsonify(user=user_public(user))


@app.post("/api/auth/password")
def change_password():
    user, err = require_auth()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    if not verify_password(current, user["salt"], user["password_hash"]):
        return jsonify(error="Current password is incorrect"), 400
    if len(new) < 8:
        return jsonify(error="New password must be at least 8 characters"), 400
    db = get_db()
    salt = secrets.token_hex(16)
    db.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
               (hash_password(new, salt), salt, user["id"]))
    # kill every other session for this user
    db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
               (user["id"], user["token_hash"]))
    db.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------- users ---

@app.get("/api/users")
def list_users():
    """Assignable users (any authenticated user) — active accounts only."""
    _, err = require_auth()
    if err:
        return err
    db = get_db()
    rows = db.execute(
        "SELECT id, username, display_name, role FROM users "
        "WHERE is_active = 1 ORDER BY display_name").fetchall()
    return jsonify(users=[dict(r) for r in rows])


@app.get("/api/admin/users")
def admin_list_users():
    _, err = require_auth(admin=True)
    if err:
        return err
    db = get_db()
    rows = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    users = []
    for r in rows:
        u = user_public(r)
        u["task_count"] = db.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE created_by = ? OR assigned_to = ?",
            (r["id"], r["id"])).fetchone()["n"]
        users.append(u)
    return jsonify(users=users)


@app.post("/api/admin/users")
def admin_create_user():
    admin, err = require_auth(admin=True)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    display_name = (data.get("display_name") or "").strip() or username
    password = data.get("password") or ""
    role = data.get("role") or "user"
    if not re.fullmatch(r"[A-Za-z0-9._@-]{3,100}", username):
        return jsonify(error="Username must be 3-100 chars (letters, digits, _ . - @)"), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters"), 400
    db = get_db()
    if db.execute("SELECT 1 AS x FROM users WHERE LOWER(username) = LOWER(?)",
                  (username,)).fetchone():
        return jsonify(error="Username already exists"), 409
    salt = secrets.token_hex(16)
    new_id = db.insert(
        "INSERT INTO users (username, display_name, password_hash, salt, role, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (username, display_name, hash_password(password, salt), salt, role, now_iso()))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id = ?", (new_id,)).fetchone()
    return jsonify(user=user_public(row)), 201

@app.put("/api/admin/users/<int:user_id>")
def admin_update_user(user_id):
    admin, err = require_auth(admin=True)
    if err:
        return err
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        return jsonify(error="User not found"), 404
    data = request.get_json(silent=True) or {}

    if "role" in data:
        role = data["role"]
        if role not in ROLES:
            return jsonify(error="Invalid role"), 400
        if target["id"] == admin["id"] and role != "admin":
            return jsonify(error="You cannot demote your own account"), 400
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

    if "is_active" in data:
        active = 1 if data["is_active"] else 0
        if target["id"] == admin["id"] and not active:
            return jsonify(error="You cannot deactivate your own account"), 400
        db.execute("UPDATE users SET is_active = ? WHERE id = ?", (active, user_id))
        if not active:
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    if "display_name" in data and (data["display_name"] or "").strip():
        db.execute("UPDATE users SET display_name = ? WHERE id = ?",
                   (data["display_name"].strip(), user_id))

    if data.get("password"):
        if len(data["password"]) < 8:
            return jsonify(error="Password must be at least 8 characters"), 400
        salt = secrets.token_hex(16)
        db.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                   (hash_password(data["password"], salt), salt, user_id))
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    db.commit()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify(user=user_public(row))


# ------------------------------------------------------------------- tasks ---

def visible_tasks_query(user):
    if user["role"] == "admin" and request.args.get("scope") == "all":
        return TASK_SELECT, ()
    return TASK_SELECT + " WHERE t.created_by = ? OR t.assigned_to = ?", \
        (user["id"], user["id"])


@app.get("/api/tasks")
def get_tasks():
    user, err = require_auth()
    if err:
        return err
    db = get_db()
    sql, params = visible_tasks_query(user)
    rows = db.execute(sql + " ORDER BY t.created_at DESC", params).fetchall()
    return jsonify(tasks=[task_public(r) for r in rows])


def validate_task_payload(data, db):
    title = (data.get("title") or "").strip()
    if not title:
        return None, "Title is required"
    priority = data.get("priority") or "Medium"
    if priority not in PRIORITIES:
        return None, "Priority must be High, Medium or Low"
    due_date = (data.get("due_date") or "").strip() or None
    if due_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date):
        return None, "Due date must be YYYY-MM-DD"
    assigned_to = data.get("assigned_to") or None
    if assigned_to is not None:
        row = db.execute("SELECT id FROM users WHERE id = ? AND is_active = 1",
                         (assigned_to,)).fetchone()
        if row is None:
            return None, "Assignee not found or inactive"
    return {
        "title": title,
        "description": (data.get("description") or "").strip(),
        "category": (data.get("category") or "").strip() or "General",
        "requested_by": (data.get("requested_by") or "").strip(),
        "priority": priority,
        "due_date": due_date,
        "assigned_to": assigned_to,
    }, None


@app.post("/api/tasks")
def create_task():
    user, err = require_auth()
    if err:
        return err
    db = get_db()
    payload, msg = validate_task_payload(request.get_json(silent=True) or {}, db)
    if msg:
        return jsonify(error=msg), 400
    now = now_iso()
    task_id = db.insert(
        "INSERT INTO tasks (title, description, category, priority, due_date, requested_by, "
        "created_by, assigned_to, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (payload["title"], payload["description"], payload["category"],
         payload["priority"], payload["due_date"], payload["requested_by"], user["id"],
         payload["assigned_to"], now, now))
    if payload["assigned_to"] and payload["assigned_to"] != user["id"]:
        notify(db, payload["assigned_to"], task_id,
               f'{user["display_name"]} assigned you a task: "{payload["title"]}"')
    db.commit()
    row = db.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    return jsonify(task=task_public(row)), 201

def get_task_or_403(task_id, user, db, *, owner_only=False):
    """owner_only=True restricts the action to the task's creator (or an admin);
    otherwise the assignee may act on it too."""
    row = db.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    if row is None:
        return None, (jsonify(error="Task not found"), 404)
    is_admin = user["role"] == "admin"
    is_owner = row["created_by"] == user["id"]
    is_assignee = row["assigned_to"] == user["id"]
    if owner_only:
        if not (is_admin or is_owner):
            return None, (jsonify(
                error="Only the person who created this task (or an admin) can delete it"), 403)
        return row, None
    if not (is_admin or is_owner or is_assignee):
        return None, (jsonify(error="You do not have access to this task"), 403)
    return row, None


@app.put("/api/tasks/<int:task_id>")
def update_task(task_id):
    user, err = require_auth()
    if err:
        return err
    db = get_db()
    task, err = get_task_or_403(task_id, user, db)
    if err:
        return err
    payload, msg = validate_task_payload(request.get_json(silent=True) or {}, db)
    if msg:
        return jsonify(error=msg), 400
    old_assignee = task["assigned_to"]
    db.execute("UPDATE tasks SET title=?, description=?, category=?, priority=?, "
        "due_date=?, requested_by=?, assigned_to=?, updated_at=? WHERE id=?",
        (payload["title"], payload["description"], payload["category"],
         payload["priority"], payload["due_date"], payload["requested_by"],
         payload["assigned_to"], now_iso(), task_id))
    new_assignee = payload["assigned_to"]
    if new_assignee and new_assignee != old_assignee and new_assignee != user["id"]:
        notify(db, new_assignee, task_id,
               f'{user["display_name"]} assigned you a task: "{payload["title"]}"')
    db.commit()
    row = db.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    return jsonify(task=task_public(row))


@app.post("/api/tasks/<int:task_id>/toggle")
def toggle_task(task_id):
    user, err = require_auth()
    if err:
        return err
    db = get_db()
    task, err = get_task_or_403(task_id, user, db)
    if err:
        return err
    if task["status"] == "pending":
        db.execute("UPDATE tasks SET status='completed', completed_at=?, updated_at=? "
                   "WHERE id=?", (now_iso(), now_iso(), task_id))
        # tell the creator when someone else completes their task
        if task["created_by"] != user["id"]:
            notify(db, task["created_by"], task_id,
                   f'{user["display_name"]} completed "{task["title"]}"')
        # tell the assignee when the creator closes it
        elif task["assigned_to"] and task["assigned_to"] != user["id"]:
            notify(db, task["assigned_to"], task_id,
                   f'"{task["title"]}" was marked completed by {user["display_name"]}')
    else:
        db.execute("UPDATE tasks SET status='pending', completed_at=NULL, updated_at=? "
                   "WHERE id=?", (now_iso(), task_id))
    db.commit()
    row = db.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    return jsonify(task=task_public(row))


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id):
    user, err = require_auth()
    if err:
        return err
    db = get_db()
    task, err = get_task_or_403(task_id, user, db, owner_only=True)
    if err:
        return err
    # let the people involved know their task disappeared
    for uid in {task["assigned_to"], task["created_by"]} - {None, user["id"]}:
        notify(db, uid, None,
               f'Task "{task["title"]}" was deleted by {user["display_name"]}')
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------- notifications ---

@app.get("/api/notifications")
def get_notifications():
    user, err = require_auth()
    if err:
        return err
    db = get_db()
    rows = db.execute(
        "SELECT * FROM notifications WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT 50", (user["id"],)).fetchall()
    unread = db.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND is_read = 0",
        (user["id"],)).fetchone()["n"]
    return jsonify(notifications=[dict(r) for r in rows], unread=unread)


@app.post("/api/notifications/read")
def mark_notifications_read():
    user, err = require_auth()
    if err:
        return err
    db = get_db()
    db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user["id"],))
    db.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ static ---

@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)

SEED_DEMO = os.environ.get("SEED_DEMO", "" if IS_PG else "1") == "1"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Administrator")

print()


def seed(db: DB):
    """Runs only on an empty database."""
    import random
    random.seed(7)

    def add_user(username, display, password, role="user"):
        salt = secrets.token_hex(16)
        return db.insert(
            "INSERT INTO users (username, display_name, password_hash, salt, role, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (username, display, hash_password(password, salt), salt, role, now_iso()))

    admin = add_user(ADMIN_USERNAME, ADMIN_NAME, ADMIN_PASSWORD, "admin")
    if not SEED_DEMO:
        print(f"Created admin account '{ADMIN_USERNAME}' (no demo data — SEED_DEMO is off)")
        return

    tunde = add_user("tunde", "Tunde Bakare", "Tunde@123")
    amaka = add_user("amaka", "Amaka Obi", "Amaka@123")
    david = add_user("david", "David Ola", "David@123")
    users = [admin, tunde, amaka, david]

    samples = [
        ("Design landing page hero", "Design", "High"),
        ("Fix login redirect bug", "Bug", "High"),
        ("Prepare Q3 budget review", "Finance", "High"),
        ("Write API documentation", "Docs", "Medium"),
        ("Research competitor pricing", "Research", "Medium"),
        ("Update onboarding emails", "Marketing", "Medium"),
        ("Refactor payment module", "Engineering", "High"),
        ("Plan sprint retrospective", "Planning", "Low"),
        ("Backup production database", "Ops", "High"),
        ("Review pull requests", "Engineering", "Medium"),
        ("Draft newsletter content", "Marketing", "Low"),
        ("Test mobile responsiveness", "QA", "Medium"),
        ("Set up CI pipeline", "Ops", "High"),
        ("Interview support candidate", "HR", "Medium"),
        ("Clean up analytics dashboard", "Data", "Low"),
        ("Migrate DNS records", "Ops", "High"),
        ("Audit access permissions", "Security", "High"),
        ("Update dependency versions", "Engineering", "Low"),
        ("Customer feedback analysis", "Research", "Medium"),
        ("Prepare investor deck", "Finance", "High"),
        ("Organise team offsite", "HR", "Low"),
        ("Optimise image loading", "Engineering", "Medium"),
        ("Write unit tests for auth", "Engineering", "High"),
        ("Translate app to French", "Product", "Low"),
    ]

    today = date.today()
    for title, category, priority in samples:
        created_offset = random.randint(0, 20)          # created within last 3 weeks
        created_dt = datetime.utcnow() - timedelta(days=created_offset,
                                                   hours=random.randint(0, 9))
        due = today + timedelta(days=random.randint(-6, 12))  # some overdue
        creator = random.choice(users)
        assignee = random.choice(users + [None, None])
        done = random.random() < 0.45
        completed_at = (created_dt + timedelta(days=random.randint(0, 4))
                        ).strftime("%Y-%m-%dT%H:%M:%S") if done else None
        task_id = db.insert(
            "INSERT INTO tasks (title, description, category, priority, status, due_date, "
            "created_by, assigned_to, created_at, updated_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (title, f"{title} — seeded demo task for testing the dashboard and filters.",
             category, priority, "completed" if done else "pending",
             due.isoformat(), creator, assignee,
             created_dt.strftime("%Y-%m-%dT%H:%M:%S"),
             created_dt.strftime("%Y-%m-%dT%H:%M:%S"), completed_at))
        if assignee and assignee != creator:
            creator_name = db.execute(
                "SELECT display_name FROM users WHERE id=?",
                (creator,)).fetchone()["display_name"]
            db.execute(
                "INSERT INTO notifications (user_id, task_id, message, is_read, created_at) "
                "VALUES (?,?,?,?,?)",
                (assignee, task_id,
                 f'{creator_name} assigned you a task: "{title}"',
                 1 if random.random() < 0.6 else 0,
                 created_dt.strftime("%Y-%m-%dT%H:%M:%S")))


def init_db():
    db = connect()
    for stmt in SCHEMA_STATEMENTS:
        db.execute(stmt)
    db.commit()
    # columns added after first release — safe to run every startup
    try:
        db.execute("ALTER TABLE tasks ADD COLUMN requested_by TEXT NOT NULL DEFAULT ''")
        db.commit()
    except Exception:
        db.conn.rollback()      # if already exists
    has_users = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] > 0
    if not has_users:
        seed(db)
        db.commit()
        if SEED_DEMO:
            print(f"Seeded demo data → {ADMIN_USERNAME}/{ADMIN_PASSWORD}, "
                  "tunde/Tunde@123, amaka/Amaka@123, david/David@123")
    db.close()


init_db()
print(f"Database backend: {'PostgreSQL' if IS_PG else 'SQLite (todo.db)'}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Todo Manager running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
