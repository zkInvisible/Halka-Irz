"""Web arayüzü için Flask API ve statik dosya sunucusu.

Eski sürümdeki üç yapısal sorun burada çözülüyor:

1. **Yenileme istek içinde çalışıyordu.** Tam bir tarama dakikalarca sürdüğü
   için "Veriyi yenile" düğmesi zaman aşımına uğruyor, tek işçili sunucuda bu
   süre boyunca site hiçbir isteğe cevap veremiyordu. Artık yenileme arka plan
   iş parçacığında çalışır; istekler her zaman son iyi raporu anında alır.

2. **Rapor diske yazılıyordu.** Render'ın ücretsiz katmanında disk kalıcı
   olmadığı için her yeniden başlatmada rapor depoya commit edilmiş eski
   sürüme dönüyordu. Artık veritabanına yazılıyor.

3. **Her işçi kendi zamanlayıcısını başlatıyordu.** Aynı anda birden fazla
   yenileme aynı veriyi yazabiliyordu. Artık veritabanı tabanlı kilit var.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from data.store import get_store, resolve_database_url  # noqa: E402
from main import seed_store_from_disk, write_local_artifacts  # noqa: E402
from pipeline import load_report, load_report_meta, refresh_report, report_age_hours  # noqa: E402

db = SQLAlchemy()

TURKEY_TZ = timezone(timedelta(hours=3))
DAILY_REFRESH_HOUR = 21          # Türkiye saati ile akşam yenilemesi
MAX_REPORT_AGE_HOURS = 12        # Bu yaşı geçen rapor otomatik tazelenir
KEEPALIVE_MINUTES = 13           # Render ücretsiz katmanı 15 dk sonra uykuya alır
MANUAL_COOLDOWN_SECONDS = 180
REFRESH_LOCK_KEY = "lock:refresh"
# Kilit, süreç yenileme ortasında ölürse kendiliğinden düşmeli. Uzun süren
# yenilemelerde ilerleme geri çağrısı kilidi tazelediği için kısa tutulabilir.
REFRESH_LOCK_TTL_MINUTES = 5
LAST_MANUAL_KEY = "meta:last_manual_refresh"
REPORT_MEMO_TTL_SECONDS = 45


class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(20), unique=True, nullable=False)
    upvotes = db.Column(db.Integer, default=0)
    downvotes = db.Column(db.Integer, default=0)


class RefreshManager:
    """Yenilemeyi arka planda, tek seferde ve gözlemlenebilir biçimde yürütür."""

    def __init__(self) -> None:
        self.store = get_store()
        self.owner_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_heartbeat = 0.0
        self.state: dict[str, Any] = {
            "running": False,
            "stage": None,
            "detail": {},
            "started_at": None,
            "finished_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_reason": None,
        }

    # ── dağıtık kilit ──────────────────────────────────────────────────────
    def _acquire_lock(self) -> bool:
        """Aynı anda yalnızca bir sürecin yenileme yapmasını sağlar."""
        now = datetime.now(timezone.utc)
        current = self.store.get(REFRESH_LOCK_KEY)
        if isinstance(current, dict):
            try:
                expires = datetime.fromisoformat(current["expires_at"])
            except (KeyError, TypeError, ValueError):
                expires = now
            if expires > now and current.get("owner") != self.owner_id:
                return False
        self.store.set(REFRESH_LOCK_KEY, {
            "owner": self.owner_id,
            "expires_at": (now + timedelta(minutes=REFRESH_LOCK_TTL_MINUTES)).isoformat(),
        })
        return True

    def _release_lock(self) -> None:
        current = self.store.get(REFRESH_LOCK_KEY)
        if isinstance(current, dict) and current.get("owner") == self.owner_id:
            self.store.set(REFRESH_LOCK_KEY, {"owner": None, "expires_at": datetime.now(timezone.utc).isoformat()})

    # ── çalıştırma ─────────────────────────────────────────────────────────
    def trigger(self, reason: str, *, force_details: bool = False) -> tuple[bool, str]:
        with self._lock:
            if self.state["running"]:
                return False, "Yenileme zaten sürüyor."
            if not self._acquire_lock():
                return False, "Başka bir sunucu örneği şu anda yeniliyor."
            self._last_heartbeat = time.monotonic()
            self.state.update(
                running=True, stage="başlatılıyor", detail={}, last_error=None,
                last_reason=reason, started_at=datetime.now(timezone.utc).isoformat(), finished_at=None,
            )
            self._thread = threading.Thread(
                target=self._run, args=(force_details,), name="refresh", daemon=True
            )
            self._thread.start()
        return True, f"Yenileme başlatıldı ({reason})."

    def _renew_lock(self) -> None:
        """Uzun yenilemelerde kilidin süresi dolmasın diye tazeler."""
        self.store.set(REFRESH_LOCK_KEY, {
            "owner": self.owner_id,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=REFRESH_LOCK_TTL_MINUTES)).isoformat(),
        })

    def _progress(self, stage: str, detail: dict[str, Any]) -> None:
        self.state["stage"] = stage
        self.state["detail"] = detail
        now = time.monotonic()
        if now - self._last_heartbeat > 60:
            self._last_heartbeat = now
            self._renew_lock()

    def _run(self, force_details: bool) -> None:
        try:
            report = refresh_report(progress=self._progress, force_details=force_details)
            self.state["last_success_at"] = report.get("generated_at")
            invalidate_report_cache()
            if os.environ.get("WRITE_LOCAL_ARTIFACTS", "").lower() in ("1", "true", "yes"):
                write_local_artifacts(report)
        except Exception as error:  # noqa: BLE001 - arka plan görevi asla süreci düşürmemeli
            self.state["last_error"] = f"{type(error).__name__}: {error}"
        finally:
            self.state["running"] = False
            self.state["stage"] = None
            self.state["finished_at"] = datetime.now(timezone.utc).isoformat()
            self._release_lock()

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)


refresh_manager: RefreshManager | None = None

# Rapor 500 KB'ı aşabiliyor; her istekte veritabanından okuyup yeniden
# serileştirmek yerine hazır JSON metnini kısa süreli bellekte tutuyoruz.
_report_memo: dict[str, Any] = {"body": None, "generated_at": None, "loaded_at": 0.0}
_report_memo_lock = threading.Lock()


def invalidate_report_cache() -> None:
    with _report_memo_lock:
        _report_memo["loaded_at"] = 0.0


def _cached_report_body() -> tuple[str | None, str | None]:
    """(json_metni, generated_at) döndürür; rapor yoksa (None, None)."""
    with _report_memo_lock:
        fresh_enough = time.monotonic() - _report_memo["loaded_at"] < REPORT_MEMO_TTL_SECONDS
        if fresh_enough and _report_memo["body"] is not None:
            return _report_memo["body"], _report_memo["generated_at"]

    report = load_report()
    if not report:
        return None, None
    body = json.dumps(report, ensure_ascii=False)
    with _report_memo_lock:
        _report_memo.update(body=body, generated_at=report.get("generated_at"), loaded_at=time.monotonic())
    return body, report.get("generated_at")


def _data_age_hours() -> float | None:
    return report_age_hours(load_report_meta())


def background_scheduler() -> None:
    """Canlı tutma pingi, açılış yenilemesi ve günlük yenileme.

    Tek bir döngü hepsini yönetir. Yenilemeyi tetiklemek ucuzdur; gerçek iş
    ``RefreshManager`` içinde ayrı bir iş parçacığında çalışır ve kilitle
    korunur, bu yüzden fazladan tetikleme zararsızdır.
    """
    manager = refresh_manager
    assert manager is not None
    external_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    last_ping = 0.0
    last_daily_refresh: Any = None

    # Açılışta: depo boşsa dosyadan tohumla, sonra veri bayatsa hemen yenile.
    try:
        if seed_store_from_disk():
            invalidate_report_cache()
    except Exception:  # noqa: BLE001
        pass

    age = _data_age_hours()
    if age is None or age > MAX_REPORT_AGE_HOURS:
        manager.trigger("açılış")

    while True:
        time.sleep(60)
        now_tr = datetime.now(TURKEY_TZ)

        # 1) Canlı tutma: Render ücretsiz servisi 15 dakika sessizlikte uyur.
        if external_url and time.monotonic() - last_ping > KEEPALIVE_MINUTES * 60:
            last_ping = time.monotonic()
            try:
                requests.get(f"{external_url}/healthz", timeout=10)
            except requests.RequestException:
                pass

        if manager.state["running"]:
            continue

        # 2) Akşam yenilemesi (Türkiye saati).
        if now_tr.hour >= DAILY_REFRESH_HOUR and last_daily_refresh != now_tr.date():
            last_daily_refresh = now_tr.date()
            manager.trigger("günlük")
            continue

        # 3) Emniyet ağı: sunucu akşam yenilemesini kaçırdıysa veya yeni
        #    başladıysa, veri bayatladığında kendi kendine tazelensin.
        age = _data_age_hours()
        if age is None or age > MAX_REPORT_AGE_HOURS:
            manager.trigger("bayat veri")


def create_app() -> Flask:
    global refresh_manager

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = resolve_database_url()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Oylama havuzu küçük tutulur: Supabase'in ücretsiz havuzlayıcısında
    # bağlantı bütçesi sınırlıdır ve bu bağlantı yalnızca oylar için gerekir.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": 2,
        "max_overflow": 3,
        "pool_timeout": 20,
    }
    db.init_app(app)
    with app.app_context():
        db.create_all()

    store = get_store()
    refresh_manager = RefreshManager()

    # ── statik arayüz ──────────────────────────────────────────────────────
    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "data_age_hours": _data_age_hours()})

    # ── rapor ──────────────────────────────────────────────────────────────
    @app.get("/api/report")
    def report():
        body, generated_at = _cached_report_body()
        if body is None:
            started, message = refresh_manager.trigger("ilk istek")
            return jsonify({
                "error": "Rapor henüz üretilmedi.",
                "detail": message,
                "refresh_started": started,
                "status": refresh_manager.snapshot(),
            }), 503
        response = Response(body, mimetype="application/json; charset=utf-8")
        response.headers["Cache-Control"] = "no-cache"
        if generated_at:
            response.headers["X-Generated-At"] = generated_at
        return response

    @app.get("/api/status")
    def status():
        meta = load_report_meta()
        age = report_age_hours(meta)
        return jsonify({
            "refresh": refresh_manager.snapshot(),
            "generated_at": meta.get("generated_at"),
            "data_age_hours": round(age, 2) if age is not None else None,
            "is_stale": age is None or age > MAX_REPORT_AGE_HOURS,
            "counts": {
                "offers": meta.get("offer_count"),
                "drafts": meta.get("draft_count"),
                "outcomes": meta.get("outcome_count"),
            },
            "collection": meta.get("collection"),
            "storage": store.health(),
        })

    @app.post("/api/refresh")
    def refresh():
        now = time.time()
        last = store.get(LAST_MANUAL_KEY) or 0
        remaining = MANUAL_COOLDOWN_SECONDS - (now - float(last))
        if remaining > 0 and not refresh_manager.state["running"]:
            return jsonify({
                "error": f"Spam koruması: {int(remaining)} saniye sonra tekrar deneyebilirsiniz.",
                "cooldown": int(remaining),
                "status": refresh_manager.snapshot(),
            }), 429

        force = bool((request.get_json(silent=True) or {}).get("force"))
        started, message = refresh_manager.trigger("manuel", force_details=force)
        if started:
            store.set(LAST_MANUAL_KEY, now)
        # 202: iş kabul edildi, arka planda sürüyor. İstemci /api/status ile izler.
        return jsonify({
            "accepted": started,
            "message": message,
            "status": refresh_manager.snapshot(),
        }), 202

    # ── oylama ─────────────────────────────────────────────────────────────
    @app.get("/api/votes")
    def get_votes():
        try:
            votes = Vote.query.all()
        except Exception:  # noqa: BLE001 - oylama arızası raporu engellemesin
            db.session.rollback()
            return jsonify({})
        return jsonify({v.ticker: {"upvotes": v.upvotes or 0, "downvotes": v.downvotes or 0} for v in votes})

    @app.post("/api/vote")
    def submit_vote():
        data = request.get_json(silent=True)
        if not data or "ticker" not in data or "type" not in data:
            return jsonify({"error": "Geçersiz istek"}), 400

        ticker = str(data["ticker"])[:20]
        vote_type = data["type"]
        if vote_type not in ("up", "down", "remove_up", "remove_down"):
            return jsonify({"error": "Geçersiz oy türü"}), 400

        try:
            record = Vote.query.filter_by(ticker=ticker).first()
            if not record:
                record = Vote(ticker=ticker, upvotes=0, downvotes=0)
                db.session.add(record)
            if vote_type == "up":
                record.upvotes = (record.upvotes or 0) + 1
            elif vote_type == "down":
                record.downvotes = (record.downvotes or 0) + 1
            elif vote_type == "remove_up":
                record.upvotes = max(0, (record.upvotes or 0) - 1)
            else:
                record.downvotes = max(0, (record.downvotes or 0) - 1)
            db.session.commit()
        except Exception as error:  # noqa: BLE001
            db.session.rollback()
            return jsonify({"error": f"Oy kaydedilemedi: {type(error).__name__}"}), 500
        return jsonify({"success": True, "upvotes": record.upvotes, "downvotes": record.downvotes})

    # Statik dosyalar en sonda: /api/* yollarını gölgelememesi için.
    @app.get("/<path:filename>")
    def frontend_asset(filename: str):
        return send_from_directory(FRONTEND_DIR, filename)

    if os.environ.get("RUN_SCHEDULER", "1").lower() not in ("0", "false", "no"):
        threading.Thread(target=background_scheduler, name="scheduler", daemon=True).start()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True)
