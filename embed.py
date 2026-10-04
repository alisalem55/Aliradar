"""يدمج بيانات الأسعار داخل صفحة index.html لعرضها في Streamlit."""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def build_html(data=None):
    html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    if data:
        blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        html = re.sub(r'(id="seed">).*?(</script>)', lambda m: m.group(1) + blob + m.group(2), html, count=1, flags=re.S)
    return html
