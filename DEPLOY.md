# Deploying to Render (free) with Neon Postgres (free)

Render's free web services have an **ephemeral filesystem** — the local
`todo.db` file is wiped on every restart/redeploy — and Render's own free
Postgres **expires after 30 days**. So we host the app on Render and the
database on Neon, whose free tier does not expire.

The app switches automatically: no `DATABASE_URL` → SQLite (local dev);
`DATABASE_URL` set → Postgres (production).

## 1. Create the free Neon database (~2 minutes)

1. Go to https://neon.tech and sign up (GitHub login works).
2. Create a project (any name, pick the region closest to you).
3. On the project dashboard, open **Connect** and copy the connection
   string. It looks like:
   `postgresql://user:password@ep-xxxx.aws.neon.tech/neondb?sslmode=require`

That's it — tables and demo data are created automatically the first time
the app starts against an empty database.

## 2. Push this folder to GitHub

```bash
cd todo-app
git init
git add .
git commit -m "Todo Manager"
# create an empty repo on github.com, then:
git remote add origin https://github.com/<you>/todo-manager.git
git push -u origin main
```

(Add `todo.db` to `.gitignore` — already done — so your local database is
never committed.)

## 3. Create the Render web service

1. Go to https://render.com → **New +** → **Web Service** → connect the repo.
2. Settings:
   - **Runtime**: Python
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `gunicorn --workers 2 --bind 0.0.0.0:$PORT server:app`
   - **Instance type**: Free
3. Under **Environment**, add:
   - `DATABASE_URL` = the Neon connection string from step 1.
   - `ADMIN_USERNAME` = your admin login (optional, default `admin`)
   - `ADMIN_PASSWORD` = a strong password of your own (strongly recommended)
   - `ADMIN_NAME` = your display name (optional)
4. Click **Create Web Service** and wait for the first deploy.

On an empty production (Postgres) database the app creates **only this one
admin account — no demo data**. You then sign in and create your real users
from the Admin page. Demo users and sample tasks are seeded only on local
SQLite, or if you explicitly set `SEED_DEMO=1`.

(Alternatively: **New +** → **Blueprint** picks up `render.yaml` and
pre-fills all of the above; you only paste `DATABASE_URL`.)

## 4. First login

Open your `https://todo-manager-xxxx.onrender.com` URL and sign in with the
admin credentials you set in the environment variables (or `admin/Admin@123`
if you left the defaults — change that password immediately from the
sidebar, since the site is now on the public internet). Then create your
real users from the Admin page.

## Wiping a database that already has demo data

If your Neon database was already seeded with demo data by an earlier
version, open Neon's **SQL Editor** and run:

```sql
DROP TABLE IF EXISTS notifications, sessions, tasks, users CASCADE;
```

Then restart the Render service (Manual Deploy → "Clear build cache &
deploy" is not needed — just **Restart**). The app recreates the tables
with only your admin account. Locally, the equivalent is deleting
`todo.db`.

## Notes and limits worth knowing

- **Cold starts**: free Render services spin down after 15 minutes without
  traffic; the next visit takes ~1 minute to wake. Your data is safe in
  Neon regardless.
- **Neon autosuspend**: Neon's free compute also scales to zero when idle
  and wakes automatically on the next query (adds ~1s to the first request).
  Data is never lost.
- **Resetting demo data**: connect to Neon (their web SQL editor works) and
  run `DROP TABLE notifications, sessions, tasks, users;` — the app reseeds
  on next startup. Locally, just delete `todo.db`.
- **Local dev**: keep running `python server.py` with no env var — SQLite,
  as before. To test against Neon locally:
  `DATABASE_URL="postgresql://…" python server.py`
