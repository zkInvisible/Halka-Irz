"""Halka arz sonrası fiyat verisini toplar ve gerçekleşme ölçütlerini üretir.

İlk işlem tarihi ve halka arz fiyatı HalkArz detay sayfasından; günlük kapanış
fiyatları Yahoo Finance grafik uç noktasından alınır. İki kaynak ayrı ayrı
rapora yazılır. Bir kaynaktan veri alınamazsa kayıt tahmin edilmez, atlanır.

Eski sürüme göre farklar:

* Yahoo'nun ``query1`` uç noktası sık sık 429/503 döndürüyor ve tek bir hata
  o arzı tamamen rapordan düşürüyordu. Artık ``query2`` yedeği ve yeniden
  deneme var.
* İlk 45 seansı tamamlanmış arzların fiyat serisi bir daha değişmez; bu seriler
  kalıcı olarak önbelleğe alınır. Her yenilemede 100+ isteğin tekrarlanması
  ücretsiz sunucuda hem yavaştı hem de hız sınırına takılıyordu.
* Yahoo, bedelsiz sermaye artırımından sonra o hissenin **bütün geçmişini**
  yeni pay adedine göre böler. Halka arz fiyatı ise o günkü nominal değerdir.
  İkisi doğrudan karşılaştırıldığında arzdan sonra bedelsiz yapan her şirket
  "ilk gün %79 düştü" gibi görünüyordu. Seriler artık nominale geri çevrilir.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .http_client import HttpClient, SourceError, shared_client

PRICE_LOOKAHEAD_DAYS = 45
LIMIT_UP_THRESHOLD = 0.095
# Bu süre geçtikten sonra ilk 45 seans kesinleşmiştir; seri bir daha değişmez.
SERIES_SETTLED_AFTER_DAYS = 70
LATEST_CLOSE_TTL_HOURS = 12
SPLIT_EVENTS_TTL_HOURS = 12


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


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_split_events(result: dict[str, Any]) -> list[tuple[date, float]]:
    """Yahoo grafik yanıtındaki bölünme/bedelsiz olaylarını ``(tarih, kat)`` listesine çevirir."""
    raw_events = ((result.get("events") or {}).get("splits") or {})
    events: list[tuple[date, float]] = []
    for entry in raw_events.values():
        try:
            when = datetime.fromtimestamp(int(entry["date"]), tz=timezone.utc).date()
            numerator = float(entry["numerator"])
            denominator = float(entry["denominator"])
        except (AttributeError, KeyError, TypeError, ValueError, OSError, OverflowError):
            continue
        if numerator <= 0 or denominator <= 0:
            continue
        ratio = numerator / denominator
        if abs(ratio - 1) < 1e-9:
            continue
        events.append((when, ratio))
    events.sort()
    return events


def split_factor_after(events: list[tuple[date, float]], observed: date) -> float:
    """``observed`` gününden **sonra** gerçekleşen bölünmelerin bileşik katı."""
    factor = 1.0
    for when, ratio in events:
        if when > observed:
            factor *= ratio
    return factor


def unadjust_series(
    series: list[tuple[date, float, int]],
    events: list[tuple[date, float]],
) -> list[tuple[date, float, int]]:
    """Yahoo'nun bölünmeye göre düzelttiği seriyi o günkü gerçek işlem değerine çevirir.

    Yahoo bedelsizden sonra geçmiş fiyatları kata **böler**, hacmi ise aynı katla
    **çarpar**. Halka arz fiyatı nominal olduğundan, karşılaştırma yapılabilmesi
    için serinin nominale döndürülmesi gerekir. Tersi de mümkündü (arz fiyatını
    bölmek) ama o zaman "ilk gün kaç TL'ye satabilirdim" sorusunun cevabı
    ekranda bugünkü pay adedine göre küçülmüş bir sayı olurdu.
    """
    if not events:
        return series
    restored: list[tuple[date, float, int]] = []
    for observed, close, volume in series:
        factor = split_factor_after(events, observed)
        restored.append((observed, close * factor, int(round(volume / factor)) if factor > 0 else volume))
    return restored


class YahooChartSource:
    """Bağımlılıksız Yahoo Finance günlük kapanış okuyucusu."""

    hosts = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")

    def __init__(self, client: HttpClient | None = None):
        self.client = client or shared_client

    def _chart(self, ticker: str, params: dict[str, Any]) -> dict[str, Any] | None:
        last_error: Exception | None = None
        for host in self.hosts:
            url = f"https://{host}/v8/finance/chart/{ticker}.IS"
            try:
                payload = self.client.get_json(url, params)
            except (SourceError, ValueError) as error:
                last_error = error
                continue
            chart = (payload or {}).get("chart") or {}
            result = (chart.get("result") or [None])[0]
            if result:
                return result
            if chart.get("error"):
                # Sembol Yahoo'da yok; başka hosttan da gelmeyecek.
                return None
        if last_error:
            return None
        return None

    @staticmethod
    def _series_from_result(result: dict[str, Any], since: date | None) -> list[tuple[date, float, int]]:
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        series: list[tuple[date, float, int]] = []
        for index, raw_timestamp in enumerate(timestamps):
            raw_close = closes[index] if index < len(closes) else None
            raw_volume = volumes[index] if index < len(volumes) else 0
            if not isinstance(raw_close, (int, float)) or raw_close <= 0:
                continue
            observed = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).date()
            if since and observed < since:
                continue
            series.append((observed, float(raw_close), int(raw_volume or 0)))
        return series

    def closes_after(self, ticker: str, listing_date: date) -> list[tuple[date, float, int]]:
        """İlk işlem gününden itibaren günlük kapanış ve hacim serisi."""
        start = datetime.combine(listing_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=PRICE_LOOKAHEAD_DAYS)
        result = self._chart(ticker, {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "events": "history",
        })
        return self._series_from_result(result, listing_date) if result else []

    def latest_close(self, ticker: str) -> tuple[date, float, int] | None:
        """Son geçerli günlük kapanışı ve hacmi döndürür; anlık işlem fiyatı değildir.

        Serinin son satırı her zaman güncel pay adedine göredir; bölünme
        düzeltmesi gerektirmez.
        """
        result = self._chart(ticker, {"range": "1mo", "interval": "1d", "events": "history"})
        if not result:
            return None
        series = self._series_from_result(result, None)
        return series[-1] if series else None

    def split_events(self, ticker: str) -> list[tuple[date, float]]:
        """Sembolün bütün bölünme/bedelsiz olayları.

        Aylık aralık istenir: olay listesi eksiksiz gelir ama yanıt birkaç
        satırdır. Günlük seriyle birlikte istemek, yıllar öncesine uzanan
        sembollerde her yenilemede gereksiz megabaytlar indirmek olurdu.
        """
        result = self._chart(ticker, {"range": "max", "interval": "1mo", "events": "div,split"})
        return parse_split_events(result) if result else []


class PriceRepository:
    """Fiyat serilerini kalıcı önbellekle birlikte sunar."""

    def __init__(self, store: Any | None = None, chart_source: YahooChartSource | None = None):
        self.store = store
        self.source = chart_source or YahooChartSource()
        self.hits = 0
        self.misses = 0
        self._split_memo: dict[str, list[tuple[date, float]]] = {}

    @staticmethod
    def _series_key(ticker: str, listing_date: date) -> str:
        # v2: seriler artık bölünmeden arındırılmış (nominal) olarak saklanır.
        # Eski anahtardaki kayıtlar, bedelsiz yapmış hisselerde Yahoo'nun
        # düzeltilmiş fiyatlarını taşıyordu ve kalıcı önbellekte oldukları için
        # kendiliğinden düzelmiyorlardı.
        return f"prices:series:v2:{ticker}:{listing_date.isoformat()}"

    @staticmethod
    def _latest_key(ticker: str) -> str:
        return f"prices:latest:{ticker}"

    @staticmethod
    def _splits_key(ticker: str) -> str:
        return f"prices:splits:{ticker}"

    @staticmethod
    def _decode(rows: Any) -> list[tuple[date, float, int]]:
        series = []
        for row in rows or []:
            try:
                series.append((date.fromisoformat(row[0]), float(row[1]), int(row[2])))
            except (TypeError, ValueError, IndexError):
                continue
        return series

    @staticmethod
    def _encode(series: list[tuple[date, float, int]]) -> list[list[Any]]:
        return [[observed.isoformat(), close, volume] for observed, close, volume in series]

    @staticmethod
    def _decode_splits(rows: Any) -> list[tuple[date, float]]:
        events = []
        for row in rows or []:
            try:
                events.append((date.fromisoformat(row[0]), float(row[1])))
            except (TypeError, ValueError, IndexError):
                continue
        events.sort()
        return events

    def split_events(self, ticker: str) -> list[tuple[date, float]]:
        """Sembolün bölünme geçmişi; 12 saatlik önbellekle.

        Boş liste de önbelleğe yazılır: hisselerin çoğu hiç bedelsiz yapmaz ve
        aksi hâlde her yenilemede 50+ boş istek tekrarlanırdı.
        """
        memo = self._split_memo.get(ticker)
        if memo is not None:
            return memo

        if self.store is not None:
            envelope = self.store.get_envelope(self._splits_key(ticker))
            if envelope and envelope.get("updated_at"):
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(envelope["updated_at"])
                    if age < timedelta(hours=SPLIT_EVENTS_TTL_HOURS):
                        self.hits += 1
                        events = self._decode_splits(envelope["value"])
                        self._split_memo[ticker] = events
                        return events
                except (TypeError, ValueError):
                    pass

        events = self.source.split_events(ticker)
        self.misses += 1
        if self.store is not None:
            self.store.set(self._splits_key(ticker), [[when.isoformat(), ratio] for when, ratio in events])
        self._split_memo[ticker] = events
        return events

    def split_factor_since(self, ticker: str, listing_date: date) -> float:
        """Arzdan bu yana 1 lotun kaç lota dönüştüğü."""
        return split_factor_after(self.split_events(ticker), listing_date)

    def opening_series(self, ticker: str, listing_date: date, reference_date: date) -> list[tuple[date, float, int]]:
        """İlk 45 takvim günündeki nominal kapanışlar; kesinleşmişse önbellekten."""
        settled = (reference_date - listing_date).days > SERIES_SETTLED_AFTER_DAYS
        key = self._series_key(ticker, listing_date)
        if settled and self.store is not None:
            cached = self.store.get(key)
            if cached:
                self.hits += 1
                return self._decode(cached)

        series = self.source.closes_after(ticker, listing_date)
        self.misses += 1
        # Yahoo düzeltmesini geri al; kayıt ileride yapılacak bir bedelsizle bozulmasın.
        series = unadjust_series(series, self.split_events(ticker))
        if settled and series and self.store is not None:
            self.store.set(key, self._encode(series))
        return series

    def latest_close(self, ticker: str) -> tuple[date, float, int] | None:
        """Son kapanış; 12 saatlik önbellekle."""
        key = self._latest_key(ticker)
        if self.store is not None:
            envelope = self.store.get_envelope(key)
            if envelope and envelope.get("updated_at"):
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(envelope["updated_at"])
                    if age < timedelta(hours=LATEST_CLOSE_TTL_HOURS):
                        decoded = self._decode([envelope["value"]])
                        if decoded:
                            self.hits += 1
                            return decoded[0]
                except (TypeError, ValueError):
                    pass

        latest = self.source.latest_close(ticker)
        self.misses += 1
        if latest and self.store is not None:
            self.store.set(key, [latest[0].isoformat(), latest[1], latest[2]])
        return latest


SNAPSHOT_KEYS = (
    "ticker", "company", "slug", "calendar_date_text", "start_date", "end_date", "detail_url",
    "calendar_source", "logo_url", "sector", "sector_source", "ipo_price_tl", "ipo_price_high_tl",
    "is_price_range", "distribution_method", "sale_method", "broker", "is_consortium", "market",
    "offered_lots", "extra_lots", "primary_lots", "secondary_lots", "actual_float_lots",
    "actual_float_pct", "retail_allocation_pct", "float_pct", "stated_discount_pct",
    "use_of_proceeds", "price_stabilization", "lockup", "daily_buy_commitment", "offer_size_mn_tl",
    "allocation_groups", "participant_count", "total_participant_count", "retail_lots_distributed",
    "realised_lot_per_person", "realised_tl_per_person", "projected_distribution",
    "major_shareholders", "financials", "documents",
)


#: ``outcome_from_offer`` neden sonuç üretemediğini bu etiketlerle bildirir.
SKIP_REASONS = {
    "fiyatsiz": "Halka arz fiyatı açıklanmamış (bölünme/doğrudan kotasyon olabilir).",
    "kod_yok": "BIST kodu henüz atanmamış.",
    "tarih_yok": "İlk işlem tarihi okunamadı.",
    "islem_baslamadi": "Talep toplandı, borsada işlem henüz başlamadı.",
    "fiyat_gecmisi_yok": "Yahoo Finance bu sembol için ilgili dönemde veri döndürmedi.",
    "fiyat_tabani_uyumsuz": (
        "Fiyat serisi halka arz fiyatıyla aynı pay tabanında değil; ilk seans kapanışı "
        "borsanın taban limitinin altında görünüyor (kaydı tutulmamış bedelsiz olabilir)."
    ),
}

#: İlk seansta borsa taban limiti uygular; bu bir kural, piyasa hareketi değil.
#: Doğrulanmış 51 arzın en düşük ilk kapanışı %-11,6'dır. Bunun belirgin altındaki
#: bir değer fiyat hareketi olamaz — fiyat serisiyle halka arz fiyatının aynı pay
#: tabanında olmadığını gösterir. Yahoo, kaydını tutmadığı bir bedelsiz sermaye
#: artırımını fiyat geçmişine işlemiş olabilir; olay listesi boş geldiği için
#: ``unadjust_series`` bunu düzeltemez.
#:
#: Yalnızca alt sınır kontrol edilir: taban limiti sabit bir kuralken ilk seansın
#: yukarı marjı dönemlere göre değişmiştir (bu 51 arzda en yükseği %+48,6).
FIRST_SESSION_FLOOR_PCT = -20.0


def outcome_from_offer(
    offer: dict[str, Any],
    reference_date: date,
    repository: PriceRepository,
) -> tuple[dict[str, Any] | None, str | None]:
    """Tamamlanmış bir arz için 5 seans getirisi ve 20 seans risk ölçümünü üretir.

    ``(sonuç, atlama_nedeni)`` döndürür. Sonuç üretilemediğinde nedeni bildirmek,
    raporun "8 arzın fiyatı alınamadı" gibi anlamsız bir sayı yerine neyin
    eksik olduğunu göstermesini sağlar.
    """
    ticker = offer.get("ticker")
    if not ticker:
        return None, "kod_yok"

    ipo_price = _number(offer.get("ipo_price_tl"))
    if not ipo_price or ipo_price <= 0:
        return None, "fiyatsiz"

    raw_listing = offer.get("listing_date") or offer.get("start_date")
    try:
        listing_date = date.fromisoformat(raw_listing)
    except (TypeError, ValueError):
        return None, "tarih_yok"
    if listing_date > reference_date:
        return None, "islem_baslamadi"

    closes = repository.opening_series(ticker, listing_date, reference_date)
    if not closes:
        # İlk işlem tarihi çok yeniyse borsada gerçekten henüz seans olmamıştır;
        # eski bir tarihte veri yoksa kaynak tarafında geçmiş eksiktir.
        recently_listed = (reference_date - listing_date).days <= 5
        return None, "islem_baslamadi" if recently_listed else "fiyat_gecmisi_yok"

    if (closes[0][1] / ipo_price - 1) * 100 < FIRST_SESSION_FLOOR_PCT:
        # Sonrasındaki bütün ölçütler arz fiyatına oranlandığı için, tabanı
        # uymayan bir seriden üretilecek her sayı yanlış olurdu. Uydurma bir
        # düzeltme katı varsaymak yerine kaydı atlayıp nedenini bildiriyoruz.
        return None, "fiyat_tabani_uyumsuz"

    # HalkArz'da ilk işlem tarihi yoksa Yahoo'daki ilk seansı esas al.
    actual_listing_date = closes[0][0]
    latest = repository.latest_close(ticker)

    # BIST'teki ilk işlem gününü 1. seans kabul eder. 5. kapanış henüz oluşmadıysa
    # eldeki son kapanışı kullanır ve bunu ``is_partial_5d`` ile işaretler.
    fifth_index = min(4, len(closes) - 1)
    fifth_date, fifth_close, _ = closes[fifth_index]
    first_twenty = closes[:20]

    peak = ipo_price
    maximum_drawdown = 0.0
    for _, close, _ in first_twenty:
        peak = max(peak, close)
        maximum_drawdown = max(maximum_drawdown, (peak - close) / peak * 100)

    # Yahoo günlük kapanışları emir defterini içermediği için bu sayı resmî "tavan"
    # verisi değildir. Önceki kapanışa göre en az %9,5 yükselen kapanışların ardışık
    # serisini, kolay okunabilen bir yaklaşık tavan göstergesi olarak tutarız.
    longest_streak = 0
    current_streak = 0
    initial_streak_broken = False
    # Taban serisi, tavanın simetriği: arz fiyatının altında açılıp üst üste
    # taban yapan hisselerde "kaç taban gitti" sorusunun karşılığı.
    longest_down_streak = 0
    current_down_streak = 0
    opening_down_streak = 0
    opening_run_intact = True
    previous_close = ipo_price
    for _, close, _ in first_twenty:
        change = close / previous_close - 1
        if change >= LIMIT_UP_THRESHOLD:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 0
            initial_streak_broken = True

        if change <= -LIMIT_UP_THRESHOLD:
            current_down_streak += 1
            longest_down_streak = max(longest_down_streak, current_down_streak)
            if opening_run_intact:
                opening_down_streak += 1
        else:
            current_down_streak = 0
            opening_run_intact = False
        previous_close = close

    first_fifteen = closes[:15]
    max_return_15d_pct = (
        round((max(close for _, close, _ in first_fifteen) / ipo_price - 1) * 100, 2)
        if first_fifteen else None
    )
    max_return_20d_pct = (
        round((max(close for _, close, _ in first_twenty) / ipo_price - 1) * 100, 2)
        if first_twenty else None
    )

    # Gerçek "en fazla ne kadar kâr edilebilirdi" ölçüsü: eldeki bütün seansların
    # en yüksek kapanışının halka arz fiyatına oranı.
    #
    # Bunu tavan serisinden türetmek yanlış sonuç veriyordu: ``(1,10 ^ seri) - 1``
    # yalnızca hisse ilk günden itibaren yükseldiyse doğru. Tabandan açılıp
    # dipten bir gün sıçrayan hisse "1 tavan" sayılıp +%10 kâr göstermiş oluyordu,
    # oysa hiçbir seansta halka arz fiyatının üzerine çıkmamıştı.
    peak_date, peak_close, _ = max(closes, key=lambda row: row[1])
    peak_return_pct = round((peak_close / ipo_price - 1) * 100, 2)
    peak_session = next(
        (index for index, (observed, _, _) in enumerate(closes, start=1) if observed == peak_date),
        None,
    )
    # Zirve serinin son gününde ise hisse hâlâ yükseliyor olabilir.
    peak_is_latest = peak_date == closes[-1][0]

    # Dibin simetrik ölçümü.
    trough_date, trough_close, _ = min(closes, key=lambda row: row[1])
    trough_return_pct = round((trough_close / ipo_price - 1) * 100, 2)
    trough_session = next(
        (index for index, (observed, _, _) in enumerate(closes, start=1) if observed == trough_date),
        None,
    )

    # İlk gerçekçi çıkış noktası.
    #
    # Taban ile tavan simetrik değildir: tavanda alıcı yığıldığı için satılabilir,
    # tabanda alıcı olmadığı için satılamaz. Açılıştan itibaren taban giden bir
    # arzda yatırımcı, seri kırılana kadar pozisyonundan çıkamaz; ilk satabildiği
    # fiyat, taban serisinin bittiği seansın kapanışıdır. Dibi göstermek bu yüzden
    # yanıltıcı olur — dip, çıkış imkânı doğduktan günler sonra oluşmuş olabilir.
    first_sellable_return_pct = None
    first_sellable_session = None
    still_limit_down_locked = False
    if opening_down_streak:
        if opening_down_streak < len(closes):
            _, exit_close, _ = closes[opening_down_streak]
            first_sellable_return_pct = round((exit_close / ipo_price - 1) * 100, 2)
            first_sellable_session = opening_down_streak + 1
        else:
            # Eldeki bütün seanslar taban: hisse hâlâ satılamaz durumda.
            still_limit_down_locked = True

    # Arzdan sonra yapılan bedelsizler yatırımcının elindeki lot adedini artırır.
    # "Arzdan bugüne" getirisi bu yüzden yalnızca fiyat oranı değildir: 15,30 TL'ye
    # alınan 1 lot bugün 5,72 lota çıktıysa, 14,02 TL'lik fiyat kayıp değil kazançtır.
    split_history = [item for item in repository.split_events(ticker) if item[0] > listing_date]
    split_factor = split_factor_after(split_history, listing_date)

    latest_close_tl = latest_close_date = return_since_ipo_pct = None
    latest_turnover_pct = None
    if latest:
        observed, price, _ = latest
        latest_close_tl = round(price, 2)
        latest_close_date = observed.isoformat()
        return_since_ipo_pct = round((price * split_factor / ipo_price - 1) * 100, 2)

        total_volume = sum(volume for _, _, volume in closes)
        offer_size = _number(offer.get("offer_size_mn_tl"))
        if total_volume > 0 and offer_size:
            floating_lots = offer_size * 1_000_000 / ipo_price
            if floating_lots > 0:
                latest_turnover_pct = round(total_volume / floating_lots * 100, 2)

    return {
        "ticker": ticker,
        "company": offer.get("company"),
        "listing_date": actual_listing_date.isoformat(),
        "return_5d_pct": round((fifth_close / ipo_price - 1) * 100, 2),
        "return_20d_pct": round((first_twenty[-1][1] / ipo_price - 1) * 100, 2) if len(first_twenty) >= 20 else None,
        "max_drawdown_20d_pct": round(maximum_drawdown, 2) if len(first_twenty) >= 10 else None,
        "max_limit_up_streak": longest_streak,
        "max_limit_down_streak": longest_down_streak,
        "opening_limit_down_streak": opening_down_streak,
        "is_streak_active": not initial_streak_broken,
        "trough_close_tl": round(trough_close, 2),
        "trough_return_pct": trough_return_pct,
        "trough_date": trough_date.isoformat(),
        "trough_session": trough_session,
        "first_sellable_return_pct": first_sellable_return_pct,
        "first_sellable_session": first_sellable_session,
        "still_limit_down_locked": still_limit_down_locked,
        "limit_down_method": (
            "Tabanda alıcı olmadığı için satış yapılamaz; ilk çıkış fiyatı, açılıştaki "
            "taban serisinin kırıldığı seansın kapanışıdır."
        ),
        "max_return_15d_pct": max_return_15d_pct,
        "max_return_20d_pct": max_return_20d_pct,
        "peak_close_tl": round(peak_close, 2),
        "peak_return_pct": peak_return_pct,
        "peak_date": peak_date.isoformat(),
        "peak_session": peak_session,
        "peak_is_latest": peak_is_latest,
        "limit_up_method": (
            "İlk 20 seansta önceki kapanışa göre en az %9,5 artan kapanışların en uzun "
            "ardışık serisi; resmî tavan sayısı değildir."
        ),
        "peak_method": (
            "Zirve getirisi = eldeki en yüksek günlük kapanışın halka arz fiyatına oranı. "
            "Tavan serisinden türetilmez."
        ),
        "latest_close_tl": latest_close_tl,
        "latest_close_date": latest_close_date,
        "return_since_ipo_pct": return_since_ipo_pct,
        "latest_turnover_pct": latest_turnover_pct,
        "split_factor_since_listing": round(split_factor, 6) if split_history else None,
        "split_events": [
            {"date": when.isoformat(), "ratio": round(ratio, 6)} for when, ratio in split_history
        ],
        "split_method": (
            "Yahoo bedelsiz sonrası geçmiş fiyatları yeni pay adedine böler. İlk seans "
            "getirileri o günkü nominal fiyatlardan, arzdan bugüne getirisi ise artan lot "
            "adedi dikkate alınarak hesaplanır."
        ) if split_history else None,
        "broker": offer.get("broker"),
        "broker_key": canonical_broker(offer.get("broker")),
        "sector": offer.get("sector"),
        "source_url": offer.get("detail_url"),
        "price_source_url": f"https://finance.yahoo.com/quote/{ticker}.IS/history",
        "ipo_price_tl": ipo_price,
        "fifth_session_date": fifth_date.isoformat(),
        "observation_count": len(first_twenty),
        "is_partial_5d": len(closes) < 5,
        "participant_count": offer.get("participant_count"),
        "realised_lot_per_person": offer.get("realised_lot_per_person"),
        "realised_tl_per_person": offer.get("realised_tl_per_person"),
        # Tarihsel puan, gelecekteki sonuçlarla kirlenmeden yeniden hesaplanabilsin
        # diye halka arz anındaki kaynaklanmış alanları saklarız.
        "offer_snapshot": {key: offer.get(key) for key in SNAPSHOT_KEYS},
    }, None
