"""رادار الأسهم الحلال على Streamlit: يجلب الأسعار المتأخرة المجانية ويعرض المنصة."""
import datetime as dt
import json
import os

import streamlit as st
import streamlit.components.v1 as components

import embed

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")

st.set_page_config(page_title="رادار الأسهم الحلال", layout="wide")
st.markdown(
    "<style>.block-container{padding:0.6rem 0.8rem 0}header{visibility:hidden;height:0}</style>",
    unsafe_allow_html=True,
)


@st.cache_resource(ttl=6 * 3600, show_spinner=False)
def fetch_all():
    import fetch_data  # يتطلب yfinance من requirements.txt
    return fetch_data.build()


def load_file():
    if os.path.exists(DATA):
        try:
            return json.load(open(DATA, encoding="utf-8"))
        except Exception:
            return None
    return None


def save(data):
    try:
        with open(DATA, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception:
        pass


data = st.session_state.get("data") or load_file()

c1, c2 = st.columns([1, 3])
with c1:
    refresh = st.button("تحديث الأسعار", type="primary")
with c2:
    if data:
        m = data.get("meta", {})
        st.caption(f"آخر أسعار: {m.get('asof', '—')} · {len(data.get('stocks', []))} سهمًا · أسعار متأخرة مجانية")

if refresh or not data:
    with st.spinner("جارٍ جلب الأسعار من Yahoo Finance… قد يستغرق عدة دقائق"):
        try:
            if refresh:
                fetch_all.clear()
            new = fetch_all()
            if new and new.get("stocks"):
                data = new
                save(data)
            else:
                st.error("لم تُجلب أي بيانات. غالبًا حجبت Yahoo الطلبات مؤقتًا، أعد المحاولة بعد قليل.")
        except Exception as e:  # noqa: BLE001
            st.error(f"تعذر جلب الأسعار: {e}")
    if data:
        st.session_state["data"] = data

components.html(embed.build_html(data), height=950, scrolling=True)
