# Todo Manager

A multi-user todo app: HTML/CSS/JS frontend + a small Flask/SQLite backend
(Python is used only for what the browser cannot do — the shared database,
authentication, and user management).

## Run it

```bash
pip install flask        # one-time
python server.py
```

Then open **http://localhost:5000** in your browser.

The SQLite database (`todo.db`) is created and seeded automatically on first
run. Delete `todo.db` and restart to reset to fresh demo data.

## Demo accounts

| Username | Password    | Role  |
|----------|-------------|-------|
| `admin`  | `Admin@123` | admin |
| `tunde`  | `Tunde@123` | user  |
| `amaka`  | `Amaka@123` | user  |
| `david`  | `David@123` | user  |

Change these passwords (sidebar → **Password**, or the admin page) before
using the app for real work.

## Features

- **Login / token management** — sessions expire after 8 hours (absolute) or
  30 minutes of inactivity, whichever comes first. The topbar shows a live
  session countdown; expired sessions are logged out automatically.
- **Roles** — admins see an **Admin** page where they create users (user or
  admin role), promote/demote, deactivate/reactivate, and reset passwords.
  Deactivated users cannot sign in and their sessions are revoked instantly.
- **Privacy** — regular users only see tasks they created or that are
  assigned to them. Admins can switch the sidebar **Scope** to "All users".
- **Tasks** — title, description, category, priority, due date, assignee;
  mark done/undone with the checkbox; edit and delete (delete is limited to
  the creator or an admin). Overdue tasks are flagged automatically.
- **Assignment + notifications** — assigning a task notifies the assignee
  (bell icon, polled every 30 s); completing someone else's task notifies
  the creator. Assigned tasks stay visible on the assigner's board with
  their live status.
- **Sidebar** — status filter (All / Pending / Completed / Overdue),
  priority filter, sort (newest, oldest, priority, due date, title), and a
  toggle switch that shows/hides the new-task form.
- **Dashboard** — total / pending / completed / overdue tiles, % completion
  meter, 14-day daily-entry trend line, priority donut, and category
  breakdown. Every chart and tile reacts to the sidebar filters and the
  search box.
- **Security details** — passwords hashed with PBKDF2-HMAC-SHA256 (200k
  iterations, per-user salt); only token hashes are stored server-side; all
  user content is HTML-escaped in the UI.

## Files

```
server.py            Flask API + SQLite schema + demo seed
static/index.html    Login, app shell, dashboard, tasks, admin page
static/style.css     Styling (dark theme)
static/script.js     SPA logic, charts, auth handling
requirements.txt     flask
todo.db              created at first run (not part of the source)
```
