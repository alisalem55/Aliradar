#!/usr/bin/env python3
"""
تشغيل المنصة على جهازك مع زر «تحديث الأسعار» يعمل بضغطة واحدة.

    pip install yfinance
    python serve.py

ثم افتح  http://localhost:8000  في المتصفح.
الزر في أعلى الصفحة يجلب الأسعار المتأخرة المجانية ويحدّث الجدول مباشرة.
"""
import json
import os
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import fetch_data

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
LOCK = threading.Lock()


class H(SimpleHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self._json({"server": True})
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/refresh"):
            return self._json({"error": "not found"}, 404)
        if not LOCK.acquire(blocking=False):
            return self._json({"error": "التحديث جارٍ بالفعل"}, 409)
        try:
            data = fetch_data.build()
            with open("data.json", "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            return self._json(data)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)
        finally:
            LOCK.release()

    def end_headers(self):
        if self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://localhost:{port}"
    print("المنصة تعمل على", url, "(Ctrl+C للإيقاف)")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    srv.serve_forever()
