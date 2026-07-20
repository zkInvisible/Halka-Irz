"""Yakın dönem halka arz sonuçlarını tekrar üretilebilir biçimde toplar.

İlk işlem tarihi ve halka arz fiyatı HalkArz ayrıntı sayfasından; günlük kapanış
fiyatları Yahoo Finance grafik uç noktasından alınır. İki kaynak ayrı ayrı rapora
yazılır. Bir kaynaktan veri alınamazsa kayıt tahmin edilmez, atlanır.
"""

from __future__ import annotations

import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .sources import HalkarzSource, SourceError


CACHE_MAX_AGE_HOURS = 12
PRICE_LOOKAHEAD_DAYS = 45
MAX_WORKERS = 8


def canonical_broker(value: str | None) -> str | None:
    """Birden çok satışa aracılık eden metinden ilk aracı kurumu eşleştirir.

    Bu alan yalnızca aynı kurumun geçmiş sonuçlarını kohortlamak içindir; ortak
    satış ağındaki bütün kurumları "lider" saymak için kullanılmaz.
    """
    if not value:
        return None
    first = re.split(r"(?<=A\.Ş\.)\s+", value, maxsplit=1, flags=re.IGNORECASE)[0]
    # NFKD, Türkçedeki noktasız ``ı`` karakterini ASCII'ye dönüştürmez. Önce
    # birebir eşleme yapmak, "Yatırım" ile "Yatirim" yazımlarının aynı kohorta
    # girmesini sağlar.
    first = first.translate(str.maketrans("ÇĞİÖŞÜçğıöşü", "CGIOSUcgiosu"))
    first = unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode("ascii").lower()
    first = re.sub(r"\b(menkul|degerler|kiymetler|yatirim|a\.?s\.?|anonim|sirketi)\b", " ", first)
    first = re.sub(r"[^a-z0-9]+", " ", first).strip()
    return first or None


class YahooChartSource:
    """Bağımlılıksız Yahoo Finance günlük kapanış okuyucusu."""

    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, timeout_seconds: int = 20):
        self.timeout_seconds = timeout_seconds

    def closes_after(self, ticker: str, listing_date: date) -> list[tuple[date, float]]:
        start = datetime.combine(listing_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=PRICE_LOOKAHEAD_DAYS)
        url = f"{self.base_url}/{ticker}.IS"
        response = requests.get(
            url,
            params={"period1": int(start.timestamp()), "period2": int(end.timestamp()), "interval": "1d", "events": "history"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        chart = response.json().get("chart", {})
        result = (chart.get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        closes_with_vol = []
        from itertools import zip_longest
        for raw_timestamp, raw_close, raw_vol in zip_longest(timestamps, closes, volumes, fillvalue=0):
            if not isinstance(raw_close, (int, float)) or raw_close <= 0:
                continue
            observed = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).date()
            if observed >= listing_date:
                closes_with_vol.append((observed, float(raw_close), int(raw_vol or 0)))
        return closes_with_vol

    def latest_close(self, ticker: str) -> tuple[date, float, int] | None:
        """Son geçerli günlük kapanışı ve hacmi döndürür; anlık işlem fiyatı değildir."""
        response = requests.get(
            f"{self.base_url}/{ticker}.IS",
            params={"range": "10d", "interval": "1d", "events": "history"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        result = ((response.json().get("chart", {}).get("result") or [None])[0])
        if not result:
            return None
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        for raw_timestamp, raw_close, raw_vol in reversed(list(zip(timestamps, closes, volumes))):
            if isinstance(raw_close, (int, float)) and raw_close > 0:
                return datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).date(), float(raw_close), int(raw_vol or 0)
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def outcome_from_offer(offer: dict[str, Any], reference_date: date) -> dict[str, Any] | None:
    """Bir tamamlanmış arz için 5 seans ve 20 seans risk ölçümünü üretir."""
    ipo_price = _number(offer.get("ipo_price_tl"))
    if not ipo_price or ipo_price <= 0:
        return None

    try:
        if offer.get("listing_date"):
            listing_date = date.fromisoformat(offer["listing_date"])
        else:
            listing_date = date.fromisoformat(offer["start_date"])
    except (KeyError, TypeError, ValueError):
        return None

    if listing_date >= reference_date:
        return None
    chart_source = YahooChartSource()
    try:
        closes = chart_source.closes_after(offer["ticker"], listing_date)
    except (requests.RequestException, ValueError, KeyError):
        return None
    if not closes:
        return None
    # Eğer listing_date halkarz'da yoktuysa, Yahoo'dan gelen ilk tarihi baz alalım:
    actual_listing_date = closes[0][0]
    try:
        latest = chart_source.latest_close(offer["ticker"])
    except (requests.RequestException, ValueError, KeyError):
        latest = None
    # BIST'teki ilk işlem gününü 1. seans kabul eder. 5. kapanış yoksa mevcut en son kapanışı alır.
    fifth_index = min(4, len(closes) - 1)
    fifth_date, fifth_close, _ = closes[fifth_index]
    first_twenty = closes[:20]
    peak = ipo_price
    maximum_drawdown = 0.0
    for _, close, _ in first_twenty:
        peak = max(peak, close)
        maximum_drawdown = max(maximum_drawdown, (peak - close) / peak * 100)
    # Yahoo günlük kapanışları emir defterini içermediği için bu sayı resmî “tavan”
    # verisi değildir. Önceki kapanışa göre en az %9,5 yükselen kapanışların ardışık
    # serisini, kolay okunabilen bir yaklaşık tavan göstergesi olarak tutarız.
    longest_limit_up_streak = 0
    current_limit_up_streak = 0
    initial_streak_broken = False
    break_close = None
    previous_close = ipo_price
    for _, close, _ in first_twenty:
        if close / previous_close - 1 >= 0.095:
            current_limit_up_streak += 1
            longest_limit_up_streak = max(longest_limit_up_streak, current_limit_up_streak)
        else:
            current_limit_up_streak = 0
            if not initial_streak_broken:
                initial_streak_broken = True
                break_close = close
        previous_close = close

    # 15 iş günü içindeki max kâr hesaplaması
    first_fifteen = closes[:15]
    if first_fifteen:
        max_15d_close = max(close for _, close, _ in first_fifteen)
        max_return_15d_pct = round((max_15d_close / ipo_price - 1) * 100, 2)
    else:
        max_return_15d_pct = None

    latest_turnover_pct = None
    if latest:
        dt, px, vol = latest
        latest_close_tl = round(px, 2)
        latest_close_date = dt.isoformat()
        return_since_ipo_pct = round((px / ipo_price - 1) * 100, 2)
        
        # Calculate Cumulative Turnover Rate
        total_vol = sum(v for _, _, v in closes)
        offer_size = _number(offer.get("offer_size_mn_tl"))
        if total_vol > 0 and offer_size and ipo_price > 0:
            floating_lots = (offer_size * 1000000) / ipo_price
            if floating_lots > 0:
                latest_turnover_pct = round((total_vol / floating_lots) * 100, 2)
    else:
        latest_close_tl = None
        latest_close_date = None
        return_since_ipo_pct = None

    return {
        "ticker": offer["ticker"],
        "company": offer.get("company"),
        "listing_date": actual_listing_date.isoformat(),
        "return_5d_pct": round((fifth_close / ipo_price - 1) * 100, 2),
        "return_20d_pct": round((first_twenty[-1][1] / ipo_price - 1) * 100, 2) if len(first_twenty) >= 20 else None,
        "max_drawdown_20d_pct": round(maximum_drawdown, 2) if len(first_twenty) >= 10 else None,
        "max_limit_up_streak": longest_limit_up_streak,
        "is_streak_active": not initial_streak_broken,
        "max_return_15d_pct": max_return_15d_pct,
        "limit_up_method": "İlk 20 seansta önceki kapanışa göre en az %9,5 artan kapanışların en uzun ardışık serisi; resmî tavan sayısı değildir.",
        "latest_close_tl": latest_close_tl,
        "latest_close_date": latest_close_date,
        "return_since_ipo_pct": return_since_ipo_pct,
        "latest_turnover_pct": latest_turnover_pct,
        "broker": offer.get("broker"),
        "broker_key": canonical_broker(offer.get("broker")),
        "sector": offer.get("sector"),
        "source_url": offer.get("detail_url"),
        "price_source_url": f"https://finance.yahoo.com/quote/{offer['ticker']}.IS/history",
        "ipo_price_tl": ipo_price,
        "fifth_session_date": fifth_date.isoformat(),
        "observation_count": len(first_twenty),
        "is_partial_5d": len(closes) < 5,
        # Tarihsel puan, gelecekteki sonuçlarla kirlenmeden yeniden hesaplanabilsin
        # diye halka arz anındaki kaynaklanmış alanları saklarız.
        "offer_snapshot": {
            key: offer.get(key)
            for key in (
                "ticker", "company", "calendar_date_text", "start_date", "end_date", "detail_url",
                "calendar_source", "sector", "sector_source", "ipo_price_tl", "distribution_method",
                "broker", "market", "offered_lots", "primary_lots", "secondary_lots",
                "retail_allocation_pct", "retail_allocation_tl", "participant_count", 
                "float_pct", "stated_discount_pct", "use_of_proceeds",
                "price_stabilization", "lockup", "offer_size_mn_tl", "financials", "documents",
            )
        },
    }


def _cache_is_current(payload: dict[str, Any], reference_date: date) -> bool:
    if payload.get("reference_date") != reference_date.isoformat() or not payload.get("outcomes"):
        return False
    try:
        generated_at = datetime.fromisoformat(payload["generated_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc) < timedelta(hours=CACHE_MAX_AGE_HOURS)


def _read_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def collect_recent_outcomes(
    reference_date: date,
    cache_path: Path,
    force: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Yakın dönem sonuçlarını alır ve kısa süreli önbellekte saklar.

    Ağ/tekil sembol problemleri tüm raporu durdurmaz. Önbellek varsa ve yeni
    toplamaya zorlanmadıysa aynı gün içindeki yenilemeler kaynaklara tekrar yük
    bindirmez.
    """
    cached = _read_cache(cache_path)
    if not force and _cache_is_current(cached, reference_date):
        return cached["outcomes"], {**cached.get("status", {}), "mode": "cached"}

    source = HalkarzSource()
    candidates = source.fetch_recent_candidates(reference_date, lookback_days=730)
    detailed: list[dict[str, Any]] = []
    source_failures = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(HalkarzSource().fetch_detail, candidate): candidate for candidate in candidates}
        for future in as_completed(futures):
            try:
                offer = future.result()
                start = date.fromisoformat(offer["start_date"])
                if reference_date.fromordinal(reference_date.toordinal() - 730) <= start < reference_date:
                    detailed.append(offer)
            except (SourceError, requests.RequestException, KeyError, TypeError, ValueError):
                source_failures += 1

    outcomes: list[dict[str, Any]] = []
    price_failures = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(outcome_from_offer, offer, reference_date): offer for offer in detailed}
        for future in as_completed(futures):
            try:
                outcome = future.result()
            except (requests.RequestException, ValueError, KeyError):
                outcome = None
            if outcome:
                outcomes.append(outcome)
            else:
                price_failures += 1
    outcomes.sort(key=lambda item: item["listing_date"], reverse=True)
    status = {
        "mode": "refreshed",
        "candidate_count": len(candidates),
        "eligible_offer_count": len(detailed),
        "outcome_count": len(outcomes),
        "source_failures": source_failures,
        "price_unavailable_count": price_failures,
        "price_method": "Halka arz fiyatından, ilk işlem gününü 1. seans kabul eden 5. kapanışa getiri; ilk 20 seanstaki azami düşüş.",
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reference_date": reference_date.isoformat(), "status": status, "outcomes": outcomes},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return outcomes, status
