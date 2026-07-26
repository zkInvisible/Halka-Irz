"""Dayanıklı HTTP istemcisi.

Eski sürümde her modül kendi ``requests`` oturumunu kuruyor, tek bir 503 veya
Cloudflare kontrol sayfası bütün yenilemeyi düşürüyordu. Burada tek bir yerde
toplanan oturum; yeniden deneme, üstel bekleme, nazik istek aralığı ve bot
koruması tespiti yapar.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524}

# Aynı sunucuya art arda giden istekler arasındaki asgari boşluk (saniye).
# Cloudflare arkasındaki halkarz.com'u 8 paralel iş parçacığıyla dövmek bot
# korumasını tetikliyordu; küçük bir gecikme bunu pratikte tamamen bitiriyor.
HOST_MIN_INTERVAL = {
    "halkarz.com": 0.35,
    "query1.finance.yahoo.com": 0.20,
    "query2.finance.yahoo.com": 0.20,
}


class SourceError(RuntimeError):
    """Kaynak sayfası alınamadığında veya bot koruması döndüğünde fırlatılır."""


class BotProtectionError(SourceError):
    """Kaynak, içerik yerine bot doğrulama sayfası döndürdü."""


class _HostThrottle:
    """Host başına asgari istek aralığını uygular."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        interval = HOST_MIN_INTERVAL.get(host)
        if not interval:
            return
        with self._lock:
            now = time.monotonic()
            earliest = self._next_allowed.get(host, 0.0)
            delay = max(0.0, earliest - now)
            self._next_allowed[host] = max(now, earliest) + interval
        if delay:
            time.sleep(delay)


_throttle = _HostThrottle()

_BOT_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
)


def _looks_like_bot_wall(text: str) -> bool:
    head = text[:4000].lower()
    return any(marker in head for marker in _BOT_MARKERS)


class HttpClient:
    """İş parçacığı güvenli, yeniden denemeli HTTP istemcisi."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES):
        self.timeout = timeout
        self.retries = retries
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        # requests.Session iş parçacıkları arasında paylaşıldığında bağlantı
        # havuzu yarış koşullarına girebiliyor; her iş parçacığına bir oturum.
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            })
            self._local.session = session
        return session

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        expect_html: bool = True,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            if attempt:
                # Üstel bekleme + jitter: eşzamanlı işçilerin aynı anda geri
                # dönüp kaynağı tekrar dövmesini engeller.
                time.sleep(min(8.0, 0.8 * (2 ** (attempt - 1))) + random.uniform(0, 0.4))
            _throttle.wait(url)
            try:
                response = self.session.request(
                    method, url, params=params, data=data, timeout=self.timeout
                )
            except requests.RequestException as error:
                last_error = error
                continue

            if response.status_code in RETRYABLE_STATUS:
                last_error = SourceError(f"{response.status_code} {url}")
                continue
            if response.status_code >= 400:
                raise SourceError(f"{response.status_code} {url}")
            if expect_html and _looks_like_bot_wall(response.text):
                last_error = BotProtectionError(f"Bot koruması: {url}")
                continue
            return response

        if isinstance(last_error, SourceError):
            raise last_error
        raise SourceError(f"{url} alınamadı: {last_error}")

    def get_soup(self, url: str, params: dict[str, Any] | None = None) -> BeautifulSoup:
        response = self.request("GET", url, params=params)
        return BeautifulSoup(response.text, "html.parser")

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self.request("GET", url, params=params, expect_html=False)
        return response.json()

    def post_soup(self, url: str, data: dict[str, Any]) -> BeautifulSoup:
        response = self.request("POST", url, data=data)
        return BeautifulSoup(response.text, "html.parser")


shared_client = HttpClient()
