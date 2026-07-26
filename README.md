# Arz Pusulası

BIST halka arzlarını **yatırım tavsiyesi üretmeden**, kaynak, veri kalitesi ve risk kontrolü
üzerinden inceleyen karar-destek uygulaması. Eksik veriyi puanla doldurmaz; belge tamamlama
ihtiyacı olarak gösterir.

## Çalıştırma

### Yerel

```powershell
pip install -r requirements.txt
python backend/main.py refresh      # veriyi topla
python backend/app.py               # http://127.0.0.1:5050
```

`baslat.bat` ikisini sırayla yapar. İlk tarama ~1 dakika sürer; sonraki taramalar detay
önbelleği sayesinde ~10 saniyede biter.

Diğer komutlar:

```powershell
python backend/main.py status                        # son raporun durumu ve toplama istatistikleri
python backend/main.py print                         # metin raporu
python backend/main.py refresh --force-market-refresh # önbelleği yok say, her sayfayı yeniden indir
```

### Sunucu (Render)

`render.yaml` blueprint'i hazır: `gunicorn -c gunicorn.conf.py wsgi:app`.

> **Kalıcılık `DATABASE_URL` ile gelir.** Render'ın ücretsiz katmanında disk kalıcı değildir;
> servis her yeniden başladığında dosya sistemi git checkout'una döner. `DATABASE_URL`
> tanımlıysa rapor, önbellek ve oylar Postgres'e yazılır ve yeniden başlatmaya dayanır.
> Tanımlı değilse uygulama SQLite'a düşer ve çalışır, ama her restart'ta veri sıfırlanır
> (sonra kendini yeniler).

Bu kurulumda Render panelinde bir **Supabase Postgres** bağlantısı tanımlı. `render.yaml`
bu değişkeni `sync: false` ile bırakır, yani blueprint uygulansa bile paneldeki değer
korunur. Supabase/Render panellerinden kopyalanan bağlantı dizeleri sık sık
`?sslmode=require` eki taşır; pg8000 bu parametreyi tanımadığı için uygulama açılışta
çökerdi — `store.normalise_database_url` bu tür libpq'ya özgü parametreleri ayıklar.

Depo durumunu `GET /api/status` yanıtındaki `storage` alanından görebilirsiniz
(`persistent: true` → Postgres kullanılıyor).

Ortam değişkenleri:

| Değişken | Varsayılan | Ne işe yarar |
| --- | --- | --- |
| `DATABASE_URL` | — | Postgres bağlantısı. Yoksa yerel SQLite. |
| `RUN_SCHEDULER` | `1` | Canlı tutma pingi + otomatik yenileme. |
| `WEB_THREADS` | `8` | Gunicorn iş parçacığı sayısı. |
| `RENDER_EXTERNAL_URL` | — | Render otomatik verir; canlı tutma pingi bu adrese gider. |
| `WRITE_LOCAL_ARTIFACTS` | kapalı | Açıksa her yenilemede `report.json` / `latest_report.md` diske de yazılır. |

## Mimari

```
frontend/            arayüz (statik)
backend/
  app.py             Flask API + zamanlayıcı  — yenilemeyi asla istek içinde çalıştırmaz
  pipeline.py        rapor üretim hattı
  main.py            komut satırı
  data/
    http_client.py   yeniden denemeli, nazik HTTP istemcisi
    sources.py       HalkArz takvim / arşiv / detay çözümleyicisi
    market_data.py   Yahoo Finance fiyat serileri + gerçekleşme ölçütleri
    store.py         Postgres/SQLite anahtar-değer deposu
  analysis/
    scoring_engine.py  puanlama
    backtest.py        emsal istatistikleri
```

**Yenileme istek içinde çalışmaz.** `POST /api/refresh` işi kabul edip hemen `202` döner;
tarama arka planda yürür ve `GET /api/status` ile izlenir. Böylece tarama sürerken de site
milisaniyelerle cevap verir.

### API

| Uç nokta | Açıklama |
| --- | --- |
| `GET /api/report` | Son iyi rapor. Asla tarama tetiklemez, her zaman anında döner. |
| `GET /api/status` | Yenileme durumu, veri yaşı, toplama istatistikleri, depo sağlığı. |
| `POST /api/refresh` | Arka planda yenileme başlatır (`202`). 3 dakika bekleme süresi var. |
| `GET /healthz` | Sağlık kontrolü. |
| `GET /api/votes`, `POST /api/vote` | Kullanıcı oyları. |

### Otomatik yenileme

Zamanlayıcı dakikada bir kontrol eder:

1. **Canlı tutma:** 13 dakikada bir kendi `/healthz` adresine istek (Render ücretsiz servisi
   15 dakika sessizlikte uykuya alır).
2. **Akşam yenilemesi:** Türkiye saatiyle 21:00'den sonra günde bir kez.
3. **Emniyet ağı:** rapor 12 saatten eskiyse, akşam yenilemesi kaçırılmış olsa bile yeniler.
   Açılışta veri yoksa veya bayatsa hemen yeniler.

Birden fazla sunucu örneği aynı anda yenileme yapmasın diye veritabanı tabanlı, kalp atışıyla
tazelenen bir kilit kullanılır.

### Önbellek

Tamamlanmış bir arzın detay sayfası ve ilk 45 seansı bir daha değişmez; bunlar kalıcı olarak
önbelleğe alınır. Aktif/yaklaşan arzlar 2–3 saatte bir, son 90 gündekiler günlük tazelenir.
Bu sayede tam tarama ~55 saniyeden ~7 saniyeye iner ve kaynağın bot korumasına takılma riski
düşer.

## Yöntem

Puan, gelecekteki getiri/tavan/taban tahmini değildir. Bir halka arzın **inceleme önceliğini**
gösterir.

| Başlık | Ağırlık | Nasıl kullanılır |
| --- | ---: | --- |
| Finansal dayanıklılık | 30 | Borçluluk, likidite, faiz karşılama, marj ve finansal tablonun güncelliği |
| Arz yapısı ve değerleme | 25 | Sermaye artırımı/ortak satışı, fon kullanımı, halka açıklık, taahhüt, fiyat istikrarı, emsal değerleme |
| Yönetim ve açıklık | 15 | Bağımsız denetim görüşü, belge ve kaynak kalitesi, ilişkili taraf riski |
| Yakın dönem emsal ortamı | 20 | En fazla son 365 gündeki doğrulanmış 5-gün sonuçları; 180 günlük yarı ömürle ağırlıklandırılır |
| Dağıtım ve aracı kurum | 10 | Eşit/oransal dağıtım, bireysel tahsisat, takvim yoğunluğu ve yeterli örneklem varsa aracı kurum kohortu |

Yakın dönem emsal sinyali için en az 6, aracı kurum kohortu için en az 1 kaynaklı gözlem
gerekir. Bu eşik sağlanmazsa ilgili başlık **puanlanmaz** — varsayılan orta puanla
doldurulmaz.

## Veri kaynakları

1. **Resmi belgeler:** KAP, SPK ve SPK onaylı izahname.
2. **İhraççı / aracı kurum:** şirket yatırımcı ilişkileri ve halka arz sayfaları.
3. **Takvim ve arşiv:** `https://halkarz.com/` — tek başına finansal doğrulama değildir.
4. **Fiyat:** Yahoo Finance günlük kapanışları.

Kaynaktan okunan alanlar: tarih, fiyat (aralık dahil), lot, ek pay, dağıtım yöntemi, satış
yöntemi, aracı kurum(lar), pazar, halka açıklık, fiili dolaşım, tahsisat grupları, fon
kullanımı, fiyat istikrarı, satmama taahhüdü, izahname finansal özeti, %5 üstü pay alanlar,
belge bağlantıları ve **gerçekleşen dağıtım tablosu**.

### Arz aşamaları

Arayüz dört aşamayı ayırır:

- **Yaklaşan** — talep toplama başlamadı.
- **Aktif** — talep toplama sürüyor.
- **İşlem bekliyor** — talep toplandı, BIST'te işlem henüz başlamadı. *(Bu aşamadaki arzlar
  önceki sürümde hiçbir listede görünmüyordu.)*
- **Geçmiş** — işlem görüyor, gerçekleşme ölçütleri hesaplandı.

Ayrıca **Sıradakiler** bölümü, SPK sürecinde olup henüz tarihi açıklanmamış ~200 şirketi
listeler.

### Dağıtım verisi

Kişi başına düşen lot artık tahmin edilmiyor, kaynaktan okunuyor:

- **Tamamlanan arzlarda** gerçekleşen dağıtım tablosu (yatırımcı grubu × kişi × lot × oran).
- **Açılmamış arzlarda** kaynağın yayımladığı katılım senaryoları (ör. "500 bin katılırsa
  ~80 lot / 3.848 TL").

## Resmi metrik eklemek

Kaynakta olmayan veya doğrulanması gereken alanları `backend/data/metrics_overrides.json`
dosyasına, kaynak URL'i ile birlikte ekleyin. Bu dosya rapordaki alanların üzerine derin
birleştirme ile yazılır; `financials` dışındaki alanlar da (ör. `participant_count`)
geçersiz kılınabilir.

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

`as_of` güncel değilse güven puanı düşer.

> Eski sürümdeki `custom_ipo_data.json` ve `recent_outcomes.json` kaldırıldı: katılımcı
> sayısı artık doğrudan kaynaktan geliyor, gerçekleşme önbelleği ise veritabanında.

## Sınırlar

- Aracı kurumun geçmişi nedensel başarı ölçüsü değildir; ikincil bir sinyaldir.
- "Max tavan", günlük kapanışlardan türetilen bir **yaklaşıklıktır**; resmî tavan sayısı değildir.
- Fiyat tespit raporundaki iskonto tek başına güvenlik marjı sayılmaz.
- Halka arz şartları değişebileceğinden talep toplamadan önce KAP/SPK belgesi tekrar doğrulanmalıdır.
- Uygulama kişisel portföy, vade, likidite ihtiyacı veya risk toleransı bilmediğinden
  **katıl/katılma** tavsiyesi vermez.
