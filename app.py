"""رادار الأسهم الحلال: تسجيل دخول، موافقة المدير، قائمة متابعة لكل حساب، وأسعار تتحدث كل 15 دقيقة."""
import datetime as dt
import os
import tempfile
import time
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

import auth_db
import updater

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE = "radar_session"
UI_REFRESH_SECONDS = 900  # تحديث الصفحة المفتوحة كل 15 دقيقة

st.set_page_config(page_title="رادار الأسهم الحلال", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    """<style>
    header[data-testid="stHeader"], #MainMenu, footer { display: none !important; }
    .block-container { padding: 0.6rem 0.75rem 1rem; max-width: 1180px; }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] { direction: rtl; }
    .stMarkdown, label, .stButton, .stAlert, .stTabs, .stCaption { text-align: right; }
    input { direction: ltr; text-align: left; }
    .stButton > button, .stFormSubmitButton > button { width: 100%; min-height: 44px; }
    .stTabs [data-baseweb="tab"] { padding-inline: 12px; }
    h1, h2, h3 { text-wrap: balance; }
    </style>""",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_updater():
    u = updater.Updater()
    u.start()
    return u


@st.cache_resource(show_spinner=False)
def _declare(tag, path):
    return components.declare_component("radar_platform_" + tag, path=path)


def get_component():
    # الواجهة مضمّنة في component_html.py. نعيد قراءته عند كل تشغيل، ونكتبه في مجلد باسم بصمته،
    # فتظهر النسخة الجديدة فورًا دون الحاجة لإعادة تشغيل الخادم أو مسح ذاكرة المتصفح.
    import hashlib
    import importlib

    try:
        import component_html

        component_html = importlib.reload(component_html)
        ui = component_html.HTML
        sha = hashlib.sha256(ui.encode("utf-8")).hexdigest()
        if sha != component_html.SHA256:
            raise ValueError("checksum")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "ملف component_html.py ناقص أو تالف على GitHub (%s). احذفه وأعد رفعه بزر Upload files "
            "من الملف الأصلي، وحجمه الصحيح نحو 27 كيلوبايت." % type(e).__name__
        ) from None
    d = os.path.join(tempfile.gettempdir(), "radar_component_" + sha[:12])
    idx = os.path.join(d, "index.html")
    if not os.path.exists(idx):
        os.makedirs(d, exist_ok=True)
        with open(idx, "w", encoding="utf-8") as fh:
            fh.write(ui)
    return _declare(sha[:12], d)


# ---------- الجلسة: تُحفظ في ذاكرة المتصفح (localStorage) فيبقى الجهاز مسجّلًا ----------
BRIDGE_HTML = """<!doctype html><meta charset="utf-8"><body style="margin:0"><script>
(function () {
  var KEY = 'radar_session_token', lastId = null;
  function post(type, extra) { window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, extra || {}), '*'); }
  function get() { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } }
  var sentOnce = false;
  window.addEventListener('message', function (ev) {
    var m = ev.data;
    if (!m || m.type !== 'streamlit:render') return;
    var c = (m.args || {}).cmd;
    if (c && c.id !== lastId) {
      lastId = c.id;
      try { if (c.op === 'set') localStorage.setItem(KEY, c.token); else localStorage.removeItem(KEY); } catch (e) {}
      post('streamlit:setComponentValue', { value: { token: get(), n: Date.now() }, dataType: 'json' });
      sentOnce = true;
    } else if (!sentOnce) {
      sentOnce = true;
      post('streamlit:setComponentValue', { value: { token: get(), n: Date.now() }, dataType: 'json' });
    }
  });
  post('streamlit:componentReady', { apiVersion: 1 });
  post('streamlit:setFrameHeight', { height: 0 });
})();
</script>"""


@st.cache_resource(show_spinner=False)
def get_bridge():
    d = os.path.join(tempfile.gettempdir(), "radar_auth_bridge")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(BRIDGE_HTML)
    return components.declare_component("radar_auth_bridge", path=d)


def bridge_cmd(op, token=""):
    st.session_state["_bridge_cmd"] = {"op": op, "token": token, "id": "%s-%s" % (op, time.time())}


def current_user():
    token = st.session_state.get("token")
    user = auth_db.session_user(token) if token else None
    if user:
        st.session_state["user"] = user
        return user
    st.session_state.pop("user", None)
    st.session_state.pop("token", None)
    return None


def do_logout():
    auth_db.end_session(st.session_state.get("token"))
    for k in ("user", "token", "watch", "radar_comp"):
        st.session_state.pop(k, None)
    bridge_cmd("clear")
    st.rerun()


# ---------- شاشة الدخول ----------
def login_screen():
    st.markdown("## رادار الأسهم الحلال")
    admins = auth_db.admin_count()
    if admins == 0:
        wanted = auth_db.first_admin_name()
        if wanted:
            st.info(f"لا يوجد مدير بعد. الحساب الذي اسمه «{wanted}» يصبح المدير عند إنشائه.")
        else:
            st.info("لا توجد حسابات بعد. أول حساب تنشئه يصبح المدير.")
    t1, t2 = st.tabs(["دخول", "حساب جديد"])
    with t1:
        with st.form("login_form"):
            u = st.text_input("اسم المستخدم", key="li_user")
            p = st.text_input("كلمة المرور", type="password", key="li_pass")
            go = st.form_submit_button("دخول", type="primary")
        if go:
            ok, msg, user = auth_db.login(u, p)
            if ok:
                token = auth_db.create_session(user["username"])
                st.session_state["user"], st.session_state["token"] = user, token
                bridge_cmd("set", token)
                st.rerun()
            else:
                st.error(msg)
    with t2:
        with st.form("reg_form"):
            u = st.text_input("اسم المستخدم (أحرف إنجليزية وأرقام)", key="rg_user")
            p = st.text_input("كلمة المرور (8 أحرف على الأقل)", type="password", key="rg_pass")
            p2 = st.text_input("أعد كتابة كلمة المرور", type="password", key="rg_pass2")
            go = st.form_submit_button("إنشاء الحساب", type="primary")
        if go:
            if p != p2:
                st.error("كلمتا المرور غير متطابقتين.")
            else:
                ok, msg, _, _ = auth_db.register(u, p)
                (st.success if ok else st.error)(msg)


# ---------- المنصة ----------
def _riyadh(ts):
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Riyadh"))
        return d.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def _stamp(d):
    m = (d or {}).get("meta", {})
    return m.get("fetchedAt") or m.get("asof") or ""


@st.fragment(run_every=UI_REFRESH_SECONDS)
def platform_view():
    token = st.session_state.get("token")
    user = auth_db.session_user(token) if token else None
    if not user:
        st.rerun()  # الحساب أُوقف أو انتهت الجلسة
    name = user["username"]
    if "watch" not in st.session_state:
        st.session_state["watch"] = auth_db.get_watch(name)

    upd = get_updater()
    data, status, err = upd.snapshot()
    if not data:
        st.info(f"{status} سيظهر الجدول تلقائيًا عند اكتمال أول جلب.")
        if err:
            st.error(err)
        time.sleep(4)
        st.rerun(scope="fragment")
        return

    prev = st.session_state.get("radar_comp")
    have = prev.get("stamp") if isinstance(prev, dict) else None
    payload = data if have != _stamp(data) else None
    try:
        comp = get_component()
    except Exception as e:  # noqa: BLE001
        st.error(f"تعذر تحميل واجهة المنصة: {type(e).__name__}: {e}")
        return
    val = comp(data=payload, watch=st.session_state["watch"], key="radar_comp", default=None)
    if isinstance(val, dict) and isinstance(val.get("watch"), dict) and val["watch"] != st.session_state["watch"]:
        auth_db.set_watch(name, val["watch"])
        st.session_state["watch"] = auth_db.get_watch(name)

    meta = data.get("meta", {})
    line = f"آخر تحديث للأسعار {_riyadh(meta.get('fetchedAt', ''))} بتوقيت الرياض · يتحدث كل 15 دقيقة أثناء ساعات السوق · الأسعار متأخرة"
    st.caption(line)
    if err:
        st.caption(f"تنبيه من آخر محاولة جلب: {err}")


# ---------- حسابي ----------
def account_tab(me):
    st.markdown(f"**الحساب:** {me['username']} · **الصلاحية:** {'مدير' if me['role'] == 'admin' else 'مستخدم'}")
    with st.form("pw_form"):
        old = st.text_input("كلمة المرور الحالية", type="password")
        new = st.text_input("كلمة المرور الجديدة (8 أحرف على الأقل)", type="password")
        go = st.form_submit_button("تغيير كلمة المرور")
    if go:
        ok, msg = auth_db.change_password(me["username"], old, new)
        if ok:
            st.success("تم تغيير كلمة المرور. سجّل الدخول من جديد.")
            do_logout()
        else:
            st.error(msg)
    if st.button("تسجيل الخروج", key="logout2"):
        do_logout()


# ---------- المدير ----------
STATUS_AR = {"approved": "معتمد", "pending": "بانتظار الاعتماد", "rejected": "مرفوض", "suspended": "موقوف"}


def admin_tab(me):
    if not auth_db.using_postgres():
        st.warning(
            "الحسابات مخزنة في ملف محلي وتُفقد عند إعادة تشغيل التطبيق. "
            "أضف DATABASE_URL في Secrets لتبقى الحسابات (الخطوات في README)."
        )
    upd = get_updater()
    data, status, err = upd.snapshot()
    with st.container(border=True):
        st.markdown("**حالة الأسعار**")
        st.caption(f"{status} · آخر جلب: {_riyadh((data or {}).get('meta', {}).get('fetchedAt', ''))} · "
                   f"أسهم: {len((data or {}).get('stocks', []))} من {(data or {}).get('meta', {}).get('total', '؟')}")
        _fl = (data or {}).get("meta", {}).get("failed") or []
        if _fl:
            st.caption("لم تُجلب (رمز غير مدعوم أو انقطاع مؤقت): " + "، ".join(_fl[:40]) + (" …" if len(_fl) > 40 else ""))
        if err:
            st.caption(f"آخر خطأ: {err}")
        c1, c2 = st.columns(2)
        if c1.button("تحديث الأسعار الآن", key="adm_quick"):
            upd.request("quick")
            st.toast("بدأ التحديث، سيظهر خلال دقيقة.")
        if c2.button("جلب كامل (بطيء)", key="adm_full"):
            upd.request("full")
            st.toast("بدأ الجلب الكامل، يستغرق عدة دقائق.")

    users = auth_db.list_users()
    pending = [u for u in users if u["status"] == "pending"]
    st.markdown(f"### طلبات الانضمام ({len(pending)})")
    if not pending:
        st.caption("لا توجد طلبات بانتظار الاعتماد.")
    for u in pending:
        with st.container(border=True):
            st.markdown(f"**{u['username']}** · طلب في {u['created'][:16].replace('T', ' ')} UTC")
            a, b = st.columns(2)
            if a.button("اعتماد", key=f"ap_{u['username']}", type="primary"):
                auth_db.set_status(u["username"], "approved", me["username"])
                st.rerun()
            if b.button("رفض", key=f"rj_{u['username']}"):
                ok, msg = auth_db.delete_user(u["username"], me["username"])
                st.rerun()

    st.markdown("### الحسابات")
    for u in users:
        if u["status"] == "pending":
            continue
        with st.container(border=True):
            st.markdown(f"**{u['username']}** · {'مدير' if u['role'] == 'admin' else 'مستخدم'} · {STATUS_AR.get(u['status'], u['status'])}")
            if u["username"] == me["username"]:
                st.caption("هذا حسابك.")
                continue
            c1, c2, c3 = st.columns(3)
            if u["status"] == "approved":
                if c1.button("إيقاف", key=f"sp_{u['username']}"):
                    auth_db.set_status(u["username"], "suspended", me["username"])
                    st.rerun()
            else:
                if c1.button("تفعيل", key=f"ac_{u['username']}"):
                    auth_db.set_status(u["username"], "approved", me["username"])
                    st.rerun()
            if u["role"] == "admin":
                if c2.button("إزالة المدير", key=f"dm_{u['username']}"):
                    ok, msg = auth_db.set_role(u["username"], "user", me["username"])
                    st.rerun() if ok else st.error(msg)
            else:
                if c2.button("ترقية لمدير", key=f"pm_{u['username']}"):
                    auth_db.set_role(u["username"], "admin", me["username"])
                    st.rerun()
            if st.session_state.get("confirm_del") == u["username"]:
                if c3.button("تأكيد الحذف", key=f"cd_{u['username']}", type="primary"):
                    auth_db.delete_user(u["username"], me["username"])
                    st.session_state.pop("confirm_del", None)
                    st.rerun()
            elif c3.button("حذف", key=f"dl_{u['username']}"):
                st.session_state["confirm_del"] = u["username"]
                st.rerun()
            with st.expander("إعادة تعيين كلمة المرور"):
                npw = st.text_input("كلمة مرور جديدة", type="password", key=f"np_{u['username']}")
                if st.button("حفظ", key=f"sv_{u['username']}"):
                    ok, msg = auth_db.admin_reset_password(u["username"], npw)
                    (st.success if ok else st.error)("تم الحفظ." if ok else msg)


# ---------- التشغيل ----------
get_updater()  # يبدأ الخيط الخلفي عند أول زيارة
_val = get_bridge()(cmd=st.session_state.get("_bridge_cmd"), key="auth_bridge", default=None)
me = current_user()
if not me and isinstance(_val, dict) and _val.get("token") and not st.session_state.get("_bridge_cmd", {}).get("op") == "clear":
    _u = auth_db.session_user(_val["token"])
    if _u:
        st.session_state["token"] = _val["token"]
        me = current_user()
if not me and _val is None:
    st.caption("جارٍ التحميل… إن طال الأمر حدّث الصفحة.")
    st.stop()
if not me:
    login_screen()
    st.stop()

top1, top2 = st.columns([3, 1])
top1.markdown(f"#### رادار الأسهم الحلال · {me['username']}")
if top2.button("خروج", key="logout1"):
    do_logout()

if me["role"] == "admin":
    pend = sum(1 for u in auth_db.list_users() if u["status"] == "pending")
    tabs = st.tabs(["المنصة", "حسابي", "المدير"])
    with tabs[0]:
        if pend:
            st.warning(f"لديك {pend} طلب انضمام بانتظار الاعتماد، افتح تبويب «المدير».")
        platform_view()
    with tabs[1]:
        account_tab(me)
    with tabs[2]:
        admin_tab(me)
else:
    tabs = st.tabs(["المنصة", "حسابي"])
    with tabs[0]:
        platform_view()
    with tabs[1]:
        account_tab(me)
