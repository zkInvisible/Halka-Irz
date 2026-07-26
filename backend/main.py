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

from pipeline import (  # noqa: E402  (sys.path ayarından sonra içe aktarılmalı)
    REPORT_KEY,
    REPORT_META_KEY,
    build_report_meta,
    load_report,
    refresh_report,
    report_age_hours,
)
from data.store import get_store  # noqa: E402

DATA_DIR = BASE_DIR / "data"
REPORT_PATH = DATA_DIR / "report.json"
MARKDOWN_REPORT_PATH = DATA_DIR / "latest_report.md"


def _format_score(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}/100"


def render_markdown(report: dict[str, Any]) -> str:
    collection = report.get("collection", {})
    lines = [
        "# Halka Arz İnceleme Raporu",
        "",
        f"Üretim zamanı: {report.get('generated_at', '—')}",
        "",
        "Bu belge yatırım tavsiyesi değildir. Puan; doğrulanmış kanıt, risk ve inceleme "
        "önceliğini gösterir; gelecekteki getiri veya tavan/taban sonucunu tahmin etmez.",
        "",
        "## Yöntem",
        "",
        "- Yakın dönem emsalleri yalnızca son 365 günden alır; gözlemler 180 günlük yarı ömürle ağırlıklandırılır.",
        "- Emsal ortamı ve aracı kurum sinyali, en az 6 / 1 doğrulanmış gözlem yoksa puanlanmaz.",
        "- Veri kapsaması %65'in altındaysa sistem sonuç etiketi yerine belge tamamlama ister.",
        "- Aracı kurum geçmişi en fazla 10 puanlık ikincil bir sinyaldir; nedensel başarı göstergesi kabul edilmez.",
        "",
        "## Veri toplama",
        "",
        f"- Takvim kaydı: {collection.get('takvim_kaydi', '—')}",
        f"- Taslak (SPK sürecinde) arz: {collection.get('taslak_kaydi', '—')}",
        f"- Arşiv kaydı: {collection.get('arsiv_kaydi', '—')}",
        f"- Gerçekleşme üretilen: {collection.get('gerceklesme', {}).get('uretilen', '—')}",
        f"- Süre: {collection.get('sure_saniye', '—')} sn",
    ]
    for reason in collection.get("gerceklesme", {}).get("atlama_nedenleri", {}).values():
        lines.append(f"- Atlandı — {reason['aciklama']} ({', '.join(reason['kodlar'])})")
    for warning in collection.get("uyarilar", []):
        lines.append(f"- ⚠️ {warning}")

    lines += [
        "",
        "## Güncel Takvim",
        "",
        "| Kod | Talep toplama | Kanıt puanı | Veri kapsaması | İnceleme etiketi |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for offer in report.get("offers", []):
        assessment = offer["assessment"]
        lines.append(
            f"| {offer['ticker']} | {offer['calendar_date_text']} | "
            f"{_format_score(assessment['evidence_score'])} | "
            f"%{assessment['evidence_coverage_pct']:.0f} | {assessment['decision_label']} |"
        )

    for offer in report.get("offers", []):
        assessment = offer["assessment"]
        lines += ["", f"## {offer['ticker']} — {offer['company']}", ""]
        lines.append(
            f"- Talep toplama: {offer['calendar_date_text']} | "
            f"Fiyat: {offer.get('ipo_price_tl') or '—'} TL | "
            f"Aracı kurum: {offer.get('broker') or '—'}"
        )
        lines.append(
            f"- Kanıt puanı: {_format_score(assessment['evidence_score'])}; "
            f"veri kapsaması: %{assessment['evidence_coverage_pct']:.0f}."
        )
        for scenario in offer.get("projected_distribution", [])[:4]:
            people = scenario.get("participants")
            lines.append(
                f"  - Olası dağıtım: {people:,.0f} katılımda ~{scenario.get('lot_per_person')} lot "
                f"({scenario.get('tl_per_person')} TL)".replace(",", ".")
            )
        if assessment["red_flags"]:
            lines.append("- Risk işaretleri: " + " ".join(assessment["red_flags"]))
        lines.append("- Kontrol listesi: " + " ".join(assessment["review_questions"]))
        lines.append("- Kaynaklar:")
        for source in offer.get("metric_sources", []) + offer.get("documents", []):
            lines.append(f"  - [{source['name']}]({source['url']})")

    drafts = report.get("draft_offers", [])
    if drafts:
        lines += ["", f"## Taslak Arzlar ({len(drafts)})", ""]
        for draft in drafts[:60]:
            code = f"`{draft['ticker']}` " if draft.get("ticker") else ""
            lines.append(f"- {code}{draft['company']}")
        if len(drafts) > 60:
            lines.append(f"- … ve {len(drafts) - 60} şirket daha")

    return "\n".join(lines) + "\n"


def write_local_artifacts(report: dict[str, Any]) -> None:
    """Yerel çalışma için okunabilir çıktıları diske yazar.

    Sunucuda tek doğru kaynak veritabanıdır; bu dosyalar yalnızca yerel
    inceleme kolaylığı sağlar.
    """
    DATA_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MARKDOWN_REPORT_PATH.write_text(render_markdown(report), encoding="utf-8")


def seed_store_from_disk() -> bool:
    """Depo boşsa, depoda bulunan son rapor dosyasını başlangıç verisi yapar.

    Yeni bir sunucu ilk kez açıldığında veritabanı boştur. İlk yenileme
    tamamlanana kadar arayüzün bomboş kalmaması için, depoya commit edilmiş
    rapor dosyası "bayat" olarak yüklenir.
    """
    store = get_store()
    if store.get(REPORT_KEY):
        return False
    if not REPORT_PATH.exists():
        return False
    try:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    report["source_status"] = "bootstrap"
    store.set(REPORT_KEY, report)
    store.set(REPORT_META_KEY, build_report_meta(report))
    return True


def _progress(stage: str, detail: dict[str, Any]) -> None:
    parts = " ".join(f"{key}={value}" for key, value in detail.items())
    print(f"  · {stage} {parts}".rstrip())


def _use_utf8_console() -> None:
    """Windows konsolu varsayılan olarak cp1254 kullanır; Türkçe/simge çıktısı patlar."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _use_utf8_console()
    parser = argparse.ArgumentParser(description="Halka arz kanıt-temelli inceleme sistemi")
    parser.add_argument("command", nargs="?", choices=("refresh", "print", "status"), default="refresh")
    parser.add_argument(
        "--force-market-refresh",
        action="store_true",
        help="Detay önbelleğini yok sayarak bütün sayfaları yeniden indir.",
    )
    arguments = parser.parse_args()

    if arguments.command == "status":
        report = load_report()
        if not report:
            print("Depoda rapor yok.")
            return
        age = report_age_hours(report)
        print(f"Üretim: {report.get('generated_at')} ({age:.1f} saat önce)" if age is not None else "Üretim: —")
        print(json.dumps(report.get("collection", {}), ensure_ascii=False, indent=2))
        return

    if arguments.command == "refresh":
        started = datetime.now()
        report = refresh_report(progress=_progress, force_details=arguments.force_market_refresh)
        write_local_artifacts(report)
        collection = report["collection"]
        print()
        print(f"{len(report['offers'])} güncel arz · {len(report['draft_offers'])} taslak arz")
        realised = collection["gerceklesme"]
        print(f"Gerçekleşme: {realised['uretilen']} kayıt, {realised['atlanan']} atlandı")
        for reason, info in realised["atlama_nedenleri"].items():
            print(f"  - {info['aciklama']} → {', '.join(info['kodlar'])}")
        print(
            f"Detay: {collection['detay']['onbellekten']} önbellek + "
            f"{collection['detay']['indirilen']} indirme, {collection['detay']['basarisiz']} hata"
        )
        for warning in collection["uyarilar"]:
            print(f"UYARI: {warning}")
        print(f"Süre: {(datetime.now() - started).total_seconds():.1f} sn")
        print(f"Rapor: {REPORT_PATH}")
        print(f"Markdown: {MARKDOWN_REPORT_PATH}")
        return

    report = load_report()
    if not report:
        report = refresh_report(progress=_progress)
        write_local_artifacts(report)
    rendered = render_markdown(report)
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(rendered.encode("utf-8"))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
