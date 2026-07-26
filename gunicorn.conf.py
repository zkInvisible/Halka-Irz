"""Gunicorn ayarları — Render ücretsiz katmanı için.

Kritik olan iki karar:

``workers = 1``
    Ücretsiz katmanda 512 MB bellek var ve arka plan yenileme iş parçacığı
    yalnızca bir kez çalışmalı. Birden fazla işçi, her biri kendi zamanlayıcısını
    başlatıp aynı veriyi eşzamanlı yazardı. (Uygulama yine de veritabanı kilidiyle
    korunuyor; bu ayar gereksiz işi baştan engeller.)

``threads > 1``
    Tek işçi + tek iş parçacığı olsaydı, arka plandaki yenileme sırasında gelen
    istekler sıraya girerdi. İş parçacıklarıyla site yenileme sürerken de
    cevap vermeye devam eder.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
threads = int(os.environ.get("WEB_THREADS", "8"))
worker_class = "gthread"

# Yenileme arka planda çalışsa da soğuk açılışta ilk istek biraz bekleyebilir.
timeout = 120
graceful_timeout = 30
keepalive = 5

# preload_app=False: uygulama işçi süreci içinde kurulur; böylece arka plan
# iş parçacığı fork'tan sonra doğar ve çalışmaya devam eder.
preload_app = False

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
