"""Yerel web arayüzü için Flask API ve statik dosya sunucusu."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, send_from_directory, request
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(20), unique=True, nullable=False)
    upvotes = db.Column(db.Integer, default=0)
    downvotes = db.Column(db.Integer, default=0)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from data.sources import SourceError
from main import load_report, refresh_report


def create_app() -> Flask:
    app = Flask(__name__)

    # Render'da DATABASE_URL (örn: Supabase/Neon), yoksa yerel SQLite.
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'database.sqlite'}")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
        
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+pg8000://", 1)
        
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    
    with app.app_context():
        db.create_all()

    @app.get("/api/votes")
    def get_votes():
        votes = Vote.query.all()
        return jsonify({v.ticker: {"upvotes": v.upvotes, "downvotes": v.downvotes} for v in votes})

    @app.post("/api/vote")
    def submit_vote():
        data = request.get_json()
        if not data or "ticker" not in data or "type" not in data:
            return jsonify({"error": "Geçersiz istek"}), 400
            
        ticker = data["ticker"]
        vote_type = data["type"]
        
        vote_record = Vote.query.filter_by(ticker=ticker).first()
        if not vote_record:
            vote_record = Vote(ticker=ticker, upvotes=0, downvotes=0)
            db.session.add(vote_record)
            
        if vote_type == "up":
            vote_record.upvotes = (vote_record.upvotes or 0) + 1
        elif vote_type == "down":
            vote_record.downvotes = (vote_record.downvotes or 0) + 1
        elif vote_type == "remove_up":
            vote_record.upvotes = max(0, (vote_record.upvotes or 0) - 1)
        elif vote_type == "remove_down":
            vote_record.downvotes = max(0, (vote_record.downvotes or 0) - 1)
            
        db.session.commit()
        return jsonify({"success": True, "upvotes": vote_record.upvotes, "downvotes": vote_record.downvotes})

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/<path:filename>")
    def frontend_asset(filename: str):
        return send_from_directory(FRONTEND_DIR, filename)

    @app.get("/api/report")
    def report():
        current = load_report()
        if not current:
            try:
                current = refresh_report()
            except SourceError as error:
                return jsonify({"error": str(error)}), 503
            except Exception as error:
                import traceback
                traceback.print_exc()
                return jsonify({"error": f"Sistem Hatası: {str(error)}"}), 500
        return jsonify(current)

    # Refresh rate limit variables
    last_manual_refresh = 0

    @app.post("/api/refresh")
    def refresh():
        nonlocal last_manual_refresh
        now = time.time()
        # 5 minute cooldown
        if now - last_manual_refresh < 300:
            return jsonify({"error": "Spam koruması: En fazla 5 dakikada bir veri çekebilirsiniz.", "cooldown": 300 - (now - last_manual_refresh)}), 429
            
        try:
            res = refresh_report(force_market_refresh=True)
            last_manual_refresh = now
            return jsonify(res)
        except SourceError as error:
            return jsonify({"error": str(error)}), 503
        except Exception as error:
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"Sistem Hatası: {str(error)}"}), 500

    # Background thread for keep-alive and daily auto-refresh
    def background_tasks():
        import datetime
        url = os.environ.get("RENDER_EXTERNAL_URL", "http://127.0.0.1:5050")
        last_refresh_date = None
        
        while True:
            time.sleep(14 * 60)  # Ping every 14 minutes
            try:
                requests.get(url, timeout=10)
            except Exception:
                pass
                
            # Her aksam 21:00'da (Turkiye Saati) yenile
            tz_tr = datetime.timezone(datetime.timedelta(hours=3))
            now_tr = datetime.datetime.now(tz_tr)
            
            if now_tr.hour >= 21 and last_refresh_date != now_tr.date():
                print(f"Running daily auto-refresh at {now_tr.strftime('%H:%M')}...")
                try:
                    refresh_report(force_market_refresh=True)
                    last_refresh_date = now_tr.date()
                except Exception as e:
                    print(f"Auto-refresh failed: {e}")

    threading.Thread(target=background_tasks, daemon=True).start()

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
