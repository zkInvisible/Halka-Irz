"""Rapor üretim hattı.

Tek bir yenileme şu adımlardan oluşur:

1. Takvim ve taslak listesini al (tek istek).
2. Yakın dönem arşiv adaylarını topla, eksiksizliği kaynağın kendi kayıt
   sayısıyla doğrula.
3. Detay sayfalarını **önbellekli** olarak çöz. Tamamlanmış bir arzın detay
   sayfası bir daha değişmediği için kalıcı olarak saklanır; her yenilemede
   80+ sayfayı yeniden indirmek ücretsiz sunucuda hem yavaştı hem de kaynağın
   bot korumasını tetikliyordu.
4. Fiyat serilerinden gerçekleşme ölçütlerini üret.
5. Puanla ve raporu yaz.

Tasarım kuralı: **tek bir kaynak hatası bütün raporu düşürmez.** Her aşama
kendi hatasını sayar, rapor yine üretilir ve ``collection`` bölümünde neyin
alınamadığı açıkça bildirilir.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from analysis.backtest import build_broker_leaderboard, build_market_context
from analysis.scoring_engine import COMPONENT_WEIGHTS, assess_offer
from data.market_data import SKIP_REASONS, PriceRepository, outcome_from_offer
from data.sources import BotProtectionError, HalkarzSource, SourceError
from data.store import get_store

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OVERRIDES_PATH = DATA_DIR / "metrics_overrides.json"

REPORT_KEY = "report:current"
# Rapor 700 KB'ı aşabiliyor. Yalnızca "veri ne kadar taze" sorusunu cevaplamak
# için tamamını okuyup çözümlemek pahalı; özet ayrı bir anahtarda tutulur.
REPORT_META_KEY = "report:meta"
DETAIL_PREFIX = "detail:"

LOOKBACK_DAYS = 730
DETAIL_WORKERS = 6
PRICE_WORKERS = 6

ProgressCallback = Callable[[str, dict[str, Any]], None]


def _noop_progress(stage: str, detail: dict[str, Any]) -> None:  # pragma: no cover - varsayılan
    return None


def _read_json_file(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = {**base}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _detail_cache_hours(start_date: str | None, reference_date: date) -> float | None:
    """Detay sayfasının ne kadar süre önbellekte tutulacağı.

    ``None`` = süresiz. Dağıtım sonuçları arzdan birkaç gün sonra yayımlandığı
    için "tamamlandı" kararını 90 gün sonra veriyoruz.
    """
    if not start_date:
        return 6.0
    try:
        age_days = (reference_date - date.fromisoformat(start_date)).days
    except ValueError:
        return 6.0
    if age_days > 90:
        return None
    if age_days > 30:
        return 24.0
    if age_days > 0:
        return 3.0
    return 2.0


class RefreshPipeline:
    """Raporu baştan sona üretir."""

    def __init__(self, store: Any | None = None, source: HalkarzSource | None = None):
        self.store = store if store is not None else get_store()
        self.source = source or HalkarzSource()
        self.prices = PriceRepository(store=self.store)

    # ── detay önbelleği ────────────────────────────────────────────────────
    def _load_cached_details(self, entries: list[dict[str, Any]], reference_date: date) -> dict[str, dict[str, Any]]:
        keys = [f"{DETAIL_PREFIX}{entry['slug']}" for entry in entries]
        raw = self.store.get_many(keys)
        usable: dict[str, dict[str, Any]] = {}
        now = datetime.now(timezone.utc)
        for entry in entries:
            cached = raw.get(f"{DETAIL_PREFIX}{entry['slug']}")
            if not isinstance(cached, dict) or "detail" not in cached:
                continue
            detail = cached["detail"]
            ttl_hours = _detail_cache_hours(detail.get("start_date") or entry.get("start_date"), reference_date)
            if ttl_hours is None:
                usable[entry["slug"]] = detail
                continue
            try:
                fetched_at = datetime.fromisoformat(cached.get("fetched_at", ""))
            except ValueError:
                continue
            if now - fetched_at < timedelta(hours=ttl_hours):
                usable[entry["slug"]] = detail
        return usable

    def _fetch_details(
        self,
        entries: list[dict[str, Any]],
        reference_date: date,
        progress: ProgressCallback,
        *,
        force: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Detayları önbellekten okur, eksik olanları paralel indirir."""
        cached = {} if force else self._load_cached_details(entries, reference_date)
        pending = [entry for entry in entries if entry["slug"] not in cached]
        details: list[dict[str, Any]] = []
        for entry in entries:
            if entry["slug"] in cached:
                # Liste tarafındaki taze alanları (ör. BIST kodu) koru.
                details.append({**cached[entry["slug"]], **{k: v for k, v in entry.items() if v}})

        failures: list[str] = []
        bot_blocked = 0
        fresh: dict[str, Any] = {}
        completed = 0

        if pending:
            progress("detay", {"toplam": len(pending), "tamamlanan": 0, "onbellek": len(cached)})
            with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
                futures = {executor.submit(self.source.fetch_detail, entry): entry for entry in pending}
                for future in as_completed(futures):
                    entry = futures[future]
                    completed += 1
                    try:
                        detail = future.result()
                    except BotProtectionError:
                        bot_blocked += 1
                        failures.append(entry["slug"])
                        continue
                    except (SourceError, ValueError, KeyError, TypeError) as error:
                        failures.append(f"{entry['slug']} ({type(error).__name__})")
                        continue
                    details.append(detail)
                    fresh[f"{DETAIL_PREFIX}{entry['slug']}"] = {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "detail": detail,
                    }
                    if completed % 10 == 0:
                        progress("detay", {"toplam": len(pending), "tamamlanan": completed, "onbellek": len(cached)})

        if fresh:
            self.store.set_many(fresh)

        status = {
            "istenen": len(entries),
            "onbellekten": len(cached),
            "indirilen": len(fresh),
            "basarisiz": len(failures),
            "bot_korumasi": bot_blocked,
            "basarisiz_ornekler": failures[:8],
        }
        return details, status

    # ── gerçekleşmeler ─────────────────────────────────────────────────────
    def _collect_outcomes(
        self,
        offers: list[dict[str, Any]],
        reference_date: date,
        progress: ProgressCallback,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        outcomes: list[dict[str, Any]] = []
        skipped: dict[str, list[str]] = {}
        pending_listing: list[dict[str, Any]] = []
        completed = 0

        progress("fiyat", {"toplam": len(offers), "tamamlanan": 0})
        with ThreadPoolExecutor(max_workers=PRICE_WORKERS) as executor:
            futures = {
                executor.submit(outcome_from_offer, offer, reference_date, self.prices): offer
                for offer in offers
            }
            for future in as_completed(futures):
                offer = futures[future]
                completed += 1
                try:
                    outcome, reason = future.result()
                except Exception as error:  # noqa: BLE001 - tek sembol tüm raporu düşürmesin
                    outcome, reason = None, f"hata:{type(error).__name__}"
                if outcome:
                    outcomes.append(outcome)
                else:
                    skipped.setdefault(reason or "bilinmiyor", []).append(
                        str(offer.get("ticker") or offer.get("company", "?"))
                    )
                    if reason == "islem_baslamadi":
                        pending_listing.append(offer)
                if completed % 10 == 0:
                    progress("fiyat", {"toplam": len(offers), "tamamlanan": completed})

        outcomes.sort(key=lambda item: item["listing_date"], reverse=True)
        status = {
            "denenen": len(offers),
            "uretilen": len(outcomes),
            "atlanan": sum(len(items) for items in skipped.values()),
            "atlama_nedenleri": {
                reason: {"aciklama": SKIP_REASONS.get(reason, reason), "kodlar": sorted(items)}
                for reason, items in sorted(skipped.items())
            },
            "onbellek_isabeti": self.prices.hits,
            "yeni_istek": self.prices.misses,
            "yontem": (
                "Halka arz fiyatından, ilk işlem gününü 1. seans kabul eden 5. kapanışa getiri; "
                "ilk 20 seanstaki azami düşüş."
            ),
        }
        return outcomes, status, pending_listing

    # ── dağıtım senaryoları ────────────────────────────────────────────────
    @staticmethod
    def _participation_band(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        """Son arzlardaki gerçekleşen bireysel katılım aralığı."""
        counts = sorted(
            outcome["participant_count"]
            for outcome in outcomes[:12]
            if isinstance(outcome.get("participant_count"), (int, float)) and outcome["participant_count"] > 0
        )
        if not counts:
            return {}
        return {
            "sample_size": len(counts),
            "min": counts[0],
            "median": counts[len(counts) // 2],
            "max": counts[-1],
        }

    @staticmethod
    def _build_scenarios(offer: dict[str, Any], band: dict[str, Any]) -> list[dict[str, Any]]:
        """Gerçekçi katılım basamakları için kişi başına düşen lotu hesaplar.

        Kaynak kendi basamaklarını yayımlıyor ama bunlar 150 binden 2,2 milyona
        kadar uzanıyor ve büyük kısmı gerçekçi değil: son arzlarda katılım
        550 bin – 1,1 milyon aralığında kaldı. Havuz bilindiği için bandı
        kendimiz üretiyoruz.
        """
        pool = offer.get("retail_lot_pool")
        price = offer.get("ipo_price_tl")
        if not pool or not price:
            return []

        levels = [600_000, 700_000, 800_000, 900_000, 1_000_000]
        median = band.get("median")
        scenarios = []
        for participants in levels:
            lots = pool / participants
            if lots < 0.5:
                continue
            scenarios.append({
                "participants": participants,
                "lot_per_person": round(lots) if lots >= 1 else round(lots, 2),
                "tl_per_person": round(round(lots) * price if lots >= 1 else lots * price),
                # Son arzların medyanına en yakın basamak arayüzde vurgulanır.
                "is_likely": bool(median and abs(participants - median) <= 50_000),
            })
        if median and not any(item["is_likely"] for item in scenarios):
            closest = min(scenarios, key=lambda item: abs(item["participants"] - median), default=None)
            if closest:
                closest["is_likely"] = True
        return scenarios

    # ── yardımcılar ────────────────────────────────────────────────────────
    @staticmethod
    def _add_calendar_context(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for offer in offers:
            try:
                start = date.fromisoformat(offer["start_date"])
            except (KeyError, TypeError, ValueError):
                offer["concurrent_offer_count"] = None
                continue
            count = 0
            for other in offers:
                try:
                    other_start = date.fromisoformat(other["start_date"])
                except (KeyError, TypeError, ValueError):
                    continue
                if abs((other_start - start).days) <= 6:
                    count += 1
            offer["concurrent_offer_count"] = count
        return offers

    @staticmethod
    def _add_schedule_status(offers: list[dict[str, Any]], reference_date: date) -> list[dict[str, Any]]:
        """Kartın hangi aşamada olduğunu işaretler.

        ``upcoming`` → talep toplama başlamadı
        ``active``   → talep toplama sürüyor
        ``pending_listing`` → talep toplandı, BIST'te işlem henüz başlamadı
        """
        for offer in offers:
            try:
                start = date.fromisoformat(offer["start_date"])
                end = date.fromisoformat(offer.get("end_date") or offer["start_date"])
            except (KeyError, TypeError, ValueError):
                offer["schedule_status"] = "upcoming"
                continue
            if reference_date < start:
                offer["schedule_status"] = "upcoming"
            elif reference_date <= end:
                offer["schedule_status"] = "active"
            else:
                offer["schedule_status"] = "pending_listing"
        return offers

    @staticmethod
    def _build_historical_offers(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Geçmiş arzları, yalnızca kendi gününden önce bilinebilen sonuçlarla puanlar.

        Böylece bugünkü piyasa sonucu aylar önceki arzın puanını etkilemez;
        gerçekleşen getiri puandan ayrı bir gözlem olarak kalır.
        """
        historical: list[dict[str, Any]] = []
        outcome_keys = (
            "listing_date", "return_5d_pct", "return_20d_pct", "max_drawdown_20d_pct",
            "max_limit_up_streak", "limit_up_method", "latest_close_tl", "latest_close_date",
            "return_since_ipo_pct", "max_return_15d_pct", "max_return_20d_pct",
            "peak_close_tl", "peak_return_pct", "peak_date", "peak_session", "peak_is_latest",
            "peak_method", "fifth_session_date", "observation_count",
            "latest_turnover_pct", "is_streak_active", "is_partial_5d",
            "source_url", "price_source_url",
        )
        for outcome in outcomes:
            snapshot = outcome.get("offer_snapshot")
            if not isinstance(snapshot, dict) or not snapshot.get("ticker"):
                continue
            try:
                listing_date = date.fromisoformat(outcome["listing_date"])
            except (KeyError, TypeError, ValueError):
                continue
            earlier = [item for item in outcomes if item.get("listing_date", "") < listing_date.isoformat()]
            historical_market = build_market_context(earlier, listing_date)
            offer = {**snapshot, "listing_date": listing_date.isoformat(), "concurrent_offer_count": None}
            assessed = assess_offer(offer, historical_market, listing_date)
            historical.append({
                **assessed,
                "historical_outcome": {key: outcome.get(key) for key in outcome_keys},
            })
        return sorted(historical, key=lambda item: item["listing_date"], reverse=True)

    # ── ana akış ───────────────────────────────────────────────────────────
    def run(
        self,
        reference_date: date | None = None,
        progress: ProgressCallback | None = None,
        *,
        force_details: bool = False,
    ) -> dict[str, Any]:
        reference_date = reference_date or date.today()
        progress = progress or _noop_progress
        started = datetime.now(timezone.utc)
        warnings: list[str] = []

        # 1) Takvim + taslaklar
        progress("takvim", {})
        calendar = self.source.fetch_calendar()
        scheduled = calendar["scheduled"]
        drafts = calendar["drafts"]
        try:
            official_draft_count = self.source.draft_category_count()
        except SourceError:
            official_draft_count = None
        if official_draft_count and len(drafts) < official_draft_count:
            warnings.append(
                f"Taslak listesi eksik olabilir: {len(drafts)}/{official_draft_count}."
            )

        # 2) Arşiv adayları
        lower_bound = date.fromordinal(reference_date.toordinal() - LOOKBACK_DAYS)
        progress("arsiv", {"yillar": f"{lower_bound.year}-{reference_date.year}"})
        try:
            archive, coverage = self.source.fetch_archive_entries(range(lower_bound.year, reference_date.year + 1))
        except SourceError as error:
            archive, coverage = [], {}
            warnings.append(f"Arşiv taraması başarısız: {error}")
        for year, info in coverage.items():
            if info.get("expected") and not info.get("complete"):
                warnings.append(f"{year} arşivi eksik: {info['collected']}/{info['expected']}.")

        # 3) Detaylar (takvim + arşiv birlikte, tek önbellekten)
        by_url: dict[str, dict[str, Any]] = {}
        for entry in archive + scheduled:
            by_url[entry["detail_url"].rstrip("/")] = entry
        details, detail_status = self._fetch_details(
            list(by_url.values()), reference_date, progress, force=force_details
        )

        overrides = _read_json_file(OVERRIDES_PATH, {})
        details = [_deep_merge(item, overrides.get(item.get("ticker") or "", {})) for item in details]

        # 4) Güncel arzlar ile geçmiş arzları ayır
        current: list[dict[str, Any]] = []
        completed: list[dict[str, Any]] = []
        for detail in details:
            start_raw = detail.get("start_date")
            if not start_raw:
                continue
            try:
                start = date.fromisoformat(start_raw)
                end = date.fromisoformat(detail.get("end_date") or start_raw)
            except ValueError:
                continue
            if end >= reference_date:
                current.append(detail)
            elif lower_bound <= start < reference_date:
                completed.append(detail)

        # 5) Gerçekleşmeler
        outcomes, outcome_status, pending_listing = self._collect_outcomes(completed, reference_date, progress)

        # Talep toplaması bitmiş ama borsada işlem görmeye başlamamış arzlar
        # eski sürümde ikisinin arasında kalıp arayüzden tamamen kayboluyordu.
        # Kullanıcı tam da bu aralıkta sonucu merak ettiği için ayrı bir
        # durumla güncel listede tutuluyorlar.
        recent_pending = [
            offer for offer in pending_listing
            if (reference_date - date.fromisoformat(offer["end_date"] or offer["start_date"])).days <= 60
        ]
        current = current + recent_pending
        current.sort(key=lambda item: item["start_date"])
        current = self._add_schedule_status(self._add_calendar_context(current), reference_date)

        # 6) Dağıtım senaryoları — gerçekleşen katılım verisine dayanan band
        participation_band = self._participation_band(outcomes)
        for offer in current:
            if offer.get("realised_lot_per_person") is None:
                offer["distribution_scenarios"] = self._build_scenarios(offer, participation_band)
            else:
                offer["distribution_scenarios"] = []

        # 7) Puanlama
        progress("puanlama", {"arz": len(current)})
        market = build_market_context(outcomes, reference_date)
        assessed = [assess_offer(offer, market, reference_date) for offer in current]
        assessed.sort(key=lambda item: item["start_date"])

        progress("gecmis", {"kayit": len(outcomes)})
        historical = self._build_historical_offers(outcomes)

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reference_date": reference_date.isoformat(),
            "build_seconds": round(elapsed, 1),
            "source_status": "live",
            "source": "https://halkarz.com/",
            "methodology": {
                "primary_window_days": 365,
                "half_life_days": 180,
                "minimum_market_sample": 6,
                "minimum_broker_sample": 1,
                "archive_lookback_days": LOOKBACK_DAYS,
                "components": COMPONENT_WEIGHTS,
            },
            "collection": {
                "takvim_kaydi": len(scheduled),
                "taslak_kaydi": len(drafts),
                "taslak_kaynak_sayisi": official_draft_count,
                "arsiv_kaydi": len(archive),
                "arsiv_kapsama": coverage,
                "detay": detail_status,
                "gerceklesme": outcome_status,
                "uyarilar": warnings,
                "sure_saniye": round(elapsed, 1),
            },
            "participation_band": participation_band,
            "market_context": market,
            "broker_leaderboard": build_broker_leaderboard(outcomes, reference_date),
            "broker_leaderboard_window_days": 730,
            "offers": assessed,
            "draft_offers": sorted(drafts, key=lambda item: item["company"]),
            "recent_outcomes": outcomes,
            "historical_offers": historical,
            "disclaimer": (
                "Yatırım tavsiyesi değildir. Sistem, eksik veya doğrulanmamış veriden kesin "
                "katılım/katılmama sonucu üretmez."
            ),
        }
        # Geriye dönük uyumluluk: eski arayüz alanı.
        report["outcome_collection"] = {
            "mode": "refreshed",
            "candidate_count": len(archive),
            "eligible_offer_count": len(completed),
            "outcome_count": len(outcomes),
            "source_failures": detail_status["basarisiz"],
            "price_unavailable_count": outcome_status["atlanan"],
            "price_method": outcome_status["yontem"],
        }

        progress("kaydediliyor", {})
        self.store.set(REPORT_KEY, report)
        self.store.set(REPORT_META_KEY, build_report_meta(report))
        return report


_write_lock = threading.Lock()


def refresh_report(
    reference_date: date | None = None,
    progress: ProgressCallback | None = None,
    *,
    force_details: bool = False,
) -> dict[str, Any]:
    """Raporu yeniden üretir. Aynı anda yalnızca bir yenileme çalışır."""
    with _write_lock:
        return RefreshPipeline().run(reference_date, progress, force_details=force_details)


def build_report_meta(report: dict[str, Any]) -> dict[str, Any]:
    """Raporun tamamını okumadan durum sorularını cevaplayan küçük özet."""
    return {
        "generated_at": report.get("generated_at"),
        "reference_date": report.get("reference_date"),
        "source_status": report.get("source_status"),
        "offer_count": len(report.get("offers", [])),
        "draft_count": len(report.get("draft_offers", [])),
        "outcome_count": len(report.get("recent_outcomes", [])),
        "collection": report.get("collection"),
    }


def load_report() -> dict[str, Any]:
    """Depodaki son raporu döndürür."""
    report = get_store().get(REPORT_KEY)
    return report if isinstance(report, dict) else {}


def load_report_meta() -> dict[str, Any]:
    """Rapor özetini döndürür; yoksa raporun tamamından türetir."""
    meta = get_store().get(REPORT_META_KEY)
    if isinstance(meta, dict) and meta.get("generated_at"):
        return meta
    report = load_report()
    return build_report_meta(report) if report else {}


def report_age_hours(report: dict[str, Any] | None) -> float | None:
    if not report:
        return None
    generated = report.get("generated_at")
    if not generated:
        return None
    try:
        stamp = datetime.fromisoformat(generated)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
