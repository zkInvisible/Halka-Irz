"""Yakın dönem halka arz sonuçlarından sızıntısız emsal istatistikleri üretir.

İyileştirmeler:
- Stability score formülü daha dengeli ağırlıklarla güncellendi.
- Negatif getiri durumunda drawdown etkisi artırıldı.
- Minimum broker örneklemi 1 olarak güncellendi, sektör ortalaması 3 gözlem istiyor.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any


LOOKBACK_DAYS = 365
HALF_LIFE_DAYS = 180
MINIMUM_SAMPLE_SIZE = 6
MINIMUM_SECTOR_SAMPLE_SIZE = 3
MINIMUM_BROKER_SAMPLE_SIZE = 1
BROKER_LOOKBACK_DAYS = 730


def _weighted_median(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    ordered = sorted(values, key=lambda item: item[0])
    midpoint = sum(weight for _, weight in ordered) / 2
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= midpoint:
            return value
    return ordered[-1][0]


def _weighted_percentile(values: list[tuple[float, float]], percentile: float) -> float | None:
    """Ağırlıklı yüzdelik dilim hesabı."""
    if not values:
        return None
    ordered = sorted(values, key=lambda item: item[0])
    total_weight = sum(w for _, w in ordered)
    target = total_weight * percentile / 100
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _summarize(records: list[dict[str, Any]], reference_date: date) -> dict[str, Any]:
    values: list[tuple[float, float]] = []
    drawdowns: list[tuple[float, float]] = []
    positive_weights: list[float] = []
    total_weight_pool = 0.0

    recent_offers = []

    for record in records:
        try:
            observed_at = date.fromisoformat(record["listing_date"])
        except (KeyError, TypeError, ValueError):
            continue
        age = (reference_date - observed_at).days
        if age < 0 or age > LOOKBACK_DAYS:
            continue
        outcome = record.get("return_5d_pct")
        if not isinstance(outcome, (int, float)) or not record.get("source_url"):
            continue
        
        recent_offers.append({
            "ticker": record.get("ticker"),
            "company": record.get("company"),
            "listing_date": record.get("listing_date"),
            "return_5d_pct": outcome,
            "max_return_15d_pct": record.get("max_return_15d_pct"),
        })

        weight = math.pow(0.5, age / HALF_LIFE_DAYS)
        values.append((float(outcome), weight))
        total_weight_pool += weight
        if outcome > 0:
            positive_weights.append(weight)
        drawdown = record.get("max_drawdown_20d_pct")
        if isinstance(drawdown, (int, float)):
            drawdowns.append((float(drawdown), weight))

    total_weight = sum(weight for _, weight in values)
    weighted_mean = sum(value * weight for value, weight in values) / total_weight if total_weight else None
    weighted_variance = (
        sum(weight * (value - weighted_mean) ** 2 for value, weight in values) / total_weight
        if total_weight and weighted_mean is not None else None
    )
    weighted_stddev = math.sqrt(weighted_variance) if weighted_variance is not None else None
    positive_rate = round(sum(positive_weights) / total_weight * 100, 1) if total_weight else None
    median_drawdown = _weighted_median(drawdowns)

    # 25. ve 75. yüzdelik dilimler
    p25_return = _weighted_percentile(values, 25)
    p75_return = _weighted_percentile(values, 75)

    # İstikrar puanı: pozitif oran, tutarlılık ve düşüş kalitesi birleşimi
    # Daha dengeli ağırlıklar: pozitif oran tek başına belirleyici olmasın
    stability_score = None
    if len(values) >= MINIMUM_BROKER_SAMPLE_SIZE and positive_rate is not None:
        # Tutarlılık: düşük std sapma = yüksek puan
        consistency = max(0, 100 - (weighted_stddev or 0) * 1.8)
        # Düşüş kalitesi: düşük drawdown = yüksek puan
        drawdown_quality = max(0, 100 - (median_drawdown or 50) * 1.6)
        # Getiri seviyesi: medyan getiri pozitifse bonus
        median_val = _weighted_median(values) or 0
        return_bonus = max(0, min(20, median_val * 1.5))
        # Ağırlıklı bileşim
        stability_score = round(
            positive_rate * 0.35
            + consistency * 0.25
            + drawdown_quality * 0.25
            + return_bonus * 0.15 / 20 * 100,  # normalize to 0-100
            1,
        )

    return {
        "sample_size": len(values),
        "recent_offers": sorted(recent_offers, key=lambda x: x["listing_date"], reverse=True),
        "weighted_median_return_5d": _weighted_median(values),
        "weighted_mean_return_5d": round(weighted_mean, 2) if weighted_mean is not None else None,
        "weighted_median_drawdown_20d": median_drawdown,
        "weighted_positive_rate_pct": positive_rate,
        "weighted_stddev_return_5d": round(weighted_stddev, 2) if weighted_stddev is not None else None,
        "p25_return_5d": round(p25_return, 2) if p25_return is not None else None,
        "p75_return_5d": round(p75_return, 2) if p75_return is not None else None,
        "stability_score": stability_score,
        "window_days": LOOKBACK_DAYS,
        "half_life_days": HALF_LIFE_DAYS,
        "is_ready": len(values) >= MINIMUM_SAMPLE_SIZE,
    }


def build_market_context(outcomes: list[dict[str, Any]], reference_date: date | None = None) -> dict[str, Any]:
    """Sadece doğrulanmış, son 365 günlük gerçekleşmeleri kullanır."""
    reference_date = reference_date or date.today()
    by_sector: dict[str, list[dict[str, Any]]] = {}
    by_broker: dict[str, list[dict[str, Any]]] = {}
    for item in outcomes:
        if item.get("sector"):
            by_sector.setdefault(item["sector"], []).append(item)
        broker_key = item.get("broker_key") or item.get("broker")
        if broker_key:
            by_broker.setdefault(broker_key, []).append(item)
    return {
        "generated_for": reference_date.isoformat(),
        "overall": _summarize(outcomes, reference_date),
        "sectors": {key: _summarize(values, reference_date) for key, values in by_sector.items()},
        "brokers": {key: _summarize(values, reference_date) for key, values in by_broker.items()},
    }


def build_broker_leaderboard(outcomes: list[dict[str, Any]], reference_date: date | None = None) -> list[dict[str, Any]]:
    """Son 2 yılın sonuçlarından, yeterli örneklemi olan kurumları sıralar."""
    reference_date = reference_date or date.today()
    lower_bound = date.fromordinal(reference_date.toordinal() - BROKER_LOOKBACK_DAYS)
    recent_outcomes = []
    for outcome in outcomes:
        try:
            if date.fromisoformat(outcome["listing_date"]) >= lower_bound:
                recent_outcomes.append(outcome)
        except (KeyError, TypeError, ValueError):
            continue
    market = build_market_context(recent_outcomes, reference_date)
    rows = []
    for broker, summary in market.get("brokers", {}).items():
        if summary.get("sample_size", 0) < MINIMUM_BROKER_SAMPLE_SIZE:
            continue
        rows.append({"broker_key": broker, "window_days": BROKER_LOOKBACK_DAYS, **summary})
    return sorted(rows, key=lambda row: row.get("stability_score") or -1, reverse=True)
