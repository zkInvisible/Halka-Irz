"""HalkArz takvimi, arşivi ve detay sayfalarından kaynak bağlantılı ham veri toplar.

HalkArz burada takvim, arz koşulları, gerçekleşen dağıtım ve doküman
bağlantılarını keşfetmek için kullanılır. Finansal oranlar için kaynaklı ek veri
girilmediğinde uygulama bilinmeyen değeri puanla doldurmaz.

Eski sürüme göre kapatılan boşluklar:

* **Taslak arzlar** (SPK sürecinde, tarihi belli olmayan ~200 şirket) hiç
  okunmuyordu; artık ayrı bir liste olarak toplanıyor.
* **Gerçekleşen dağıtım tablosu** (`table.as-table`) hiç okunmuyordu. Katılımcı
  sayısı, metin içinde rastgele sayı arayan bir düzenli ifadeyle tahmin
  ediliyor ve çoğunlukla boş kalıyordu.
* **"Dağıtılan Pay Miktarı"** bloğu, başlık adı tam eşleşmediği için hiçbir
  zaman bulunamıyordu. Bu blok kişi başına düşen lot ve TL tutarını doğrudan
  veriyor; arayüz bunu tahmin etmeye çalışıyordu.
* **Ek pay (green shoe), fiili dolaşım, satış yöntemi, fiyat aralığı** alanları
  okunmuyordu.
* Yıl arşivi paginasyonu tek bir base64 script etiketine bağlıydı; artık URL
  paginasyonu birincil, WordPress REST API tamamlayıcı kaynak.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date
from typing import Any, Iterable

from bs4 import BeautifulSoup

from .http_client import BotProtectionError, HttpClient, SourceError, shared_client

__all__ = [
    "BotProtectionError",
    "CALENDAR_URL",
    "HalkarzSource",
    "SourceError",
    "infer_sector",
]

CALENDAR_URL = "https://halkarz.com/"
REST_BASE = "https://halkarz.com/wp-json/wp/v2"
DRAFT_CATEGORY_SLUG = "taslak"

MONTHS = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}

SECTOR_KEYWORDS = {
    "Enerji": ("enerji", "elektrik", "güneş", "solar", "rüzgar", "petrol", "doğalgaz", "akaryakıt", "ges", "res"),
    "Gayrimenkul": ("gayrimenkul", "gyo", "emlak"),
    "Finans": ("finans", "yatırım", "portföy", "sigorta", "banka", "faktoring", "menkul", "girişim sermayesi"),
    "Teknoloji": ("teknoloji", "yazılım", "bilişim", "elektronik", "dijital", "telekom"),
    "Sanayi": ("çelik", "demir", "metal", "makina", "makine", "kimya", "sanayi", "otomotiv", "jant", "kablo", "döküm", "plastik"),
    "İnşaat ve yapı": ("beton", "inşaat", "seramik", "yapı", "çimento", "altyapı", "taahhüt"),
    "Sağlık": ("sağlık", "hastane", "ilaç", "medikal", "tıbbi"),
    "Tüketim": ("gıda", "giyim", "tekstil", "turizm", "saat", "perakende", "mobilya", "kozmetik", "içecek"),
    "Madencilik": ("maden", "mermer", "krom", "altın"),
    "Ulaştırma ve lojistik": ("lojistik", "taşımacılık", "kargo", "denizcilik", "havacılık", "liman"),
    "Tarım": ("tarım", "hayvancılık", "su ürünleri", "tohum", "gübre"),
}

# İzahname/dağıtım tablosundaki yatırımcı grubu adları.
RETAIL_GROUP_MARKERS = ("yurt içi bireysel", "yurtiçi bireysel", "bireysel yatırımcı")
TOTAL_ROW_MARKERS = ("toplam", "genel toplam")


# ── küçük yardımcılar ──────────────────────────────────────────────────────
def _compact(value: str) -> str:
    return " ".join(value.split())


_NUMBER_PATTERN = re.compile(r"\d[\d.]*(?:,\d+)?")


def _to_number(raw: str) -> float | None:
    """Ham sayı metnini float'a çevirir.

    Kaynak iki gösterimi karışık kullanıyor: Türkçe ``728.823`` (binlik ayracı)
    ve İngilizce ``1.1 Milyon`` / ``%20.02`` (ondalık ayracı). Türkçe binlik
    ayracı her zaman üçer haneli grup ürettiğinden, son noktadan sonraki grup
    üç haneden kısaysa bunu ondalık kabul ediyoruz. Eski sürüm bu ayrımı
    yapmadığı için ``%20.02`` fiili dolaşım oranını 2002 olarak okuyordu.
    """
    raw = raw.strip().strip(".")
    if not raw:
        return None
    if "," in raw:
        candidate = raw.replace(".", "").replace(",", ".")
    elif "." in raw:
        head, _, tail = raw.rpartition(".")
        if len(tail) == 3 and all(len(group) == 3 for group in head.split(".")[1:]):
            candidate = raw.replace(".", "")
        else:
            candidate = head.replace(".", "") + "." + tail
    else:
        candidate = raw
    try:
        return float(candidate)
    except ValueError:
        return None


def _parse_turkish_number(value: str | None) -> float | None:
    """Metindeki ilk sayıyı float'a çevirir."""
    if not value:
        return None
    match = _NUMBER_PATTERN.search(value.replace(" ", " "))
    return _to_number(match.group(0)) if match else None


def _parse_all_numbers(value: str | None) -> list[float]:
    if not value:
        return []
    numbers: list[float] = []
    for raw in _NUMBER_PATTERN.findall(value.replace(" ", " ")):
        number = _to_number(raw)
        if number is not None:
            numbers.append(number)
    return numbers


def _apply_magnitude(number: float | None, text: str) -> float | None:
    """`3,3 Milyar` / `450 Bin` gibi çarpanları uygular."""
    if number is None:
        return None
    lowered = text.lower()
    if "milyar" in lowered:
        return number * 1_000_000_000
    if "milyon" in lowered:
        return number * 1_000_000
    if re.search(r"\bbin\b", lowered):
        return number * 1_000
    return number


def _parse_money_million(value: str | None) -> float | None:
    """Para tutarını **milyon TL** cinsinden döndürür."""
    if not value:
        return None
    number = _parse_turkish_number(value)
    if number is None:
        return None
    # `_apply_magnitude` tutarı TL'ye çevirir; birim yazılmamışsa zaten TL kabul
    # edilir. Sonuç her durumda milyon TL cinsine indirilir.
    absolute = _apply_magnitude(number, value)
    return None if absolute is None else absolute / 1_000_000


def _parse_date_range(value: str | None) -> tuple[str | None, str | None]:
    """`29-30-31 Temmuz 2026` gibi metinden ilk ve son günü çıkarır."""
    if not value:
        return None, None
    lowered = value.lower()
    years = [int(item) for item in re.findall(r"(20\d{2})", lowered)]
    if not years:
        return None, None
    month_pattern = "|".join(MONTHS)
    month_matches = list(re.finditer(month_pattern, lowered))
    if not month_matches:
        return None, None

    parsed_dates: list[date] = []
    previous_end = 0
    for index, match in enumerate(month_matches):
        segment = lowered[previous_end:match.start()]
        days = [int(item) for item in re.findall(r"\b(\d{1,2})\b", segment) if 1 <= int(item) <= 31]
        month = MONTHS[match.group(0)]
        # Ay birden fazlaysa (yıl sonu geçişi) o aya en yakın yılı seç.
        tail = lowered[match.end():]
        tail_year = re.search(r"(20\d{2})", tail)
        year = int(tail_year.group(1)) if tail_year else years[min(index, len(years) - 1)]
        for day in days:
            try:
                parsed_dates.append(date(year, month, day))
            except ValueError:
                continue
        previous_end = match.end()

    if not parsed_dates:
        return None, None
    return min(parsed_dates).isoformat(), max(parsed_dates).isoformat()


def _normalise_period(value: str | None) -> str | None:
    """`2026/3` gibi çeyrek dönemlerini ISO tarihine dönüştürür."""
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        return value
    match = re.fullmatch(r"(20\d{2})\s*[/.-]\s*(3|6|9|12)", value)
    if match:
        year, month = (int(item) for item in match.groups())
        return date(year, month, {3: 31, 6: 30, 9: 30, 12: 31}[month]).isoformat()
    if re.fullmatch(r"20\d{2}", value):
        return date(int(value), 12, 31).isoformat()
    return None


def infer_sector(company: str | None) -> str | None:
    """Şirket adından geniş bir emsal grubu çıkarır.

    Bu bir sektör sınıflandırma kaynağı değildir. Aynı sınıftan üç gözlem yoksa
    puan motoru zaten genel yakın dönem örneklemine geri döner.
    """
    if not company:
        return None
    lowered = company.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return sector
    return None


def _strip_heading(heading: str, body: str) -> str:
    """`li` metninden başlığın kendisini ve süs karakterlerini ayıklar."""
    cleaned = body
    if cleaned.startswith(heading):
        cleaned = cleaned[len(heading):]
    return cleaned.strip(" -–—:*～~.").strip()


# ── kaynak ────────────────────────────────────────────────────────────────
class HalkarzSource:
    """HalkArz'ı arayüz kaynağı olarak kullanır; metrikler resmi belgeyle doğrulanmalıdır."""

    def __init__(self, client: HttpClient | None = None):
        self.client = client or shared_client

    # ── liste keşfi ────────────────────────────────────────────────────────
    @staticmethod
    def _entry_from_article(article: Any, *, require_date: bool) -> dict[str, Any] | None:
        company_link = article.select_one(".il-halka-arz-sirket a[href]")
        if not company_link:
            return None
        ticker_node = article.select_one(".il-bist-kod")
        time_tag = article.select_one(".il-halka-arz-tarihi time")

        ticker = _compact(ticker_node.get_text()) if ticker_node else ""
        company = _compact(company_link.get_text())
        detail_url = company_link["href"]
        date_text = _compact(time_tag.get("datetime") or time_tag.get_text()) if time_tag else ""
        start_date, end_date = _parse_date_range(date_text)

        if require_date and not start_date:
            return None
        if not company:
            return None

        slug = detail_url.rstrip("/").rsplit("/", 1)[-1]
        logo = article.select_one("img.slogo")
        return {
            "ticker": ticker or None,
            "slug": slug,
            "company": company,
            "calendar_date_text": date_text,
            "start_date": start_date,
            "end_date": end_date,
            "detail_url": detail_url,
            "logo_url": logo.get("src") if logo else None,
            "calendar_source": CALENDAR_URL,
            "sector": infer_sector(company),
            "sector_source": "Şirket adından geniş sınıf eşlemesi (doğrulanmalı)",
        }

    @classmethod
    def _entries_from_soup(cls, soup: BeautifulSoup, *, require_date: bool) -> list[dict[str, Any]]:
        entries = []
        for article in soup.select("article.index-list"):
            entry = cls._entry_from_article(article, require_date=require_date)
            if entry:
                entries.append(entry)
        return entries

    def fetch_calendar(self) -> dict[str, list[dict[str, Any]]]:
        """Ana sayfayı bir kez okuyup tarihli takvimi ve taslak listesini birlikte döndürür.

        Ana sayfa hem yakın dönem takvimini hem de "Taslak Arzlar" bölümünü
        içerir. Tek istekte ikisini de almak, kaynağa gereksiz yük binmesini
        önler.
        """
        soup = self.client.get_soup(CALENDAR_URL)
        scheduled: list[dict[str, Any]] = []
        drafts: list[dict[str, Any]] = []
        for article in soup.select("article.index-list"):
            entry = self._entry_from_article(article, require_date=False)
            if not entry:
                continue
            if entry["start_date"]:
                scheduled.append(entry)
            else:
                entry["pipeline_status"] = "draft"
                drafts.append(entry)

        scheduled = self._deduplicate(scheduled)
        scheduled.sort(key=lambda item: item["start_date"], reverse=True)
        return {"scheduled": scheduled, "drafts": self._deduplicate(drafts)}

    @staticmethod
    def _deduplicate(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for entry in entries:
            unique.setdefault(entry["detail_url"], entry)
        return list(unique.values())

    # ── yıl arşivi ─────────────────────────────────────────────────────────
    def _archive_page(self, year: int, page: int) -> list[dict[str, Any]]:
        suffix = "" if page == 1 else f"page/{page}/"
        url = f"https://halkarz.com/k/halka-arz/{year}/{suffix}"
        try:
            soup = self.client.get_soup(url)
        except BotProtectionError:
            raise
        except SourceError:
            # 404 = sayfa yok; sayfalama burada biter.
            return []
        return self._entries_from_soup(soup, require_date=False)

    def _archive_via_loadmore(self, year: int) -> list[dict[str, Any]]:
        """WordPress "daha fazla yükle" AJAX'ı üzerinden kalan sayfaları alır."""
        try:
            soup = self.client.get_soup(f"https://halkarz.com/k/halka-arz/{year}/")
        except SourceError:
            return []
        config_tag = soup.select_one("#my_loadmore-js-extra[src*='base64,']")
        if not config_tag:
            return []
        try:
            encoded = config_tag["src"].split("base64,", 1)[1]
            config_text = base64.b64decode(encoded).decode("utf-8")
            payload = json.loads(config_text.split("=", 1)[1].rstrip(";"))
            current_page = int(payload["current_page"])
            max_page = int(payload["max_page"])
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError, IndexError):
            return []

        entries: list[dict[str, Any]] = []
        while current_page < max_page:
            try:
                loaded = self.client.post_soup(
                    payload["ajaxurl"],
                    {"action": "loadmore", "query": payload["posts"], "page": current_page},
                )
            except SourceError:
                break
            page_entries = self._entries_from_soup(loaded, require_date=False)
            if not page_entries:
                break
            entries.extend(page_entries)
            current_page += 1
        return entries

    def _rest_category_id(self, slug: str) -> tuple[int | None, int]:
        """Kategori kimliğini ve resmi kayıt sayısını REST API'den okur."""
        try:
            payload = self.client.get_json(f"{REST_BASE}/categories", {"slug": slug, "per_page": 1})
        except (SourceError, ValueError):
            return None, 0
        if not isinstance(payload, list) or not payload:
            return None, 0
        return payload[0].get("id"), int(payload[0].get("count") or 0)

    def _rest_category_posts(self, category_id: int) -> list[dict[str, Any]]:
        """Kategorideki tüm yazıları REST API üzerinden sayfalayarak alır."""
        collected: list[dict[str, Any]] = []
        for page in range(1, 12):
            try:
                payload = self.client.get_json(
                    f"{REST_BASE}/posts",
                    {
                        "categories": category_id,
                        "per_page": 100,
                        "page": page,
                        "_fields": "id,slug,link,title,date,modified",
                    },
                )
            except (SourceError, ValueError):
                break
            if not isinstance(payload, list) or not payload:
                break
            collected.extend(payload)
            if len(payload) < 100:
                break
        return collected

    @staticmethod
    def _entry_from_rest_post(post: dict[str, Any]) -> dict[str, Any] | None:
        link = post.get("link")
        if not link:
            return None
        title = _compact(BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text())
        if not title:
            return None
        return {
            "ticker": None,
            "slug": post.get("slug") or link.rstrip("/").rsplit("/", 1)[-1],
            "company": title,
            "calendar_date_text": "",
            "start_date": None,
            "end_date": None,
            "detail_url": link,
            "logo_url": None,
            "calendar_source": f"{REST_BASE}/posts",
            "sector": infer_sector(title),
            "sector_source": "Şirket adından geniş sınıf eşlemesi (doğrulanmalı)",
        }

    def fetch_archive_entries(self, years: Iterable[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Verilen yıllardaki tüm arşiv kayıtlarını, eksiksizlik kontrolüyle döndürür.

        Önce HTML sayfalaması denenir (BIST kodunu da verdiği için tercih
        edilir). REST API'nin bildirdiği resmi kayıt sayısına ulaşılamazsa
        eksik kalanlar REST listesinden tamamlanır.
        """
        entries: list[dict[str, Any]] = []
        coverage: dict[str, Any] = {}

        for year in years:
            year_entries: list[dict[str, Any]] = []
            for page in range(1, 12):
                page_entries = self._archive_page(year, page)
                if not page_entries:
                    break
                year_entries.extend(page_entries)
                if len(page_entries) < 20:
                    break

            expected_id, expected_count = self._rest_category_id(str(year))
            year_entries = self._deduplicate(year_entries)

            if expected_count and len(year_entries) < expected_count:
                # HTML sayfalaması eksik kaldı: AJAX ve REST ile tamamla.
                year_entries = self._deduplicate(year_entries + self._archive_via_loadmore(year))
            if expected_id and expected_count and len(year_entries) < expected_count:
                known = {entry["detail_url"].rstrip("/") for entry in year_entries}
                for post in self._rest_category_posts(expected_id):
                    entry = self._entry_from_rest_post(post)
                    if entry and entry["detail_url"].rstrip("/") not in known:
                        year_entries.append(entry)

            coverage[str(year)] = {
                "expected": expected_count or None,
                "collected": len(year_entries),
                "complete": bool(expected_count) and len(year_entries) >= expected_count,
            }
            entries.extend(year_entries)

        return self._deduplicate(entries), coverage

    def draft_category_count(self) -> int | None:
        """Kaynağın bildirdiği resmi taslak sayısı (eksiksizlik kontrolü için)."""
        _, count = self._rest_category_id(DRAFT_CATEGORY_SLUG)
        return count or None

    # ── detay sayfası ──────────────────────────────────────────────────────
    def fetch_detail(self, entry: dict[str, Any]) -> dict[str, Any]:
        soup = self.client.get_soup(entry["detail_url"])
        return self.parse_detail(entry, soup)

    def parse_detail(self, entry: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
        terms = self._parse_terms(soup)
        summary = self._parse_summary_blocks(soup)
        allocation = self._parse_allocation_table(soup)
        distribution = self._parse_distributed_shares(summary)
        finance = self._parse_finance_table(soup)
        documents = self._extract_documents(soup)

        offer_date_text = terms.get("Halka Arz Tarihi") or entry.get("calendar_date_text", "")
        start_date, end_date = _parse_date_range(offer_date_text)
        listing_date, _ = _parse_date_range(terms.get("Bist İlk İşlem Tarihi"))

        price_low, price_high = self._parse_price(terms.get("Halka Arz Fiyatı/Aralığı"))
        offered_lots = _parse_turkish_number(terms.get("Pay"))
        extra_lots = _parse_turkish_number(terms.get("Ek Pay"))
        primary_lots, secondary_lots = self._parse_offer_structure(summary.get("Halka Arz Şekli", ""))
        retail_allocation_pct = self._parse_retail_allocation_pct(summary.get("Tahsisat Grupları", ""))

        # Gerçekleşen dağıtım tablosu, tahsisat metnindeki plandan daha
        # güvenilirdir; varsa bireysel oranı oradan alırız.
        if allocation.get("retail_share_pct") is not None:
            retail_allocation_pct = allocation["retail_share_pct"]

        parsed: dict[str, Any] = {
            **entry,
            "ticker": terms.get("Bist Kodu") or entry.get("ticker"),
            "calendar_date_text": offer_date_text or entry.get("calendar_date_text") or "Tarih bulunamadı",
            "start_date": start_date or entry.get("start_date"),
            "end_date": end_date or entry.get("end_date"),
            "listing_date": listing_date,
            "ipo_price_tl": price_low,
            "ipo_price_high_tl": price_high,
            "is_price_range": price_high is not None and price_low is not None and price_high > price_low,
            "distribution_method": terms.get("Dağıtım Yöntemi"),
            "sale_method": summary.get("Halka Arz Satış Yöntemi") or None,
            "broker": terms.get("Aracı Kurum"),
            "is_consortium": bool(terms.get("_broker_is_consortium")),
            "market": terms.get("Pazar"),
            "offered_lots": offered_lots,
            "extra_lots": extra_lots,
            "primary_lots": primary_lots,
            "secondary_lots": secondary_lots,
            "actual_float_lots": _parse_turkish_number(terms.get("Fiili Dolaşımdaki Pay")),
            "actual_float_pct": _parse_turkish_number(terms.get("Fiili Dolaşımdaki Pay Oranı (%)")),
            "retail_allocation_pct": retail_allocation_pct,
            "float_pct": _parse_turkish_number(summary.get("Halka Açıklık")),
            "stated_discount_pct": _parse_turkish_number(summary.get("Halka Arz İskontosu")),
            "use_of_proceeds": summary.get("Fonun Kullanım Yeri", ""),
            "price_stabilization": summary.get("Fiyat İstikrarı", ""),
            "lockup": summary.get("Satmama Taahhüdü", ""),
            "daily_buy_commitment": summary.get("Günlük Alım Emri Taahhüdü") or None,
            "offer_size_mn_tl": _parse_money_million(summary.get("Halka Arz Büyüklüğü")),
            "allocation_groups": allocation.get("groups", []),
            "participant_count": allocation.get("retail_participant_count") or distribution.get("participant_count"),
            "total_participant_count": allocation.get("total_participant_count"),
            "retail_lots_distributed": allocation.get("retail_lots"),
            "realised_lot_per_person": distribution.get("lot_per_person"),
            "realised_tl_per_person": distribution.get("tl_per_person"),
            "projected_distribution": distribution.get("projections", []),
            "major_shareholders": self._parse_major_shareholders(soup),
            "financials": finance,
            "documents": documents,
            "source_fetched_from": entry["detail_url"],
        }

        # Kullanıcı talebi: Max lot bulunamadıysa dağıtım tablosundan ortalama hesapla.
        if parsed["realised_lot_per_person"] is None:
            lots = allocation.get("retail_lots")
            people = allocation.get("retail_participant_count")
            if lots and people:
                parsed["realised_lot_per_person"] = round(lots / people, 2)
                parsed["is_lot_average"] = True
                if price_low:
                    parsed["realised_tl_per_person"] = round(lots / people * price_low, 2)
        else:
            parsed["is_lot_average"] = False

        parsed["retail_lot_pool"] = self._retail_lot_pool(
            projections=distribution.get("projections", []),
            realised_lots=allocation.get("retail_lots"),
            offered_lots=offered_lots,
            retail_pct=retail_allocation_pct,
        )

        if parsed["offer_size_mn_tl"] is None and offered_lots and price_low:
            parsed["offer_size_mn_tl"] = round(offered_lots * price_low / 1_000_000, 2)

        parsed["sector"] = parsed.get("sector") or infer_sector(parsed.get("company"))
        return parsed

    # ── detay parçalayıcıları ──────────────────────────────────────────────
    @staticmethod
    def _parse_terms(soup: BeautifulSoup) -> dict[str, str]:
        """`table.sp-table` içindeki `Etiket : Değer` satırlarını okur."""
        terms: dict[str, str] = {}
        rows = soup.select("table.sp-table tr") or soup.select("table tr")
        for row in rows:
            cells = [_compact(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            label = cells[0]
            value = " ".join(part for part in cells[1:] if part).strip()
            if not value:
                continue
            if label.startswith("Aracı Kurum"):
                terms["Aracı Kurum"] = value
                if "konsorsiyum" in label.lower():
                    terms["_broker_is_consortium"] = "1"
                continue
            if label.endswith(":"):
                terms[label.rstrip(":").strip()] = value
        return terms

    @staticmethod
    def _parse_summary_blocks(soup: BeautifulSoup) -> dict[str, str]:
        """`h5` başlıklı özet bloklarını başlıktan arındırarak toplar."""
        summary: dict[str, str] = {}
        for heading in soup.find_all("h5"):
            container = heading.find_parent("li") or heading.parent
            if container is None:
                continue
            title = _compact(heading.get_text())
            body = _strip_heading(title, _compact(container.get_text(" ", strip=True)))
            if not title:
                continue
            # Aynı başlık iki kez geçerse (boş + dolu) dolu olanı koru.
            if summary.get(title) and not body:
                continue
            summary[title] = body
        return summary

    @staticmethod
    def _find_block(summary: dict[str, str], *prefixes: str) -> str:
        """Başlık adı yıldız/parantez ile değişebildiği için ön ek eşleşmesi yapar."""
        for prefix in prefixes:
            lowered_prefix = prefix.lower()
            for title, body in summary.items():
                if title.lower().startswith(lowered_prefix) and body:
                    return body
        return ""

    @staticmethod
    def _parse_price(value: str | None) -> tuple[float | None, float | None]:
        """Tek fiyat veya `12,00 - 14,00 TL` aralığını döndürür."""
        if not value:
            return None, None
        numbers = _parse_all_numbers(value)
        if not numbers:
            return None, None
        if len(numbers) == 1:
            return numbers[0], None
        return min(numbers), max(numbers)

    @staticmethod
    def _parse_offer_structure(text: str) -> tuple[float | None, float | None]:
        """`Sermaye Artırımı` ve `Ortak Satışı` lotlarını ayırır."""
        if not text:
            return None, None
        primary = None
        primary_match = re.search(r"Sermaye Artırımı\s*:?\s*([\d.,]+)", text, re.IGNORECASE)
        if primary_match:
            primary = _parse_turkish_number(primary_match.group(1))
        secondary_values = [
            _parse_turkish_number(match) or 0.0
            for match in re.findall(r"Ortak Satışı\s*:?\s*([\d.,]+)", text, re.IGNORECASE)
        ]
        secondary = sum(secondary_values) if secondary_values else None
        return primary, secondary

    @staticmethod
    def _parse_retail_allocation_pct(text: str) -> float | None:
        """Tahsisat metninden yurt içi bireysel yüzdesini alır."""
        if not text:
            return None
        match = re.search(r"\(%\s*([\d.,]+)\s*\)\s*Yurt\s*İçi\s*Bireysel", text, re.IGNORECASE)
        if match:
            return _parse_turkish_number(match.group(1))
        match = re.search(r"Yurt\s*İçi\s*Bireysel[^%]{0,40}%\s*([\d.,]+)", text, re.IGNORECASE)
        return _parse_turkish_number(match.group(1)) if match else None

    @classmethod
    def _parse_allocation_table(cls, soup: BeautifulSoup) -> dict[str, Any]:
        """Gerçekleşen dağıtım tablosunu (`table.as-table`) okur.

        Tablo başlığı iki satıra yayılır::

            Yatırımcı Grubu | Dağıtım
                            | Kişi | Lot | Oran
            Yurt İçi Bireysel | 728.823 | 33.820.000 | %38
            Toplam            | 729.560 | 89.000.000 | %100

        Bu tablo, halka arzın gerçekleşen sonucudur: kaç kişiye kaç lot
        dağıtıldığını doğrudan verir.
        """
        table = soup.select_one("table.as-table")
        if table is None:
            return {}

        groups: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        for row in table.find_all("tr"):
            cells = [_compact(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) < 4:
                continue
            label = cells[0]
            lowered = label.lower()
            if not label or lowered.startswith("yatırımcı") or lowered in ("kişi", "dağıtım"):
                continue
            if label.startswith("*"):
                continue
            people = _parse_turkish_number(cells[1])
            lots = _parse_turkish_number(cells[2])
            share = _parse_turkish_number(cells[3])
            if people is None and lots is None:
                continue

            if any(marker in lowered for marker in TOTAL_ROW_MARKERS):
                result["total_participant_count"] = people
                result["total_lots"] = lots
                continue

            groups.append({"group": label, "people": people, "lots": lots, "share_pct": share})
            if any(marker in lowered for marker in RETAIL_GROUP_MARKERS):
                result["retail_participant_count"] = people
                result["retail_lots"] = lots
                result["retail_share_pct"] = share

        result["groups"] = groups
        return result

    @classmethod
    def _parse_distributed_shares(cls, summary: dict[str, str]) -> dict[str, Any]:
        """`Dağıtılan / Dağıtılacak Pay Miktarı` bloklarını çözer.

        Tamamlanmış arz::

            728.823 katılım ~ 45 Lot (3150 TL)

        Henüz tamamlanmamış arz (senaryo projeksiyonu)::

            150 Bin katılım ~ 266 Lot (12794 TL). - 250 Bin katılım ~ 160 Lot ...
        """
        pattern = re.compile(
            r"(\d[\d.]*(?:,\d+)?)\s*(bin|milyon)?\s*(?:katılım\w*)?\s*[~≈\-]\s*"
            r"(\d[\d.]*(?:,\d+)?)\s*Lot\s*\(\s*(\d[\d.]*(?:,\d+)?)\s*TL\s*\)",
            re.IGNORECASE,
        )

        realised_text = cls._find_block(summary, "Dağıtılan Pay Miktarı")
        projected_text = cls._find_block(summary, "Dağıtılacak Pay Miktarı")

        result: dict[str, Any] = {"projections": []}

        for match in pattern.finditer(realised_text):
            people = _apply_magnitude(_parse_turkish_number(match.group(1)), match.group(2) or "")
            result["participant_count"] = people
            result["lot_per_person"] = _parse_turkish_number(match.group(3))
            result["tl_per_person"] = _parse_turkish_number(match.group(4))
            break  # Gerçekleşen tek satırdır.

        for match in pattern.finditer(projected_text):
            people = _apply_magnitude(_parse_turkish_number(match.group(1)), match.group(2) or "")
            result["projections"].append({
                "participants": people,
                "lot_per_person": _parse_turkish_number(match.group(3)),
                "tl_per_person": _parse_turkish_number(match.group(4)),
            })
        result["projections"].sort(key=lambda item: item["participants"] or 0)
        return result

    @staticmethod
    def _retail_lot_pool(
        *,
        projections: list[dict[str, Any]],
        realised_lots: float | None,
        offered_lots: float | None,
        retail_pct: float | None,
    ) -> float | None:
        """Bireysel yatırımcıya ayrılan toplam lot havuzunu belirler.

        Bu havuz bilinince kişi başına düşen lot herhangi bir katılım sayısı
        için hesaplanabilir. Kaynak yalnızca kendi seçtiği katılım basamaklarını
        yayımlıyor (150 bin, 250 bin, 1,1 milyon…); havuzu geri hesaplayarak
        gerçekçi bir banda göre senaryo üretebiliyoruz.
        """
        if realised_lots:
            return realised_lots
        # Yayımlanan senaryolardan geri hesap: katılım × kişi başı lot ≈ havuz.
        derived = sorted(
            (item["participants"] or 0) * (item["lot_per_person"] or 0)
            for item in projections
            if item.get("participants") and item.get("lot_per_person")
        )
        if derived:
            return derived[len(derived) // 2]
        if offered_lots and retail_pct:
            return offered_lots * retail_pct / 100
        return None

    @staticmethod
    def _parse_finance_table(soup: BeautifulSoup) -> dict[str, Any]:
        """İzahname özeti finansal tablosunu okur (en güncel dönem)."""
        for table in soup.find_all("table"):
            header = _compact(table.get_text(" ", strip=True)).lower()
            if "hasılat" not in header:
                continue
            rows = [
                [_compact(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
                for row in table.find_all("tr")
            ]
            rows = [row for row in rows if len(row) >= 2]
            if len(rows) < 2:
                continue
            periods = rows[0][1:]
            values: dict[str, list[float | None]] = {}
            for row in rows[1:]:
                label = row[0].lstrip("- ").strip().lower()
                values[label] = [_parse_money_million(cell) for cell in row[1:]]

            def latest(name: str) -> float | None:
                series = values.get(name) or []
                return series[0] if series else None

            revenue = latest("hasılat")
            gross_profit = latest("brüt kâr") or latest("brüt kar")
            net_profit = latest("net kâr") or latest("net kar") or latest("dönem kârı")
            return {
                "as_of": _normalise_period(periods[0]) if periods else None,
                "periods": periods,
                "revenue_mn_tl": revenue,
                "gross_profit_mn_tl": gross_profit,
                "net_profit_mn_tl": net_profit,
                "gross_margin": (gross_profit / revenue) if revenue and gross_profit is not None else None,
                "net_margin": (net_profit / revenue) if revenue and net_profit is not None else None,
                "revenue_series_mn_tl": values.get("hasılat"),
                "source": "HalkArz detay sayfasındaki izahname özeti; resmi belge ile doğrulanmalı.",
            }
        return {}

    @staticmethod
    def _parse_major_shareholders(soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Payların %5'inden fazlasını alan yatırımcıları listeler."""
        table = soup.select_one("table.t-result")
        if table is None:
            return []
        rows = [
            [_compact(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            for row in table.find_all("tr")
        ]
        holders = []
        for row in rows[1:]:
            if len(row) < 3 or not row[0]:
                continue
            holders.append({
                "name": row[0],
                "share_pct": _parse_turkish_number(row[1]),
                "nominal_tl": _parse_turkish_number(row[2]),
                "activity": row[3] if len(row) > 3 else None,
                "country": row[4] if len(row) > 4 else None,
            })
        return holders

    @staticmethod
    def _extract_documents(soup: BeautifulSoup) -> list[dict[str, str]]:
        documents: list[dict[str, str]] = []
        keywords = (
            "izahname", "fiyat tespit", "fon kullanım", "denetim", "tasarruf sahip",
            "sirküler", "spk bülten", "değerleme",
        )
        for link in soup.find_all("a", href=True):
            name = _compact(link.get_text(" ", strip=True))
            href = link["href"]
            if not name or not href.startswith("http"):
                continue
            if not any(keyword in name.lower() for keyword in keywords):
                continue
            documents.append({
                "name": name,
                "url": href,
                "tier": "official" if ("kap.org.tr" in href or "spk.gov.tr" in href) else "issuer",
            })
        unique = {(item["name"], item["url"]): item for item in documents}
        return list(unique.values())
