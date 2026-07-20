"""HalkArz takvimi ve detay sayfalarından kaynak bağlantılı ham veri toplar.

HalkArz burada yalnızca takvim, arz koşulları ve doküman bağlantılarını keşfetmek
için kullanılır. Finansal oranlar için kaynaklı ek veri girilmediğinde uygulama
bilinmeyen değeri puanla doldurmaz.
"""

from __future__ import annotations

import re
import base64
import json
from datetime import date
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


CALENDAR_URL = "https://halkarz.com/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
MONTHS = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}

SECTOR_KEYWORDS = {
    "Enerji": ("enerji", "elektrik", "güneş", "rüzgar", "petrol", "doğalgaz"),
    "Sanayi": ("çelik", "metal", "makina", "makine", "kimya", "sanayi", "otomotiv"),
    "İnşaat ve yapı": ("beton", "inşaat", "seramik", "yapı", "çimento"),
    "Tüketim": ("gıda", "giyim", "ilaç", "turizm", "saat", "perakende"),
    "Teknoloji": ("teknoloji", "yazılım", "bilişim", "elektronik"),
    "Finans": ("finans", "yatırım", "portföy", "sigorta"),
}


class SourceError(RuntimeError):
    """Kaynak sayfasına erişilemediğinde fırlatılır."""


def _compact(value: str) -> str:
    return " ".join(value.split())


def _parse_turkish_number(value: str) -> float | None:
    match = re.search(r"([\d.]+(?:,\d+)?)", value.replace("\u00a0", " "))
    if not match:
        return None
    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_money_million(value: str) -> float | None:
    number = _parse_turkish_number(value)
    if number is None:
        return None
    lowered = value.lower()
    if "milyar" in lowered:
        return number * 1000
    if "milyon" in lowered:
        return number
    return number / 1_000_000


def _parse_date_range(value: str) -> tuple[str | None, str | None]:
    lowered = value.lower()
    year_match = re.search(r"(20\d{2})", lowered)
    if not year_match:
        return None, None
    year = int(year_match.group(1))
    month_pattern = "|".join(MONTHS)
    month_matches = list(re.finditer(month_pattern, lowered))
    if not month_matches:
        return None, None
    parsed_dates: list[date] = []
    previous_end = 0
    for match in month_matches:
        segment = lowered[previous_end:match.start()]
        days = [int(item) for item in re.findall(r"\b(\d{1,2})\b", segment) if int(item) < 32]
        month = MONTHS[match.group(0)]
        for day in days:
            try:
                parsed_dates.append(date(year, month, day))
            except ValueError:
                continue
        previous_end = match.end()
    if not parsed_dates:
        return None, None
    try:
        return min(parsed_dates).isoformat(), max(parsed_dates).isoformat()
    except ValueError:
        return None, None


def _find_summary(summary: dict[str, str], label: str) -> str:
    return summary.get(label, "")


def _normalise_period(value: str | None) -> str | None:
    """`2026/3` gibi takvim sayfası dönemlerini ISO tarihine dönüştürür."""
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        return value
    match = re.fullmatch(r"(20\d{2})\s*[/.-]\s*(3|6|9|12)", value)
    if not match:
        return None
    year, month = (int(item) for item in match.groups())
    day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
    return date(year, month, day).isoformat()


def infer_sector(company: str) -> str | None:
    """Şirket adından yalnızca geniş sınıfta bir emsal grubu çıkarır.

    Bu bir sektör sınıflandırma kaynağı değildir. Aynı sınıftan üç gözlem yoksa
    puan motoru zaten genel yakın dönem örneklemine geri döner.
    """
    lowered = company.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return sector
    return None


class HalkarzSource:
    """Arayüz kaynağı olarak HalkArz kullanır; metrikleri resmi belgeyle doğrulamak gerekir."""

    def __init__(self, timeout_seconds: int = 20):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9"})
        self.timeout_seconds = timeout_seconds

    def _get_soup(self, url: str) -> BeautifulSoup:
        response = self.session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        if "just a moment" in soup.get_text(" ", strip=True).lower():
            raise SourceError("Kaynak bot koruması döndürdü; daha sonra tekrar deneyin.")
        return soup

    @staticmethod
    def _entry_from_article(article: Any, allow_missing_date: bool = False) -> dict[str, Any] | None:
        ticker_node = article.select_one(".il-bist-kod")
        company_link = article.select_one(".il-halka-arz-sirket a[href]")
        time_tag = article.select_one(".il-halka-arz-tarihi time")
        if not ticker_node or not company_link:
            return None
        ticker = _compact(ticker_node.get_text())
        company = _compact(company_link.get_text())
        date_text = _compact(time_tag.get_text()) if time_tag else ""
        start_date, end_date = _parse_date_range(date_text)
        if not ticker or (not start_date and not allow_missing_date):
            return None
        return {
            "ticker": ticker,
            "company": company,
            "calendar_date_text": date_text,
            "start_date": start_date,
            "end_date": end_date,
            "detail_url": company_link["href"],
            "calendar_source": CALENDAR_URL,
            "sector": infer_sector(company),
            "sector_source": "Şirket adından geniş sınıf eşlemesi (doğrulanmalı)",
        }

    def fetch_calendar_entries(self) -> list[dict[str, Any]]:
        """Ana takvimdeki tarihli kayıtları tek seferde alır.

        Ana sayfa yakın yılların arşivini de içerir. Bu sayede geçmiş emsal
        taramasında iki yıl öncesine körlemesine gitmek yerine tarihi programatik
        olarak daraltabiliyoruz.
        """
        calendar = self._get_soup(CALENDAR_URL)
        entries = self._entries_from_soup(calendar)
        deduplicated = {(item["ticker"], item["start_date"], item["detail_url"]): item for item in entries}
        return sorted(deduplicated.values(), key=lambda item: item["start_date"], reverse=True)

    @classmethod
    def _entries_from_soup(cls, soup: BeautifulSoup, allow_missing_date: bool = False) -> list[dict[str, Any]]:
        return [
            entry for article in soup.select("article.index-list")
            if (entry := cls._entry_from_article(article, allow_missing_date=allow_missing_date))
        ]

    def _category_entries(self, year: int) -> list[dict[str, Any]]:
        page_url = f"https://halkarz.com/k/halka-arz/{year}/"
        soup = self._get_soup(page_url)
        entries = self._entries_from_soup(soup, allow_missing_date=True)
        config_tag = soup.select_one("#my_loadmore-js-extra[src*='base64,']")
        if not config_tag:
            return entries
        try:
            encoded = config_tag["src"].split("base64,", 1)[1]
            config_text = base64.b64decode(encoded).decode("utf-8")
            payload = json.loads(config_text.split("=", 1)[1].rstrip(";"))
            current_page = int(payload["current_page"])
            max_page = int(payload["max_page"])
        except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError, IndexError):
            return entries
        while current_page < max_page:
            response = self.session.post(
                payload["ajaxurl"],
                data={"action": "loadmore", "query": payload["posts"], "page": current_page},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            loaded_soup = BeautifulSoup(response.text, "html.parser")
            loaded = self._entries_from_soup(loaded_soup, allow_missing_date=True)
            if not loaded:
                break
            entries.extend(loaded)
            current_page += 1
        return entries

    def fetch_upcoming(self, reference_date: date | None = None) -> list[dict[str, Any]]:
        reference_date = reference_date or date.today()
        entries = [item for item in self.fetch_calendar_entries() if date.fromisoformat(item["start_date"]) >= reference_date]
        return [self.fetch_detail(entry) for entry in sorted(entries, key=lambda item: item["start_date"])]

    def fetch_current_and_upcoming(self, reference_date: date | None = None) -> list[dict[str, Any]]:
        """Talep toplaması açık olanları ve henüz başlamamış olanları birlikte döndürür."""
        reference_date = reference_date or date.today()
        entries = []
        for item in self.fetch_calendar_entries():
            start = date.fromisoformat(item["start_date"])
            end = date.fromisoformat(item.get("end_date") or item["start_date"])
            if end >= reference_date:
                entries.append(item)
        return [self.fetch_detail(entry) for entry in sorted(entries, key=lambda item: item["start_date"])]

    def fetch_recent_candidates(self, reference_date: date, lookback_days: int) -> list[dict[str, Any]]:
        """Yakın dönem arşiv adaylarını döndürür."""
        lower_bound = date.fromordinal(reference_date.toordinal() - lookback_days)
        years = range(lower_bound.year, reference_date.year + 1)
        all_entries = [entry for year in years for entry in self._category_entries(year)]
        unique_entries = {(item["ticker"], item["detail_url"]): item for item in all_entries}
        return list(unique_entries.values())

    def fetch_detail(self, entry: dict[str, Any]) -> dict[str, Any]:
        soup = self._get_soup(entry["detail_url"])
        terms: dict[str, str] = {}
        for row in soup.select("table tr"):
            cells = [_compact(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) >= 2 and cells[0].startswith("Aracı Kurum"):
                terms["Aracı Kurum"] = " | ".join(cells[1:])
                continue
            if len(cells) >= 2 and cells[0].endswith(":"):
                terms[cells[0].rstrip(":").strip()] = " | ".join(cells[1:])
        summary: dict[str, str] = {}
        for heading in soup.find_all("h5"):
            container = heading.find_parent("li")
            if container:
                summary[_compact(heading.get_text())] = _compact(container.get_text(" ", strip=True))
        finance = self._parse_finance_table(soup)
        documents = self._extract_documents(soup)
        offered_lots = _parse_turkish_number(terms.get("Pay", ""))
        primary_lots = _parse_turkish_number(_find_summary(summary, "Halka Arz Şekli").split("Ortak Satışı")[0])
        secondary_lots = sum(
            _parse_turkish_number(match) or 0
            for match in re.findall(r"Ortak Satışı\s*:\s*([\d.,]+\s*Lot)", _find_summary(summary, "Halka Arz Şekli"))
        )
        allocation_text = _find_summary(summary, "Tahsisat Grupları")
        retail_match = re.search(r"\(%\s*([\d.,]+)\)\s*Yurt İçi Bireysel", allocation_text, re.IGNORECASE)
        retail_allocation = _parse_turkish_number(retail_match.group(1)) if retail_match else None
        
        distribution_text = _find_summary(summary, "Dağıtılacak Pay")
        retail_allocation_tl = None
        if "TL" in distribution_text.upper() or "₺" in distribution_text:
            tl_match = re.search(r"([\d.,]+)\s*(?:TL|₺)", distribution_text, re.IGNORECASE)
            if tl_match:
                retail_allocation_tl = _parse_turkish_number(tl_match.group(1))

        all_text = " ".join(summary.values()) + " ".join(terms.values())
        kisi_match = re.search(r"([\d.,]+)\s*(?:Ki\u015fi|Bireysel Yat\u0131r\u0131mc\u0131|Ba\u015fvuru|Kat\u0131l\u0131m)", all_text, re.IGNORECASE)
        participant_count = None
        if kisi_match:
            participant_count = _parse_turkish_number(kisi_match.group(1))
            if participant_count and participant_count < 1000 and "milyon" in all_text[max(0, kisi_match.start()-20):kisi_match.end()+20].lower():
                participant_count *= 1_000_000

        float_pct = _parse_turkish_number(_find_summary(summary, "Halka Açıklık"))
        stated_discount = _parse_turkish_number(_find_summary(summary, "Halka Arz İskontosu"))
        offer_date_text = terms.get("Halka Arz Tarihi") or entry.get("calendar_date_text", "")
        start_date, end_date = _parse_date_range(offer_date_text)
        parsed = {
            **entry,
            "calendar_date_text": offer_date_text or entry.get("calendar_date_text", "Tarih bulunamadı"),
            "start_date": start_date or entry.get("start_date"),
            "end_date": end_date or entry.get("end_date"),
            "ipo_price_tl": _parse_turkish_number(terms.get("Halka Arz Fiyatı/Aralığı", "")),
            "distribution_method": terms.get("Dağıtım Yöntemi"),
            "broker": terms.get("Aracı Kurum"),
            "market": terms.get("Pazar"),
            "listing_date": _parse_date_range(terms.get("Bist İlk İşlem Tarihi", ""))[0],
            "offered_lots": offered_lots,
            "primary_lots": primary_lots,
            "secondary_lots": secondary_lots or None,
            "retail_allocation_pct": retail_allocation,
            "retail_allocation_tl": retail_allocation_tl,
            "participant_count": participant_count,
            "float_pct": float_pct,
            "stated_discount_pct": stated_discount,
            "use_of_proceeds": _find_summary(summary, "Fonun Kullanım Yeri"),
            "price_stabilization": _find_summary(summary, "Fiyat İstikrarı"),
            "lockup": _find_summary(summary, "Satmama Taahhüdü"),
            "offer_size_mn_tl": _parse_money_million(_find_summary(summary, "Halka Arz Büyüklüğü")),
            "financials": finance,
            "documents": documents,
        }
        
        custom_data_path = Path(__file__).parent / "custom_ipo_data.json"
        if custom_data_path.exists():
            try:
                import json
                custom_data = json.loads(custom_data_path.read_text("utf-8-sig"))
                ticker_key = parsed.get("ticker", "")
                if ticker_key in custom_data:
                    cdata = custom_data[ticker_key]
                    if not parsed.get("participant_count"):
                        parsed["participant_count"] = cdata.get("participant_count")
                    if not parsed.get("retail_allocation_tl"):
                        parsed["retail_allocation_tl"] = cdata.get("retail_allocation_tl")
            except Exception as e:
                print("ERROR IN JSON OVERRIDE:", e)
        return parsed

    @staticmethod
    def _parse_finance_table(soup: BeautifulSoup) -> dict[str, Any]:
        for table in soup.find_all("table"):
            header = _compact(table.get_text(" ", strip=True)).lower()
            if "hasılat" not in header or "brüt kâr" not in header:
                continue
            rows = [[_compact(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])] for row in table.find_all("tr")]
            if len(rows) < 3 or len(rows[0]) < 2:
                continue
            periods = rows[0][1:]
            revenue = _parse_money_million(rows[1][1]) if len(rows[1]) > 1 else None
            gross_profit = _parse_money_million(rows[2][1]) if len(rows[2]) > 1 else None
            return {
                "as_of": _normalise_period(periods[0]) if periods else None,
                "revenue_mn_tl": revenue,
                "gross_profit_mn_tl": gross_profit,
                "gross_margin": gross_profit / revenue if revenue and gross_profit is not None else None,
                "source": "HalkArz detay sayfasındaki izahname özeti; resmi belge ile doğrulanmalı.",
            }
        return {}

    @staticmethod
    def _extract_documents(soup: BeautifulSoup) -> list[dict[str, str]]:
        documents = []
        keywords = ("izahname", "fiyat tespit", "fon kullanım", "denetim", "tasarruf sahip")
        for link in soup.find_all("a", href=True):
            name = _compact(link.get_text(" ", strip=True))
            if not name or not any(keyword in name.lower() for keyword in keywords):
                continue
            href = link["href"]
            documents.append({
                "name": name,
                "url": href,
                "tier": "official" if "kap.org.tr" in href or "spk.gov.tr" in href else "issuer",
            })
        unique = {(item["name"], item["url"]): item for item in documents}
        return list(unique.values())
