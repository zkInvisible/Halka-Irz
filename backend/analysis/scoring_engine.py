"""Kanıt temelli halka arz inceleme ve puanlama motoru.

Bu modül getiri veya tavan tahmini yapmaz. Puan, halka arz bilgisinin kalitesi ve
izahnamedeki inceleme başlıklarının görünümü için karar-destek sinyalidir.

Puanlama iyileştirmeleri:
- Kademeli (lerp) geçişli band puanlaması: sınır noktalarında ani atlama yerine
  doğrusal interpolasyon yaparak daha hassas değerlendirme sağlar.
- Sermaye artırımı oranı, fon kullanımı ve emsal değerleme alt bileşenleri
  daha gerçekçi ağırlıklarla güncellendi.
- Finansal sağlamlık bileşeni: brüt/net marj formülü, aşırı yüksek marjlarda
  azalan getiri (diminishing returns) uygulanacak şekilde güncellendi.
- Belge kalitesi ve denetim görüşü ayrıntılı değerlendi.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from data.market_data import canonical_broker


COMPONENT_WEIGHTS = {
    "Finansal dayanıklılık": 30,
    "Arz yapısı ve değerleme": 25,
    "Yönetim ve açıklık": 15,
    "Yakın dönem emsal ortamı": 20,
    "Dağıtım ve aracı kurum": 10,
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))


def _lerp(value: float, low: float, high: float, low_score: float, high_score: float) -> float:
    """İki nokta arasında doğrusal interpolasyon ile puan hesaplar."""
    if high == low:
        return high_score
    t = (value - low) / (high - low)
    return low_score + t * (high_score - low_score)


def _weighted_mean(items: Iterable[tuple[float | None, float]]) -> tuple[float | None, float]:
    valid = [(score, weight) for score, weight in items if score is not None]
    weight_total = sum(weight for _, weight in valid)
    if not weight_total:
        return None, 0
    return sum(score * weight for score, weight in valid) / weight_total, weight_total


def _ratio_score_smooth(value: float | None, bands: list[tuple[float, float]]) -> float | None:
    """Kademeli geçişli band puanlaması.

    bands: [(üst_sınır, puan), ...] sıralı listesi.
    Değer iki band arasındaysa doğrusal interpolasyon yapılır.
    """
    if value is None:
        return None
    if value <= bands[0][0]:
        return bands[0][1]
    for i in range(1, len(bands)):
        if value <= bands[i][0]:
            return _lerp(value, bands[i - 1][0], bands[i][0], bands[i - 1][1], bands[i][1])
    return bands[-1][1]


def _freshness_score(as_of: str | None, today: date) -> float | None:
    if not as_of:
        return None
    try:
        age_days = (today - date.fromisoformat(as_of)).days
    except ValueError:
        return None
    if age_days < 0:
        return 85  # Gelecek tarihli veri şüpheli ama tamamen reddetme
    if age_days <= 90:
        return 100
    if age_days <= 150:
        return _lerp(age_days, 90, 150, 100, 80)
    if age_days <= 240:
        return _lerp(age_days, 150, 240, 80, 50)
    if age_days <= 365:
        return _lerp(age_days, 240, 365, 50, 20)
    return 10


def _margin_score(margin: float | None, base: float = 35, multiplier: float = 220) -> float | None:
    """Marj puanlaması: düşük marjlarda hızlı cezalandırma, yüksek marjlarda azalan getiri."""
    if margin is None:
        return None
    if margin < 0:
        # Zarar durumunda ağır ceza ama tamamen sıfırlama yok
        return _clamp(25 + margin * 180)
    # Azalan getiri: sqrt benzeri eğri ile yüksek marjlarda puan kazanımı yavaşlar
    effective = margin ** 0.7 if margin > 0.3 else margin
    return _clamp(base + effective * multiplier)


def _financial_component(financials: dict[str, Any], today: date) -> tuple[dict[str, Any], list[str]]:
    gross_margin = _number(financials.get("gross_margin"))
    net_margin = _number(financials.get("net_margin"))
    net_debt_to_equity = _number(financials.get("net_debt_to_equity"))
    net_debt_to_ebitda = _number(financials.get("net_debt_to_ebitda"))
    current_ratio = _number(financials.get("current_ratio"))
    interest_coverage = _number(financials.get("interest_coverage"))

    gross_score = _margin_score(gross_margin, base=35, multiplier=220)
    net_score = _margin_score(net_margin, base=30, multiplier=280)

    # Kademeli geçişli borç puanlaması
    debt_equity_score = _ratio_score_smooth(
        net_debt_to_equity,
        [(-0.5, 95), (0, 88), (0.3, 80), (0.6, 65), (1.0, 45), (1.5, 25), (3.0, 8)]
    )
    debt_ebitda_score = _ratio_score_smooth(
        net_debt_to_ebitda,
        [(0, 95), (1.5, 85), (2.5, 68), (3.5, 48), (5.0, 25), (8.0, 8)]
    )
    liquidity_score = _ratio_score_smooth(
        current_ratio,
        [(0.5, 8), (0.75, 20), (1.0, 42), (1.3, 62), (1.8, 80), (3.0, 88)]
    )
    coverage_score = _ratio_score_smooth(
        interest_coverage,
        [(0.5, 5), (1.0, 18), (2.0, 40), (3.5, 62), (6.0, 82), (10.0, 90)]
    )
    freshness = _freshness_score(financials.get("as_of"), today)

    # Ağırlıklar: borç ve likidite en kritik
    score, available_weight = _weighted_mean(
        [
            (gross_score, 3),
            (net_score, 5),
            (debt_equity_score, 7),
            (debt_ebitda_score, 6),
            (liquidity_score, 5),
            (coverage_score, 3),
            (freshness, 1),
        ]
    )
    notes: list[str] = []
    if net_debt_to_equity is not None:
        level = "düşük" if net_debt_to_equity < 0.5 else "orta" if net_debt_to_equity < 1 else "yüksek"
        notes.append(f"Net borç/özkaynak: {net_debt_to_equity:.2f}x ({level})")
    if net_debt_to_ebitda is not None:
        level = "düşük" if net_debt_to_ebitda < 2 else "orta" if net_debt_to_ebitda < 4 else "yüksek"
        notes.append(f"Net borç/FAVÖK: {net_debt_to_ebitda:.2f}x ({level})")
    if current_ratio is not None:
        status = "sağlıklı" if current_ratio >= 1.3 else "yeterli" if current_ratio >= 1.0 else "riskli"
        notes.append(f"Cari oran: {current_ratio:.2f}x ({status})")
    if interest_coverage is not None:
        status = "güçlü" if interest_coverage >= 4 else "yeterli" if interest_coverage >= 2 else "zayıf"
        notes.append(f"Faiz karşılama: {interest_coverage:.1f}x ({status})")
    if gross_margin is not None:
        notes.append(f"Brüt marj: %{gross_margin * 100:.1f}")
    if net_margin is not None:
        status = "kârlı" if net_margin > 0.05 else "düşük kârlı" if net_margin > 0 else "zararlı"
        notes.append(f"Net marj: %{net_margin * 100:.1f} ({status})")
    if score is None:
        notes.append("Borç, likidite ve net kârlılık metrikleri kaynaklı olarak eksik.")
    return {
        "name": "Finansal dayanıklılık",
        "weight": COMPONENT_WEIGHTS["Finansal dayanıklılık"],
        "score": None if score is None else round(score, 1),
        "coverage_pct": round(available_weight / 30 * 100),
        "notes": notes,
    }, notes


def _offer_component(offer: dict[str, Any]) -> dict[str, Any]:
    total_lots = _number(offer.get("offered_lots"))
    primary_lots = _number(offer.get("primary_lots"))
    primary_ratio = primary_lots / total_lots if total_lots and primary_lots is not None else None
    float_pct = _number(offer.get("float_pct"))
    stated_discount = _number(offer.get("stated_discount_pct"))
    price_support = (offer.get("price_stabilization") or "").lower()
    lockup = (offer.get("lockup") or "").lower()
    use_of_proceeds = (offer.get("use_of_proceeds") or "").lower()
    peer_discount = _number(offer.get("peer_valuation_discount_pct"))

    # Sermaye artırımı oranı: %100 sermaye artırımı en iyi, %0 sadece ortak satışı en kötü
    primary_score = None if primary_ratio is None else _clamp(25 + primary_ratio * 65)

    # Halka açıklık: %20-30 ideal, çok düşük veya çok yüksek sorunlu
    float_score = None
    if float_pct is not None:
        if float_pct < 10:
            float_score = _lerp(float_pct, 0, 10, 20, 40)
        elif float_pct < 20:
            float_score = _lerp(float_pct, 10, 20, 40, 75)
        elif float_pct <= 35:
            float_score = _lerp(float_pct, 20, 35, 75, 88)
        elif float_pct <= 50:
            float_score = _lerp(float_pct, 35, 50, 88, 70)
        else:
            float_score = _lerp(float_pct, 50, 80, 70, 45)
        float_score = _clamp(float_score)

    # Satmama taahhüdü: süre ne kadar uzunsa o kadar iyi
    lockup_score = None
    if lockup:
        if any(kw in lockup for kw in ("2 yıl", "24 ay")):
            lockup_score = 92
        elif any(kw in lockup for kw in ("1 yıl", "12 ay", "18 ay")):
            lockup_score = 82
        elif any(kw in lockup for kw in ("6 ay", "180 gün")):
            lockup_score = 60
        else:
            lockup_score = 40

    # Fiyat istikrarı: aktif destek programı olması artı
    support_score = None
    if price_support:
        if "planlanmam" in price_support or "yok" in price_support:
            support_score = 15  # Fiyat istikrarı planlanmıyorsa hafif-orta ceza
        elif any(token in price_support for token in ("30 gün", "45 gün")):
            support_score = 85
        elif any(token in price_support for token in ("15 gün", "%20", "%10")):
            support_score = 72
        else:
            support_score = 50

    # Fon kullanımı: yatırım amaçlı = yüksek, borç ödeme = düşük, karışık = orta
    proceeds_score = None
    if use_of_proceeds:
        growth_words = ("yatırım", "tesis", "makine", "ekipman", "yenilenebilir", "ges", "res", "kapasite", "ar-ge", "ihracat")
        debt_words = ("borç", "kredi", "refinansman", "faiz")
        dividend_words = ("temettü", "ortaklara")
        growth_hits = sum(word in use_of_proceeds for word in growth_words)
        debt_hits = sum(word in use_of_proceeds for word in debt_words)
        dividend_hits = sum(word in use_of_proceeds for word in dividend_words)
        # Dengeli nominal değerler (Aşırı ceza uygulanmaz, ancak etkisi korunur)
        proceeds_score = _clamp(45 + growth_hits * 10 - debt_hits * 15 - dividend_hits * 20)

    # Emsal değerleme
    valuation_score = None
    if peer_discount is not None:
        # Emsal iskontosu ne kadar yüksekse o kadar cazip (ortalama %15-20 ideal)
        valuation_score = _clamp(40 + peer_discount * 1.8)
    elif stated_discount is not None:
        # Fiyat tespit raporundaki iskonto tek başına değerleme kanıtı değildir.
        valuation_score = _clamp(35 + stated_discount * 0.9)

    score, available_weight = _weighted_mean(
        [
            (primary_score, 5),
            (proceeds_score, 4),
            (valuation_score, 5),
            (float_score, 3),
            (lockup_score, 4),
            (support_score, 4),
        ]
    )
    notes = []
    if primary_ratio is not None:
        label = "güçlü" if primary_ratio > 0.7 else "orta" if primary_ratio > 0.3 else "zayıf"
        notes.append(f"Sermaye artırımı payı: %{primary_ratio * 100:.1f} ({label})")
    if float_pct is not None:
        notes.append(f"Halka açıklık: %{float_pct:.2f}")
    if stated_discount is not None:
        notes.append(f"Beyan edilen fiyat tespit iskontosu: %{stated_discount:.1f} (tek başına yeterli değildir)")
    if lockup:
        notes.append(f"Satmama taahhüdü: {lockup[:80]}")
    if not price_support:
        notes.append("Fiyat istikrarı planı yok veya doğrulanamadı.")
    elif "planlanmam" in price_support:
        notes.append("Fiyat istikrarı işlemi planlanmıyor.")
    if peer_discount is None:
        notes.append("Bağımsız emsal çarpan karşılaştırması girilmedi.")
    return {
        "name": "Arz yapısı ve değerleme",
        "weight": COMPONENT_WEIGHTS["Arz yapısı ve değerleme"],
        "score": None if score is None else round(score, 1),
        "coverage_pct": round(available_weight / 25 * 100),
        "notes": notes,
    }


def _governance_component(offer: dict[str, Any], today: date) -> dict[str, Any]:
    financials = offer.get("financials", {})
    audit = (financials.get("audit_opinion") or "").lower()
    documents = offer.get("documents", [])
    source_count = len(documents) + len(offer.get("metric_sources", []))

    # Denetim görüşü: "şartlı" ile "olumsuz" ayrımı
    audit_score = None
    if audit:
        if audit == "unqualified":
            audit_score = 92
        elif audit in ("qualified", "şartlı"):
            audit_score = 45
        elif audit in ("adverse", "olumsuz"):
            audit_score = 12
        elif audit in ("disclaimer", "görüş bildirmekten kaçınma"):
            audit_score = 8
        else:
            audit_score = 35

    # Belge kalitesi: KAP/SPK bağlantısı varsa bonus
    official_count = sum(1 for d in documents if d.get("tier") == "official")
    if source_count >= 4 and official_count >= 2:
        document_score = 95
    elif source_count >= 3:
        document_score = 85
    elif source_count >= 2:
        document_score = 68
    elif source_count >= 1:
        document_score = 45
    else:
        document_score = None

    freshness_score = _freshness_score(financials.get("as_of"), today)

    # İlişkili taraf riski
    related_party = financials.get("related_party_risk")
    related_party_score = None
    if related_party is not None:
        related_party_score = {"low": 88, "medium": 52, "high": 18}.get(
            str(related_party).lower(), 40
        )

    score, available_weight = _weighted_mean(
        [(audit_score, 6), (document_score, 4), (freshness_score, 3), (related_party_score, 2)]
    )
    notes = [f"Doğrulanmış belge bağlantısı: {source_count} (resmi: {official_count})"]
    if not audit:
        notes.append("Bağımsız denetim görüşü işlenmedi.")
    elif audit == "unqualified":
        notes.append("Denetim görüşü: olumlu (şartsız).")
    else:
        notes.append(f"Denetim görüşü: {audit} — dikkat gerektirir.")
    if related_party is None:
        notes.append("İlişkili taraf yoğunluğu işlenmedi.")
    return {
        "name": "Yönetim ve açıklık",
        "weight": COMPONENT_WEIGHTS["Yönetim ve açıklık"],
        "score": None if score is None else round(score, 1),
        "coverage_pct": round(available_weight / 15 * 100),
        "notes": notes,
    }


def _market_component(offer: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    overall = market.get("overall", {})
    sector = market.get("sectors", {}).get(offer.get("sector"), {})
    sector_is_usable = sector.get("sample_size", 0) >= 3
    source = sector if sector_is_usable else overall
    sample_size = source.get("sample_size", 0)
    median_return = _number(source.get("weighted_median_return_5d"))
    drawdown_rate = _number(source.get("weighted_median_drawdown_20d"))
    positive_rate = _number(source.get("weighted_positive_rate_pct"))
    minimum_sample = 3 if sector_is_usable else 6

    if sample_size < minimum_sample or median_return is None:
        return {
            "name": "Yakın dönem emsal ortamı",
            "weight": COMPONENT_WEIGHTS["Yakın dönem emsal ortamı"],
            "score": None,
            "coverage_pct": 0,
            "notes": [f"Son 365 günde en az {minimum_sample} doğrulanmış 5-gün sonucu olmadan bu bölüm puanlanmaz. Şu an {sample_size} kayıt var."],
        }

    # Getiri puanı: kademeli — %0 civarı nötr, pozitif iyi, negatif kötü
    return_score = _clamp(50 + median_return * 3.5)

    # Düşüş puanı: düşük drawdown iyi
    drawdown_score = None if drawdown_rate is None else _clamp(100 - drawdown_rate * 1.5)

    # Pozitif oran: ek sinyal
    positive_score = None
    if positive_rate is not None:
        positive_score = _clamp(positive_rate * 0.9)

    score, available_weight = _weighted_mean(
        [(return_score, 10), (drawdown_score, 6), (positive_score, 4)]
    )

    scope = "sektör" if sector_is_usable else "genel"
    notes = [
        f"{scope.title()} emsal örneklemi: {sample_size} arz (en fazla 365 gün, 180 gün yarı ömür).",
        f"Ağırlıklı medyan 5-gün getirisi: %{median_return:.1f}",
    ]
    if drawdown_rate is not None:
        severity = "düşük" if drawdown_rate < 10 else "orta" if drawdown_rate < 20 else "yüksek"
        notes.append(f"İlk 20 seanstaki ağırlıklı medyan azami düşüş: %{drawdown_rate:.1f} ({severity})")
    if positive_rate is not None:
        notes.append(f"Pozitif 5-gün sonucu ağırlığı: %{positive_rate:.1f}")
    return {
        "name": "Yakın dönem emsal ortamı",
        "weight": COMPONENT_WEIGHTS["Yakın dönem emsal ortamı"],
        "score": None if score is None else round(score, 1),
        "coverage_pct": round(available_weight / 20 * 100),
        "notes": notes,
    }


def _distribution_component(offer: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    method = (offer.get("distribution_method") or "").lower()
    retail_share = _number(offer.get("retail_allocation_pct"))
    concurrent = _number(offer.get("concurrent_offer_count"))
    broker = offer.get("broker")
    broker_data = market.get("brokers", {}).get(canonical_broker(broker), {})
    broker_return = _number(broker_data.get("weighted_median_return_5d"))
    broker_stability = _number(broker_data.get("stability_score"))
    broker_n = broker_data.get("sample_size", 0)

    # Dağıtım yöntemi: eşit en iyi, oransal orta
    allocation_score = None
    if method:
        if "eşit" in method:
            allocation_score = 88
        elif "oransal" in method:
            allocation_score = 55
        else:
            allocation_score = 45

    # Bireysel yatırımcı tahsisat oranı
    retail_score = None if retail_share is None else _clamp(35 + retail_share * 0.8)

    # Takvim yoğunluğu: aynı dönemde çok arz = düşük talep riski
    calendar_score = None
    if concurrent is not None:
        if concurrent <= 1:
            calendar_score = 90
        elif concurrent <= 3:
            calendar_score = _lerp(concurrent, 1, 3, 90, 65)
        elif concurrent <= 6:
            calendar_score = _lerp(concurrent, 3, 6, 65, 35)
        else:
            calendar_score = _lerp(concurrent, 6, 10, 35, 15)
        calendar_score = _clamp(calendar_score)

    # Aracı kurum geçmiş performansı
    broker_score = None
    if broker_n >= 3 and broker_return is not None:
        broker_score = _clamp(50 + broker_return * 2.2)

    score, available_weight = _weighted_mean(
        [(allocation_score, 3), (retail_score, 2), (calendar_score, 2), (broker_score, 3)]
    )
    notes = []
    if concurrent is not None:
        intensity = "düşük" if concurrent <= 2 else "orta" if concurrent <= 4 else "yüksek"
        notes.append(f"Aynı 7 günlük pencerede {int(concurrent)} arz takvimi var ({intensity} yoğunluk).")
    if method:
        notes.append(f"Dağıtım yöntemi: {method.title()}")
    if broker_score is None:
        notes.append("Aracı kurum geçmişi en az 3 doğrulanmış yakın dönem gözlem olmadan puanlanmaz.")
    else:
        stability_note = f" İstikrar puanı: {broker_stability:.1f}/100." if broker_stability is not None else ""
        notes.append(f"Aracı kurum yakın dönem örneklemi: {broker_n} arz.{stability_note}")
    return {
        "name": "Dağıtım ve aracı kurum",
        "weight": COMPONENT_WEIGHTS["Dağıtım ve aracı kurum"],
        "score": None if score is None else round(score, 1),
        "coverage_pct": round(available_weight / 10 * 100),
        "notes": notes,
    }


def _red_flags(offer: dict[str, Any]) -> list[str]:
    financials = offer.get("financials", {})
    flags: list[str] = []
    debt_equity = _number(financials.get("net_debt_to_equity"))
    debt_ebitda = _number(financials.get("net_debt_to_ebitda"))
    current_ratio = _number(financials.get("current_ratio"))
    interest_coverage = _number(financials.get("interest_coverage"))
    net_margin = _number(financials.get("net_margin"))
    total_lots = _number(offer.get("offered_lots"))
    secondary_lots = _number(offer.get("secondary_lots"))

    if debt_equity is not None and debt_equity > 1:
        flags.append("⚠️ Net borç/özkaynak 1x üzerindedir; refinansman ve faiz riskini izahname üzerinden kontrol et.")
    if debt_ebitda is not None and debt_ebitda > 4:
        flags.append("⚠️ Net borç/FAVÖK 4x üzerindedir; borç vadesi ve kur riskini ayrıca incele.")
    if current_ratio is not None and current_ratio < 1:
        flags.append("⚠️ Cari oran 1x altındadır; kısa vadeli likidite riski vardır.")
    if interest_coverage is not None and interest_coverage < 1.5:
        flags.append("⚠️ Faiz karşılama oranı 1.5x altındadır; faiz yükü riski dikkat gerektirir.")
    if net_margin is not None and net_margin < 0:
        flags.append("🔴 Son dönem net zarar açıklanmıştır.")
    if total_lots and secondary_lots is not None and secondary_lots / total_lots >= 0.5:
        flags.append("⚠️ Arzın en az yarısı ortak satışıdır; büyüme sermayesi etkisi sınırlı olabilir.")

    price_support = (offer.get("price_stabilization") or "").lower()
    if not price_support or "planlanmam" in price_support:
        flags.append("ℹ️ Fiyat istikrarı işlemi planlanmıyor veya kaynakta görünmüyor.")

    # Halka açıklık uyarısı
    float_pct = _number(offer.get("float_pct"))
    if float_pct is not None and float_pct < 10:
        flags.append("⚠️ Halka açıklık oranı %10 altındadır; likidite riski yüksek olabilir.")

    # Düşük belge sayısı
    documents = offer.get("documents", [])
    metric_sources = offer.get("metric_sources", [])
    if len(documents) + len(metric_sources) == 0:
        flags.append("🔴 Hiçbir kaynak belge bağlantısı bulunamadı.")

    return flags


def _review_questions(offer: dict[str, Any], components: list[dict[str, Any]]) -> list[str]:
    questions = []
    financials = offer.get("financials", {})
    missing = []
    for key, label in (
        ("net_debt_to_equity", "net borç/özkaynak"),
        ("net_debt_to_ebitda", "net borç/FAVÖK"),
        ("current_ratio", "cari oran"),
        ("interest_coverage", "faiz karşılama oranı"),
        ("net_margin", "net kâr marjı"),
        ("audit_opinion", "bağımsız denetim görüşü"),
    ):
        if financials.get(key) is None:
            missing.append(label)
    if missing:
        questions.append("📋 Resmi izahnameden şu alanları tamamla: " + ", ".join(missing) + ".")
    if offer.get("peer_valuation_discount_pct") is None:
        questions.append("📊 Fiyat tespit raporundaki benzer şirket çarpanlarını ve güncel piyasa değerlerini yeniden hesapla.")
    if not any(c["name"] == "Yakın dönem emsal ortamı" and c["score"] is not None for c in components):
        questions.append("📈 Son 365 gün için doğrulanmış 5-gün getiri ve 20-gün azami düşüş verisini ekle.")
    if not offer.get("lockup"):
        questions.append("🔒 Satmama taahhüdü bilgisini doğrula.")
    questions.append("📌 Risk faktörleri, ilişkili taraf işlemleri, dava/teminatlar ve fon kullanımının gerçekleşme raporunu ayrıca kontrol et.")
    return questions


def _decision_label(score: float | None, coverage: float, flags: list[str], market_ready: bool) -> str:
    """Karar etiketi: veri kapsaması ve risk bayrağı sayısına göre belirler.

    Etiketler katılma/katılmama tavsiyesi değildir; inceleme önceliğini gösterir.
    """
    critical_flags = sum(1 for f in flags if "🔴" in f)
    warning_flags = sum(1 for f in flags if "⚠️" in f)

    if not market_ready or coverage < 55:
        return "VERİ EKSİĞİ — KARAR VERME"
    if coverage < 65:
        return "VERİ YETERSİZ — TAMAMLA"
    if critical_flags >= 2 or (critical_flags >= 1 and warning_flags >= 2):
        return "YÜKSEK RİSK — DERİN İNCELEME"
    if warning_flags >= 3:
        return "YÜKSEK RİSK — DERİN İNCELEME"
    if score is not None and score >= 72:
        return "ÖNCELİKLİ İNCELEME"
    if score is not None and score >= 55:
        return "TEMKİNLİ İNCELEME"
    if score is not None and score >= 40:
        return "ARTIRILMIŞ RİSK — DİKKATLİ İNCELE"
    return "YÜKSEK RİSK — DERİN İNCELEME"


def assess_offer(offer: dict[str, Any], market: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Bir arzın kanıt, risk ve inceleme önceliği raporunu üretir."""
    today = today or date.today()
    financial_component, _ = _financial_component(offer.get("financials", {}), today)
    components = [
        financial_component,
        _offer_component(offer),
        _governance_component(offer, today),
        _market_component(offer, market),
        _distribution_component(offer, market),
    ]
    known_data_score, scored_weight = _weighted_mean(
        [(component["score"], component["weight"]) for component in components]
    )
    coverage = sum(component["weight"] * component["coverage_pct"] / 100 for component in components)

    # Eksik bileşenler "iyi" varsayılmasın diye nötr 50 puana yakınsatılır.
    # Coverage oranı yükseldikçe gerçek veri puanı daha belirleyici olur.
    evidence_score = None if known_data_score is None else 50 + (coverage / 100) * (known_data_score - 50)

    flags = _red_flags(offer)

    # Ağır risk bayraklarında puan düşürme
    critical_count = sum(1 for f in flags if "🔴" in f)
    if evidence_score is not None and critical_count > 0:
        evidence_score = evidence_score * (1 - critical_count * 0.05)

    return {
        **offer,
        "assessment": {
            "evidence_score": None if evidence_score is None else round(evidence_score, 1),
            "known_data_score": None if known_data_score is None else round(known_data_score, 1),
            "evidence_coverage_pct": round(coverage, 1),
            "scored_weight": round(scored_weight, 1),
            "decision_label": _decision_label(evidence_score, coverage, flags, market.get("overall", {}).get("is_ready", False)),
            "components": components,
            "red_flags": flags,
            "review_questions": _review_questions(offer, components),
            "disclaimer": "Bu çalışma yatırım tavsiyesi değildir; puan gelecekteki getiri veya tavan/taban sonucunu tahmin etmez.",
        },
    }
