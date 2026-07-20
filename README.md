# Arz Pusulası

BIST halka arzlarını **yatırım tavsiyesi üretmeden**, kaynak, veri kalitesi ve risk kontrolü üzerinden inceleyen yerel karar-destek uygulaması.

Eski sürümdeki kurgusal şirketler, yapay geçmiş performans ve `%100 doğruluk` iddiası kaldırıldı. Yeni sürüm, eksik veriyi puanla doldurmaz; belge tamamlama ihtiyacı olarak gösterir.

## Başlatma

```powershell
pip install -r requirements.txt
python backend/main.py refresh
python backend/app.py
```

Arayüzü `http://127.0.0.1:5050` adresinde açın. Tarayıcıdaki **Veriyi yenile** düğmesi takvimi tekrar alır, son 365 günün gerçekleşmelerini hesaplar ve raporu günceller. Geçmiş veri 12 saatlik önbellekte saklanır; zorla yenilemek için `python backend/main.py refresh --force-market-refresh` çalıştırın.

Tek seferlik metin raporu için:

```powershell
python backend/main.py print
```

Üretilen dosyalar:

- `backend/data/report.json`: Arayüzün kullandığı kaynaklı rapor.
- `backend/data/latest_report.md`: Okunabilir inceleme özeti.

## Yöntem

Puan, gelecekteki getiri/tavan/taban tahmini değildir. Bir halka arzın inceleme önceliğini gösterir.

| Başlık | Ağırlık | Nasıl kullanılır |
| --- | ---: | --- |
| Finansal dayanıklılık | 30 | Borçluluk, likidite, faiz karşılama, marj ve finansal tablonun güncelliği |
| Arz yapısı ve değerleme | 25 | Sermaye artırımı/ortak satışı, fon kullanımı, halka açıklık, taahhüt, fiyat istikrarı, emsal değerleme |
| Yönetim ve açıklık | 15 | Bağımsız denetim görüşü, belge ve kaynak kalitesi, ilişkili taraf riski |
| Yakın dönem emsal ortamı | 20 | En fazla son 365 gündeki doğrulanmış 5-gün sonuçları; 180 günlük yarı ömürle ağırlıklandırılır |
| Dağıtım ve aracı kurum | 10 | Eşit/oransal dağıtım, bireysel tahsisat, takvim yoğunluğu ve yeterli örneklem varsa aracı kurum kohortu |

`2022` gibi uzak dönemler ana karşılaştırmayı belirlemez. Yakın dönem emsal sinyali için en az 6, aracı kurum kohortu için en az 1 kaynaklı gözlem gerekir. Bu eşik sağlanmazsa ilgili başlık puanlanmaz.

## Veri kaynakları

1. **Resmi belgeler:** KAP, SPK ve SPK onaylı izahname.
2. **İhraççı / aracı kurum:** şirket yatırımcı ilişkileri ve halka arz sayfaları.
3. **Takvim:** `https://halkarz.com/` takvim ve ilk tarama kaynağıdır; tek başına finansal doğrulama değildir.

Canlı tarayıcı, HalkArz sayfasından tarih, fiyat, lot, dağıtım, aracı kurum, halka açıklık, fon kullanım özeti, fiyat istikrarı, satmama taahhüdü ve kaynak bağlantılarını alır.

## Resmi finansal metrik eklemek

`backend/data/metrics_overrides.json` dosyasına yalnızca bir kaynak URL ile birlikte şu alanları ekleyin:

```json
{
  "KOD": {
    "financials": {
      "as_of": "2026-03-31",
      "net_debt_to_equity": 0.72,
      "net_debt_to_ebitda": 2.4,
      "current_ratio": 1.18,
      "interest_coverage": 3.2,
      "net_margin": 0.08,
      "audit_opinion": "unqualified"
    },
    "metric_sources": [
      {"name": "Onaylı izahname", "url": "https://...", "tier": "official", "pages": "..."}
    ]
  }
}
```

Sistem mevcut eklenmiş veriyi takvimden gelen ham bilgilerle birleştirir. `as_of` alanı güncel değilse güven puanı düşer.

## Yakın dönem gerçekleşme verisi

`backend/data/recent_outcomes.json`, uygulamanın yeniden ürettiği 12 saatlik önbellektir. Uygulama, HalkArz arşivinden rapor tarihinden önceki en fazla 365 gündeki adayları alır; halka arz fiyatı ve ilk işlem tarihini aday sayfasından, kapanışları ise Yahoo Finance grafik verisinden okuyarak 5. seans getirisi ile ilk 20 seanstaki azami düşüşü hesaplar. Her kayıtta iki kaynak URL'i tutulur; fiyat veya tarih alınamazsa kayıt atlanır.

Gerekirse dosyaya elle eklenen kayıtlar da aynı şemaya uymalıdır:

```json
[
  {
    "ticker": "ORNEK",
    "listing_date": "2026-06-12",
    "return_5d_pct": 7.8,
    "max_drawdown_20d_pct": 11.2,
    "broker": "Örnek Yatırım",
    "sector": "Enerji",
    "source_url": "https://www.kap.org.tr/..."
  }
]
```

Yeterli kaynaklı kayıt elde edilemezse sistem piyasa rejimini ve aracı kurum geçmişini **bilinmiyor** olarak bırakır. Bu, varsayılan orta puandan daha güvenli bir davranıştır.

## Sınırlar

- Aracı kurumun geçmişi nedensel başarı ölçüsü değildir; ikincil bir sinyaldir.
- Fiyat tespit raporundaki iskonto, tek başına güvenlik marjı sayılmaz; güncel emsal çarpanlar ayrıca kontrol edilmelidir.
- Halka arz şartları ve belgeler değişebileceğinden talep toplamadan önce KAP/SPK belgesi tekrar doğrulanmalıdır.
- Uygulama kişisel portföy, vade, likidite ihtiyacı veya risk toleransı bilmediğinden **katıl/katılma** tavsiyesi vermez.
