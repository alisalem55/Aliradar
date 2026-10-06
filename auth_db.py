"""الحسابات والجلسات وقوائم المتابعة.

التخزين: Postgres إذا وُجد DATABASE_URL (يبقى بعد إعادة تشغيل التطبيق)،
وإلا ملف SQLite محلي (يُفقد عند إعادة تشغيل Streamlit Cloud).
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.environ.get("RADAR_DB", os.path.join(HERE, "radar.db"))
SESSION_DAYS = 30
USER_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")

_lock = threading.Lock()
_fails = {}  # محاولات الدخول الفاشلة: username -> [count, first_time]


def _secret(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def _url():
    return _secret("DATABASE_URL")


def using_postgres():
    return _url().startswith("postgres")


class _Conn:
    """غلاف صغير يوحّد sqlite3 وpsycopg2: علامات ? ومعاملة واحدة."""

    def __enter__(self):
        url = _url()
        self.pg = url.startswith("postgres")
        if self.pg:
            import psycopg2
            self.c = psycopg2.connect(url, connect_timeout=10)
        else:
            self.c = sqlite3.connect(SQLITE_PATH, timeout=10)
            self.c.row_factory = sqlite3.Row
        self.cur = self.c.cursor()
        return self

    def q(self, sql, args=()):
        if self.pg:
            sql = sql.replace("?", "%s")
        self.cur.execute(sql, args)
        return self

    def all(self):
        cols = [d[0] for d in self.cur.description]
        return [dict(zip(cols, r)) for r in self.cur.fetchall()]

    def one(self):
        rows = self.all()
        return rows[0] if rows else None

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.c.commit()
            else:
                self.c.rollback()
        finally:
            self.c.close()
        return False


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, role TEXT NOT NULL,
        status TEXT NOT NULL, created TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS watch (
        username TEXT NOT NULL, wkey TEXT NOT NULL, price REAL, wdate TEXT,
        PRIMARY KEY (username, wkey))""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires TEXT NOT NULL)""",
]
_inited = False


def init():
    global _inited
    if _inited:
        return
    with _lock:
        if _inited:
            return
        with _Conn() as c:
            for s in SCHEMA:
                c.q(s)
        _inited = True


def _now():
    return dt.datetime.utcnow().isoformat(timespec="seconds")


# ---------- كلمات المرور ----------
def _hash_pw(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + h.hex()


def _check_pw(pw, stored):
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def _norm(username):
    return (username or "").strip().lower()


# ---------- الحسابات ----------
def user_count():
    init()
    with _Conn() as c:
        return c.q("SELECT COUNT(*) AS n FROM users").one()["n"]


def admin_count():
    init()
    with _Conn() as c:
        return c.q("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'").one()["n"]


def first_admin_name():
    return _norm(_secret("FIRST_ADMIN"))


def register(username, password):
    """يعيد (ok, message, role, status). أول حساب يصبح المدير ومعتمدًا."""
    init()
    u = _norm(username)
    if not USER_RE.match(u):
        return False, "اسم المستخدم من 3 إلى 32 حرفًا: أحرف إنجليزية وأرقام و _ . -", None, None
    if len(password or "") < 8:
        return False, "كلمة المرور 8 أحرف على الأقل.", None, None
    ph = _hash_pw(password)
    with _lock:
        with _Conn() as c:
            if c.q("SELECT 1 AS x FROM users WHERE username = ?", (u,)).one():
                return False, "اسم المستخدم مستخدم، اختر اسمًا آخر.", None, None
            no_admin = c.q("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'").one()["n"] == 0
            wanted = _norm(_secret("FIRST_ADMIN"))
            # أول حساب يصبح المدير، أو الاسم المحدد في FIRST_ADMIN إن وُضع
            first = no_admin and (not wanted or u == wanted)
            role, status = ("admin", "approved") if first else ("user", "pending")
            c.q("INSERT INTO users (username, pw_hash, role, status, created) VALUES (?, ?, ?, ?, ?)",
                (u, ph, role, status, _now()))
    if first:
        return True, "تم إنشاء حسابك وأنت المدير. يمكنك الدخول الآن.", role, status
    return True, "تم إنشاء حسابك وهو بانتظار اعتماد المدير.", role, status


def login(username, password):
    """يعيد (ok, message, user_dict)."""
    init()
    u = _norm(username)
    now = time.time()
    f = _fails.get(u)
    if f and f[0] >= 5 and now - f[1] < 300:
        return False, "محاولات كثيرة خاطئة. انتظر 5 دقائق ثم أعد المحاولة.", None
    with _Conn() as c:
        row = c.q("SELECT * FROM users WHERE username = ?", (u,)).one()
    ok = bool(row) and _check_pw(password or "", row["pw_hash"])
    if not ok:
        if not f or now - f[1] >= 300:
            _fails[u] = [1, now]
        else:
            f[0] += 1
        return False, "اسم المستخدم أو كلمة المرور غير صحيحة.", None
    _fails.pop(u, None)
    if row["status"] == "pending":
        return False, "حسابك بانتظار اعتماد المدير.", None
    if row["status"] != "approved":
        return False, "تم إيقاف هذا الحساب. تواصل مع المدير.", None
    return True, "", {"username": row["username"], "role": row["role"]}


def list_users():
    init()
    with _Conn() as c:
        return c.q("SELECT username, role, status, created FROM users ORDER BY created").all()


def _admins_left(c, excluding):
    r = c.q("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND status = 'approved' AND username <> ?",
            (excluding,)).one()
    return r["n"]


def set_status(username, status, actor):
    """status: approved | rejected | suspended."""
    init()
    u = _norm(username)
    with _Conn() as c:
        row = c.q("SELECT role FROM users WHERE username = ?", (u,)).one()
        if not row:
            return False, "الحساب غير موجود."
        if row["role"] == "admin" and status != "approved" and _admins_left(c, u) == 0:
            return False, "لا يمكن إيقاف آخر مدير."
        if u == actor and status != "approved":
            return False, "لا يمكنك إيقاف حسابك."
        c.q("UPDATE users SET status = ? WHERE username = ?", (status, u))
        if status != "approved":
            c.q("DELETE FROM sessions WHERE username = ?", (u,))
    return True, ""


def set_role(username, role, actor):
    init()
    u = _norm(username)
    with _Conn() as c:
        row = c.q("SELECT role FROM users WHERE username = ?", (u,)).one()
        if not row:
            return False, "الحساب غير موجود."
        if row["role"] == "admin" and role != "admin" and _admins_left(c, u) == 0:
            return False, "لا يمكن إزالة صلاحية آخر مدير."
        c.q("UPDATE users SET role = ? WHERE username = ?", (role, u))
    return True, ""


def delete_user(username, actor):
    init()
    u = _norm(username)
    if u == actor:
        return False, "لا يمكنك حذف حسابك."
    with _Conn() as c:
        row = c.q("SELECT role FROM users WHERE username = ?", (u,)).one()
        if not row:
            return False, "الحساب غير موجود."
        if row["role"] == "admin" and _admins_left(c, u) == 0:
            return False, "لا يمكن حذف آخر مدير."
        for t in ("watch", "sessions"):
            c.q("DELETE FROM " + t + " WHERE username = ?", (u,))
        c.q("DELETE FROM users WHERE username = ?", (u,))
    return True, ""


def change_password(username, old, new):
    init()
    u = _norm(username)
    if len(new or "") < 8:
        return False, "كلمة المرور الجديدة 8 أحرف على الأقل."
    with _Conn() as c:
        row = c.q("SELECT pw_hash FROM users WHERE username = ?", (u,)).one()
        if not row or not _check_pw(old or "", row["pw_hash"]):
            return False, "كلمة المرور الحالية غير صحيحة."
        c.q("UPDATE users SET pw_hash = ? WHERE username = ?", (_hash_pw(new), u))
        c.q("DELETE FROM sessions WHERE username = ?", (u,))
    return True, ""


def admin_reset_password(username, new):
    init()
    u = _norm(username)
    if len(new or "") < 8:
        return False, "كلمة المرور 8 أحرف على الأقل."
    with _Conn() as c:
        if not c.q("SELECT 1 AS x FROM users WHERE username = ?", (u,)).one():
            return False, "الحساب غير موجود."
        c.q("UPDATE users SET pw_hash = ? WHERE username = ?", (_hash_pw(new), u))
        c.q("DELETE FROM sessions WHERE username = ?", (u,))
    return True, ""


# ---------- الجلسات (تبقى بعد تحديث الصفحة) ----------
def _th(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(username):
    init()
    token = secrets.token_urlsafe(32)
    exp = (dt.datetime.utcnow() + dt.timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with _Conn() as c:
        c.q("DELETE FROM sessions WHERE expires < ?", (_now(),))
        c.q("INSERT INTO sessions (token_hash, username, expires) VALUES (?, ?, ?)", (_th(token), username, exp))
    return token


def session_user(token):
    if not token:
        return None
    init()
    with _Conn() as c:
        r = c.q("""SELECT u.username AS username, u.role AS role, u.status AS status, s.expires AS expires
                   FROM sessions s JOIN users u ON u.username = s.username WHERE s.token_hash = ?""",
                (_th(token),)).one()
    if not r or r["status"] != "approved" or r["expires"] < _now():
        return None
    return {"username": r["username"], "role": r["role"]}


def end_session(token):
    if not token:
        return
    init()
    with _Conn() as c:
        c.q("DELETE FROM sessions WHERE token_hash = ?", (_th(token),))


# ---------- قائمة المتابعة لكل حساب ----------
def get_watch(username):
    init()
    with _Conn() as c:
        rows = c.q("SELECT wkey, price, wdate FROM watch WHERE username = ?", (username,)).all()
    return {r["wkey"]: {"price": r["price"], "date": r["wdate"]} for r in rows}


def set_watch(username, watch):
    """يستبدل قائمة الحساب بالكامل بما أرسلته الواجهة (مع تحقق من الشكل)."""
    init()
    clean = {}
    for k, v in (watch or {}).items():
        if not isinstance(k, str) or not re.match(r"^(SA|US):[A-Za-z0-9.\-]{1,12}$", k) or len(clean) >= 500:
            continue
        v = v if isinstance(v, dict) else {}
        try:
            price = float(v.get("price"))
        except (TypeError, ValueError):
            price = None
        clean[k] = (price, str(v.get("date") or "")[:10])
    with _Conn() as c:
        c.q("DELETE FROM watch WHERE username = ?", (username,))
        for k, (price, d) in clean.items():
            c.q("INSERT INTO watch (username, wkey, price, wdate) VALUES (?, ?, ?, ?)", (username, k, price, d))
    return True
