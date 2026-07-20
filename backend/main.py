"""BIST halka arz inceleme uygulamasının komut satırı giriş noktası."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from analysis.backtest import build_broker_leaderboard, build_market_context
from analysis.scoring_engine import COMPONENT_WEIGHTS, assess_offer
from data.market_data import collect_recent_outcomes
from data.sources import HalkarzSource

DATA_DIR = BASE_DIR / "data"
REPORT_PATH = DATA_DIR / "report.json"
MARKDOWN_REPORT_PATH = DATA_DIR / "latest_report.md"
OVERRIDES_PATH = DATA_DIR / "metrics_overrides.json"
OUTCOMES_PATH = DATA_DIR / "recent_outcomes.json"


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = {**base}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _add_calendar_context(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for offer in offers:
        start = date.fromisoformat(offer["start_date"])
        offer["concurrent_offer_count"] = sum(
            abs((date.fromisoformat(other["start_date"]) - start).days) <= 6 for other in offers
        )
    return offers


def _add_schedule_status(offers: list[dict[str, Any]], reference_date: date) -> list[dict[str, Any]]:
    """Kartların aktif talep mi, yaklaşan takvim mi olduğunu açıkça işaretler."""
    for offer in offers:
        start = date.fromisoformat(offer["start_date"])
        end = date.fromisoformat(offer.get("end_date") or offer["start_date"])
        offer["schedule_status"] = "active" if start <= reference_date <= end else "upcoming"
    return offers


def _build_historical_offers(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Geçmiş arzları, yalnızca kendi gününden önce bilinebilen sonuçlarla puanlar.

    Böylece 2026 Temmuz'undaki piyasa sonucu Ocak 2026 arzının puanını etkilemez;
    gerçekleşen getiri puandan ayrı bir gözlem olarak kalır.
    """
    historical: list[dict[str, Any]] = []
    for outcome in outcomes:
        snapshot = outcome.get("offer_snapshot")
        try:
            listing_date = date.fromisoformat(outcome["listing_date"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(snapshot, dict) or not snapshot.get("ticker"):
            continue
        earlier_outcomes = [
            item for item in outcomes
            if item.get("listing_date", "") < listing_date.isoformat()
        ]
        historical_market = build_market_context(earlier_outcomes, listing_date)
        offer = {
            **snapshot,
            "listing_date": listing_date.isoformat(),
            "concurrent_offer_count": None,
        }
        assessed = assess_offer(offer, historical_market, listing_date)
        historical.append({
            **assessed,
            "historical_outcome": {
                key: outcome.get(key)
                for key in (
                    "listing_date", "return_5d_pct", "return_20d_pct", "max_drawdown_20d_pct",
                    "max_limit_up_streak", "limit_up_method", "latest_close_tl", "latest_close_date",
                    "return_since_ipo_pct", "fifth_session_date", "observation_count",
                    "latest_turnover_pct", "is_streak_active",
                    "source_url", "price_source_url",
                )
            },
        })
    return sorted(historical, key=lambda item: item["listing_date"], reverse=True)


def _format_score(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}/100"


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Halka Arz İnceleme Raporu",
        "",
        f"Üretim zamanı: {report['generated_at']}",
        "",
        "Bu belge yatırım tavsiyesi değildir. Puan; doğrulanmış kanıt, risk ve inceleme önceliğini gösterir; gelecekteki getiri veya tavan/taban sonucunu tahmin etmez.",
        "",
        "## Yöntem",
        "",
        "- Yakın dönem emsalleri yalnızca son 365 günden alır; gözlemler 180 günlük yarı ömürle ağırlıklandırılır.",
        "- Emsal ortamı ve aracı kurum sinyali, en az 6 / 1 doğrulanmış gözlem yoksa puanlanmaz.",
        "- Veri kapsaması %65'in altındaysa sistem sonuç etiketi yerine belge tamamlama ister.",
        "- Aracı kurum geçmişi en fazla 10 puanlık ikincil bir sinyaldir; nedensel başarı göstergesi kabul edilmez.",
        "",
        "## Güncel Takvim",
        "",
        "| Kod | Talep toplama | Kanıt puanı | Veri kapsaması | İnceleme etiketi |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for offer in report["offers"]:
        assessment = offer["assessment"]
        lines.append(
            f"| {offer['ticker']} | {offer['calendar_date_text']} | {_format_score(assessment['evidence_score'])} | %{assessment['evidence_coverage_pct']:.0f} | {assessment['decision_label']} |"
        )
    for offer in report["offers"]:
        assessment = offer["assessment"]
        lines.extend(["", f"## {offer['ticker']} — {offer['company']}", ""])
        lines.append(f"- Talep toplama: {offer['calendar_date_text']} | Fiyat: {offer.get('ipo_price_tl') or '—'} TL | Aracı kurum: {offer.get('broker') or '—'}")
        lines.append(f"- Kanıt puanı: {_format_score(assessment['evidence_score'])}; veri kapsaması: %{assessment['evidence_coverage_pct']:.0f}.")
        if assessment["red_flags"]:
            lines.append("- Risk işaretleri: " + " ".join(assessment["red_flags"]))
        lines.append("- Kontrol listesi: " + " ".join(assessment["review_questions"]))
        lines.append("- Kaynaklar:")
        for source in offer.get("metric_sources", []) + offer.get("documents", []):
            lines.append(f"  - [{source['name']}]({source['url']})")
    return "\n".join(lines) + "\n"


def refresh_report(reference_date: date | None = None, force_market_refresh: bool = False) -> dict[str, Any]:
    """Canlı takvimi alır, resmi metrik notlarıyla birleştirir ve raporu yazar."""
    reference_date = reference_date or date.today()
    overrides = _read_json(OVERRIDES_PATH, {})
    outcomes, outcome_collection = collect_recent_outcomes(
        reference_date,
        OUTCOMES_PATH,
        force=force_market_refresh,
    )
    offers = HalkarzSource().fetch_current_and_upcoming(reference_date)
    offers = [_deep_merge(offer, overrides.get(offer["ticker"], {})) for offer in offers]
    offers = _add_schedule_status(_add_calendar_context(offers), reference_date)
    market = build_market_context(outcomes, reference_date)
    assessed_offers = [assess_offer(offer, market, reference_date) for offer in offers]
    assessed_offers.sort(key=lambda item: item["start_date"])
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_status": "live",
        "source": "https://halkarz.com/",
        "methodology": {
            "primary_window_days": 365,
            "half_life_days": 180,
            "minimum_market_sample": 6,
            "minimum_broker_sample": 1,
            "components": COMPONENT_WEIGHTS,
        },
        "market_context": market,
        "broker_leaderboard": build_broker_leaderboard(outcomes, reference_date),
        "broker_leaderboard_window_days": 183,
        "outcome_collection": outcome_collection,
        "offers": assessed_offers,
        "recent_outcomes": sorted(outcomes, key=lambda item: item["listing_date"], reverse=True),
        "historical_offers": _build_historical_offers(outcomes),
        "disclaimer": "Yatırım tavsiyesi değildir. Sistem, eksik veya doğrulanmamış veriden kesin katılım/katılmama sonucu üretmez.",
    }
    DATA_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MARKDOWN_REPORT_PATH.write_text(_render_markdown(report), encoding="utf-8")
    return report


def load_report() -> dict[str, Any]:
    return _read_json(REPORT_PATH, {})


def main() -> None:
    parser = argparse.ArgumentParser(description="Halka arz kanıt-temelli inceleme sistemi")
    parser.add_argument("command", nargs="?", choices=("refresh", "print"), default="refresh")
    parser.add_argument(
        "--force-market-refresh",
        action="store_true",
        help="Rebuild the recent-outcome cache without using its 12-hour TTL.",
    )
    arguments = parser.parse_args()
    if arguments.command == "refresh":
        report = refresh_report(force_market_refresh=arguments.force_market_refresh)
        print(f"{len(report['offers'])} arz güncellendi: {REPORT_PATH}")
        collection = report["outcome_collection"]
        print(
            f"Yakın dönem emsal verisi: {collection.get('outcome_count', 0)} sonuç "
            f"({collection.get('mode', 'bilinmiyor')})"
        )
        print(f"Markdown rapor: {MARKDOWN_REPORT_PATH}")
        return
    report = load_report()
    if not report:
        report = refresh_report()
    rendered = _render_markdown(report)
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(rendered.encode("utf-8"))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
