"""تحديث الأسعار كل 15 دقيقة في الخلفية (خيط واحد يخدم كل المستخدمين).

- عند أول تشغيل: يقرأ data.json إن وُجد، وإلا يجلب كل السجل (بضع دقائق).
- كل 15 دقيقة أثناء ساعات السوق: طلب واحد مجمّع لآخر 5 أيام لكل سوق ثم دمجها في السجل.
- مرة كل يوم تقريبًا: جلب كامل لتحديث النسب المالية.
"""
import datetime as dt
import json
import os
import threading
import time
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
INTERVAL = int(os.environ.get("RADAR_INTERVAL_SECONDS", "900"))
KEEP_BARS = 260
RIYADH = ZoneInfo("Asia/Riyadh")
NEW_YORK = ZoneInfo("America/New_York")


def open_markets(now_utc=None):
    """الأسواق المفتوحة الآن (مع هامش قبل الافتتاح وبعد الإغلاق لالتقاط سعر الإغلاق)."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    out = []
    sa = now_utc.astimezone(RIYADH)
    if sa.weekday() in (6, 0, 1, 2, 3) and dt.time(9, 50) <= sa.time() <= dt.time(15, 45):
        out.append("SA")
    us = now_utc.astimezone(NEW_YORK)
    if us.weekday() <= 4 and dt.time(9, 25) <= us.time() <= dt.time(16, 45):
        out.append("US")
    return out


def merge_bars(bars, rows):
    """يدمج صفوفًا جديدة [date,o,h,l,c,v] في السجل: يستبدل نفس اليوم ويضيف الأحدث."""
    by = {b[0]: b for b in bars}
    for r in rows:
        by[r[0]] = r
    return [by[k] for k in sorted(by)][-KEEP_BARS:]


def _iso_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    try:
        return dt.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except Exception:
        return None


class Updater:
    def __init__(self, path=DATA_PATH, downloader=None, full_builder=None):
        self.path = path
        self.lock = threading.Lock()
        self.data = None
        self.status = "جارٍ التشغيل…"
        self.error = ""
        self.last_quick = None
        self.last_full = None
        self._wake = threading.Event()
        self._force = None
        self._started = False
        self._downloader = downloader
        self._full_builder = full_builder
        self._load_file()

    # ---------- الحالة ----------
    def snapshot(self):
        with self.lock:
            return self.data, self.status, self.error

    def _set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _load_file(self):
        try:
            d = json.load(open(self.path, encoding="utf-8"))
            if d.get("stocks"):
                self.data = d
                m = d.get("meta", {})
                self.last_full = _parse(m.get("fullAt") or m.get("fetchedAt"))
                self.last_quick = _parse(m.get("fetchedAt"))
                self.status = "جاهز"
        except Exception:
            pass

    def _save_file(self, d):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---------- الجلب ----------
    def full_fetch(self):
        self._set(status="جارٍ جلب كل البيانات (بضع دقائق)…")
        if self._full_builder:
            d = self._full_builder()
        else:
            import fetch_data
            d = fetch_data.build()
        if not d or not d.get("stocks"):
            raise RuntimeError("لم تُجلب أي بيانات (قد تكون Yahoo حجبت الطلبات مؤقتًا)")
        for s in d["stocks"]:
            s["bars"] = s["bars"][-KEEP_BARS:]
        now = _iso_now()
        d["meta"]["fullAt"] = now
        d["meta"]["fetchedAt"] = now
        with self.lock:
            self.data = d
            self.last_full = self.last_quick = _parse(now)
            self.status = "جاهز"
            self.error = ""
        self._save_file(d)

    def _download(self, ysyms):
        if self._downloader:
            return self._downloader(ysyms)
        import yfinance as yf
        return yf.download(ysyms, period="5d", interval="1d", group_by="ticker", auto_adjust=False,
                           progress=False, threads=True)

    @staticmethod
    def _rows_for(df, ysym):
        try:
            sub = df[ysym]
        except Exception:
            return []
        rows = []
        try:
            sub = sub.dropna(subset=["Close"])
            for idx, r in sub.iterrows():
                rows.append([idx.strftime("%Y-%m-%d"), round(float(r["Open"]), 2), round(float(r["High"]), 2),
                             round(float(r["Low"]), 2), round(float(r["Close"]), 2), int(r["Volume"])])
        except Exception:
            return []
        return rows

    def quick_update(self, markets):
        with self.lock:
            cur = self.data
        if not cur:
            return 0
        self._set(status="جارٍ تحديث الأسعار…")
        stocks = [dict(s) for s in cur["stocks"]]
        changed = 0
        for mk in markets:
            idxs = [i for i, s in enumerate(stocks) if s["market"] == mk]
            ys = [(stocks[i]["sym"] + ".SR") if mk == "SA" else stocks[i]["sym"] for i in idxs]
            if not ys:
                continue
            df = self._download(ys)
            for i, ysym in zip(idxs, ys):
                rows = self._rows_for(df, ysym)
                if rows:
                    stocks[i]["bars"] = merge_bars(stocks[i]["bars"], rows)
                    changed += 1
        if not changed:
            raise RuntimeError("لم تصل أسعار جديدة")
        meta = dict(cur["meta"])
        meta["fetchedAt"] = _iso_now()
        meta["asof"] = max(s["bars"][-1][0] for s in stocks)
        d = {"meta": meta, "stocks": stocks}
        with self.lock:
            self.data = d
            self.last_quick = _parse(meta["fetchedAt"])
            self.status = "جاهز"
            self.error = ""
        self._save_file(d)
        return changed

    # ---------- الحلقة ----------
    def need_full(self, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        if not self.data:
            return True
        if not self.last_full:
            return True
        age = (now - self.last_full).total_seconds()
        if age > 36 * 3600:
            return True
        return age > 24 * 3600 and not open_markets(now)

    def tick(self, now=None):
        """خطوة واحدة: تقرر ما يجب جلبه وتنفذه. تعيد وصفًا لما حدث."""
        now = now or dt.datetime.now(dt.timezone.utc)
        force, self._force = self._force, None
        if force == "full" or self.need_full(now):
            self.full_fetch()
            return "full"
        mk = open_markets(now)
        stale = (not self.last_quick) or (now - self.last_quick).total_seconds() > 6 * 3600
        if force == "quick":
            mk = ["SA", "US"]
        elif not mk and stale:
            mk = ["SA", "US"]
        if mk:
            self.quick_update(mk)
            return "quick:" + ",".join(mk)
        self._set(status="جاهز")
        return "idle"

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                self._set(error=str(e), status="جاهز" if self.data else "تعذر الجلب")
            self._wake.wait(INTERVAL)
            self._wake.clear()

    def start(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._loop, daemon=True, name="radar-updater").start()

    def request(self, kind="quick"):
        self._force = kind
        self._wake.set()
