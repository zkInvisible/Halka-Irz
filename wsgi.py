"""WSGI giriş noktası.

Gunicorn'un `backend` dizinini paket olarak çözmesine bağlı kalmamak için
sys.path'i burada açıkça kuruyoruz. Böylece hem `gunicorn wsgi:app` hem de
`python backend/app.py` aynı içe aktarma düzeniyle çalışır.
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import app  # noqa: E402,F401  (gunicorn bu adı arar)
