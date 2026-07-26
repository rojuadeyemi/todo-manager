"""End-to-end API tests against the running server."""
import json
import urllib.request

BASE = "http://localhost:5000"
passed = failed = 0


def call(method, path, token=None, body=None, expect=200):
    global passed, failed
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            status, payload = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        status, payload = e.code, json.loads(e.read() or b"{}")
    ok = status == expect
    passed += ok
    failed += (not ok)
    print(f"{'PASS' if ok else 'FAIL'} {method} {path} -> {status} (want {expect})"
          + ("" if ok else f"  {payload}"))
    return payload


def login(u, p, expect=200):
    return call("POST", "/api/auth/login", body={"username": u, "password": p}, expect=expect)

# --- auth ---
admin = login("admin", "Admin@123")["token"]
tunde = login("tunde", "Tunde@123")["token"]
amaka = login("amaka", "Amaka@123")["token"]
login("admin", "wrongpass", expect=401)
call("GET", "/api/tasks", expect=401)                       # no token
call("GET", "/api/tasks", token="garbage", expect=401)      # bad token

# --- visibility: tunde only sees own/assigned ---
t_tasks = call("GET", "/api/tasks", token=tunde)["tasks"]
me = call("GET", "/api/auth/me", token=tunde)["user"]
bad = [t for t in t_tasks if t["created_by"] != me["id"] and t["assigned_to"] != me["id"]]
print(("PASS" if not bad else "FAIL"), f"visibility: tunde sees {len(t_tasks)} tasks, {len(bad)} leaked")
passed += not bad; failed += bool(bad)

# admin scope=all sees everything
all_tasks = call("GET", "/api/tasks?scope=all", token=admin)["tasks"]
print(("PASS" if len(all_tasks) >= 24 else "FAIL"), f"admin all-scope sees {len(all_tasks)} tasks")
passed += len(all_tasks) >= 24; failed += len(all_tasks) < 24
# non-admin scope=all is ignored (still restricted)
t2 = call("GET", "/api/tasks?scope=all", token=tunde)["tasks"]
print(("PASS" if len(t2) == len(t_tasks) else "FAIL"), "scope=all ignored for non-admin")
passed += len(t2) == len(t_tasks); failed += len(t2) != len(t_tasks)

# --- create + assign + notification ---
amaka_id = call("GET", "/api/auth/me", token=amaka)["user"]["id"]
task = call("POST", "/api/tasks", token=tunde, body={
    "title": "Test assignment flow", "priority": "High", "category": "QA",
    "due_date": "2026-08-01", "assigned_to": amaka_id,
    "description": "check <script>alert(1)</script> escaping"}, expect=201)["task"]
notifs = call("GET", "/api/notifications", token=amaka)
has = any(n["task_id"] == task["id"] for n in notifs["notifications"])
print(("PASS" if has else "FAIL"), "amaka notified of assignment")
passed += has; failed += not has

# assignee can toggle done -> creator notified
call("POST", f"/api/tasks/{task['id']}/toggle", token=amaka)
notifs = call("GET", "/api/notifications", token=tunde)
has = any(n["task_id"] == task["id"] and "completed" in n["message"] for n in notifs["notifications"])
print(("PASS" if has else "FAIL"), "tunde notified of completion")
passed += has; failed += not has

# deletion: the assignee cannot delete, the creator can
call("DELETE", f"/api/tasks/{task['id']}", token=amaka, expect=403)
call("DELETE", f"/api/tasks/{task['id']}", token=tunde)
# the assignee is notified that the task they were working on was deleted
notifs = call("GET", "/api/notifications", token=amaka)
has = any("was deleted by" in n["message"] for n in notifs["notifications"])
print(("PASS" if has else "FAIL"), "amaka notified of deletion")
passed += has; failed += not has
# an admin can also delete someone else's task, and the creator is notified
victim = call("POST", "/api/tasks", token=amaka,
              body={"title": "Admin deletes this", "priority": "Low"}, expect=201)["task"]
call("DELETE", f"/api/tasks/{victim['id']}", token=admin)
notifs = call("GET", "/api/notifications", token=amaka)
has = any(n["message"].startswith('Task "Admin deletes this"') for n in notifs["notifications"])
print(("PASS" if has else "FAIL"), "amaka notified when admin deleted her task")
passed += has; failed += not has
# every task carries a created_at timestamp for the Created column
sample = call("GET", "/api/tasks", token=tunde)["tasks"][0]
ok = bool(sample.get("created_at"))
print(("PASS" if ok else "FAIL"), f"tasks expose created_at ({sample.get('created_at')})")
passed += ok; failed += not ok
# outsider cannot access others' tasks
other = [t for t in all_tasks if t["created_by"] != me["id"] and t["assigned_to"] != me["id"]][0]
call("PUT", f"/api/tasks/{other['id']}", token=tunde,
     body={"title": "hijack"}, expect=403)

# --- validation ---
call("POST", "/api/tasks", token=tunde, body={"title": ""}, expect=400)
call("POST", "/api/tasks", token=tunde, body={"title": "x", "priority": "Urgent"}, expect=400)
call("POST", "/api/tasks", token=tunde, body={"title": "x", "due_date": "01/02/2026"}, expect=400)
call("POST", "/api/tasks", token=tunde, body={"title": "x", "assigned_to": 9999}, expect=400)

# --- admin user management ---
call("GET", "/api/admin/users", token=tunde, expect=403)
call("POST", "/api/admin/users", token=admin,
     body={"username": "testuser", "password": "Test@1234", "display_name": "Test User"}, expect=201)
call("POST", "/api/admin/users", token=admin,
     body={"username": "testuser", "password": "Test@1234"}, expect=409)  # duplicate
call("POST", "/api/admin/users", token=admin,
     body={"username": "x", "password": "Test@1234"}, expect=400)         # short username
call("POST", "/api/admin/users", token=admin,
     body={"username": "okname", "password": "short"}, expect=400)        # short pw
new = login("testuser", "Test@1234")
uid = new["user"]["id"]
# deactivate -> session revoked, login blocked
call("PUT", f"/api/admin/users/{uid}", token=admin, body={"is_active": False})
call("GET", "/api/tasks", token=new["token"], expect=401)
login("testuser", "Test@1234", expect=401)
# reactivate + promote
call("PUT", f"/api/admin/users/{uid}", token=admin, body={"is_active": True, "role": "admin"})
tok2 = login("testuser", "Test@1234")["token"]
call("GET", "/api/admin/users", token=tok2)  # now admin
# admin cannot demote/deactivate self
admin_id = call("GET", "/api/auth/me", token=admin)["user"]["id"]
call("PUT", f"/api/admin/users/{admin_id}", token=admin, body={"role": "user"}, expect=400)
call("PUT", f"/api/admin/users/{admin_id}", token=admin, body={"is_active": False}, expect=400)

# --- password change ---
call("POST", "/api/auth/password", token=tok2,
     body={"current_password": "wrong", "new_password": "NewPass@123"}, expect=400)
call("POST", "/api/auth/password", token=tok2,
     body={"current_password": "Test@1234", "new_password": "NewPass@123"})
login("testuser", "Test@1234", expect=401)
login("testuser", "NewPass@123")

# --- logout kills token ---
call("POST", "/api/auth/logout", token=amaka)
call("GET", "/api/tasks", token=amaka, expect=401)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
