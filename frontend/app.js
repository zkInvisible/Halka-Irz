/* ═══════════════════════════════════════════════════
   Halka Irz — Frontend Application
   ═══════════════════════════════════════════════════ */

/* ─── State ─── */
const state = {
  report: null,
  status: null,
  draftQuery: '',
  draftsExpanded: false,
  votes: {},
  selectedTicker: null,
  selectedHistoricalTicker: null,
  historyExpanded: false,
  currentTab: 'summary',
  historySortKey: 'date-desc',
  historyQuery: '',
  brokerSortKey: 'score',
  brokerQuery: '',
  offerSortKey: 'date',
  brokerExpandedRow: null,
  charts: {},
};

/* ─── Formatters ─── */
const fmt   = (v) => v == null ? '—' : new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 2 }).format(v);
const fmtPct= (v) => v == null ? '—' : `%${fmt(v)}`;
const fmtScore=(v)=> v == null ? '—' : `${v.toFixed(1)} / 100`;
const cap = (s) => s ? s.replace(/\b\w/g, l => l.toUpperCase()) : s;

/* ─── Color helpers ─── */
const scoreColor = (s) => {
  if (s == null) return 'var(--text-secondary)';
  if (s >= 70)   return 'var(--accent)';
  if (s >= 50)   return 'var(--amber)';
  return 'var(--red)';
};
const returnColor = (p) => {
  if (p == null) return 'var(--text-secondary)';
  if (p >= 10)   return 'var(--accent)';
  if (p >= 0)    return '#8ed6b8';
  if (p >= -10)  return 'var(--amber)';
  return 'var(--red)';
};
const labelClass = (l) => {
  if (!l) return '';
  const lo = l.toLowerCase();
  if (lo.includes('öncelikli')) return 'label-oncelikli';
  if (lo.includes('temkinli'))  return 'label-temkinli';
  if (lo.includes('yüksek'))    return 'label-yuksek-risk';
  return 'label-veri-eksik';
};
const riskBadge = (score, label) => {
  if (score == null) return `<span class="risk-badge risk-badge--gray">⚪ Veri Eksik</span>`;
  if (score >= 70)   return `<span class="risk-badge risk-badge--green">🟢 ${label || 'Öncelikli'}</span>`;
  if (score >= 50)   return `<span class="risk-badge risk-badge--amber">🟡 ${label || 'Temkinli'}</span>`;
  return `<span class="risk-badge risk-badge--red">🔴 ${label || 'Yüksek Risk'}</span>`;
};

/* ─── SVG Gauge ─── */
function scoreGaugeSVG(score, size = 80) {
  const r   = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const pct  = score != null ? score / 100 : 0;
  const off  = circ * (1 - pct);
  const color= scoreColor(score);
  const txt  = score != null ? score.toFixed(1) : '—';
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="rgba(40,55,90,0.5)" stroke-width="5"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none"
      stroke="${color}" stroke-width="5" stroke-linecap="round"
      stroke-dasharray="${circ}" stroke-dashoffset="${off}"
      transform="rotate(-90 ${size/2} ${size/2})"
      style="transition:stroke-dashoffset 0.8s cubic-bezier(0.22,1,0.36,1)"/>
    <text x="50%" y="50%" text-anchor="middle" dominant-baseline="central"
      fill="${color}" font-family="JetBrains Mono,monospace" font-weight="700"
      font-size="${size * 0.22}px">${txt}</text>
  </svg>`;
}

/* ─── API ─── */
async function requestReport() {
  const res = await fetch('/api/report?_t=' + Date.now());
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Rapor alınamadı.');
  return data;
}

async function requestStatus() {
  const res = await fetch('/api/status?_t=' + Date.now());
  if (!res.ok) throw new Error('Durum alınamadı.');
  return res.json();
}

/* Yenileme artık sunucuda arka planda çalışıyor: /api/refresh işi kabul edip
   hemen 202 döner, ilerlemeyi /api/status üzerinden izliyoruz. Eski sürümde
   istek tarama bitene kadar açık kalıyor ve zaman aşımına uğruyordu. */
const STAGE_LABELS = {
  'başlatılıyor': 'Başlatılıyor',
  takvim: 'Takvim okunuyor',
  arsiv: 'Arşiv taranıyor',
  detay: 'Arz detayları',
  fiyat: 'Fiyat geçmişi',
  puanlama: 'Puanlanıyor',
  gecmis: 'Geçmiş hesaplanıyor',
  kaydediliyor: 'Kaydediliyor',
};

function stageText(status) {
  const r = status?.refresh;
  if (!r?.running) return null;
  const label = STAGE_LABELS[r.stage] || r.stage || 'Çalışıyor';
  const d = r.detail || {};
  if (d.toplam) return `${label} ${d.tamamlanan ?? 0}/${d.toplam}`;
  return label;
}

async function startRefresh() {
  const res = await fetch('/api/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 429) return { accepted: false, message: data.error };
  return data;
}

function waitForRefresh(onProgress) {
  return new Promise((resolve) => {
    let idleTicks = 0;
    const timer = setInterval(async () => {
      let status;
      try {
        status = await requestStatus();
      } catch {
        return; // geçici ağ hatası: bir sonraki turda tekrar dene
      }
      if (status.refresh?.running) {
        idleTicks = 0;
        onProgress(stageText(status));
        return;
      }
      // İş parçacığı henüz başlamamış olabilir; birkaç tur tolerans tanı.
      if (++idleTicks < 3) return;
      clearInterval(timer);
      resolve(status);
    }, 1500);
  });
}

async function requestVotes() {
  try {
    const res = await fetch('/api/votes');
    if (res.ok) state.votes = await res.json();
  } catch(e) { console.error('Oylar alınamadı:', e); }
}

async function submitVote(ticker, type) {
  const lsKey = `voted_${ticker}`;
  const currentVote = localStorage.getItem(lsKey);
  
  let actionType = type;
  if (currentVote === type) {
    actionType = `remove_${type}`;
  } else if (currentVote) {
    alert('Önce mevcut oyunuzu geri almalısınız.');
    return;
  }

  try {
    const res = await fetch('/api/vote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, type: actionType })
    });
    if (res.ok) {
      const data = await res.json();
      state.votes[ticker] = { upvotes: data.upvotes, downvotes: data.downvotes };
      if (actionType.startsWith('remove_')) {
        localStorage.removeItem(lsKey);
      } else {
        localStorage.setItem(lsKey, type);
      }
      renderOffers();
    }
  } catch(e) { console.error('Oy gönderilemedi:', e); }
}

/* ─── Hero Stats ─── */
function renderHeroStats() {
  const r  = state.report;
  const ho = r.historical_offers || [];

  // Zirve getirisi: en yüksek kapanış / halka arz fiyatı. Tavan serisinden
  // türetilen eski tahmin, düşen hisseleri kârlı gösteriyordu.
  const peaks = ho.map(o => maxGainPct(o.historical_outcome).pct).filter(v => v != null);
  const avgPeak  = peaks.length ? peaks.reduce((a,b) => a+b, 0) / peaks.length : null;
  const winners  = peaks.filter(v => v > 0).length;

  document.getElementById('hsActiveOffers').textContent = r.offers.length;
  document.getElementById('hsHistorical').textContent   = ho.length;
  document.getElementById('hsAvgReturn').textContent    = avgPeak != null ? `%${fmt(avgPeak)}` : '—';
  document.getElementById('hsPosRate').textContent      = peaks.length
    ? `%${fmt((winners / peaks.length) * 100)}`
    : '—';

  const avgEl = document.getElementById('hsAvgReturn');
  if (avgPeak != null) avgEl.style.color = returnColor(avgPeak);
}

/* ─── Header & veri tazeliği ─── */
function ageText(hours) {
  if (hours == null) return 'bilinmiyor';
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} dk önce`;
  if (hours < 24) return `${Math.round(hours)} saat önce`;
  return `${Math.round(hours / 24)} gün önce`;
}

/* Eski sürüm sabit "Canlı" yazıyordu; sunucu yeniden başladığında günler önceki
   veriyi sunsa bile rozet yeşil kalıyordu. Artık gerçek veri yaşını gösteriyor. */
function renderFreshness() {
  const badge  = document.getElementById('statusBadge');
  const label  = document.getElementById('sourceStatus');
  if (!badge || !label) return;

  const generated = state.status?.generated_at || state.report?.generated_at;
  const hours = state.status?.data_age_hours ??
    (generated ? (Date.now() - new Date(generated).getTime()) / 3600000 : null);

  badge.classList.remove('status--fresh', 'status--stale', 'status--old');
  if (state.status?.refresh?.running) {
    label.textContent = 'Yenileniyor…';
    badge.classList.add('status--stale');
  } else if (hours == null) {
    label.textContent = 'Veri yok';
    badge.classList.add('status--old');
  } else if (hours <= 24) {
    label.textContent = `Güncel · ${ageText(hours)}`;
    badge.classList.add('status--fresh');
  } else {
    label.textContent = `Eski veri · ${ageText(hours)}`;
    badge.classList.add(hours > 72 ? 'status--old' : 'status--stale');
  }
  badge.title = generated
    ? `Rapor üretimi: ${new Date(generated).toLocaleString('tr-TR')}`
    : 'Henüz rapor üretilmedi';
}

function renderHeader() {
  const generated = state.report.generated_at;
  const dateStr = new Date(generated).toLocaleString('tr-TR', {
    day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  const collection = state.report.collection;
  let note = '';
  if (collection) {
    const skipped = collection.gerceklesme?.atlanan;
    note = ` · ${collection.arsiv_kaydi} arşiv kaydı tarandı` + (skipped ? `, ${skipped} arz veri beklemede` : '');
  }
  document.getElementById('generatedAt').textContent = `Son güncelleme: ${dateStr}${note}`;
  renderFreshness();
}

/* ─── Offers ─── */
function getSortedOffers() {
  const offers = [...state.report.offers];
  const key = state.offerSortKey;
  if (key === 'date')       return offers.sort((a,b) => (a.start_date||'').localeCompare(b.start_date||''));
  if (key === 'score-desc') return offers.sort((a,b) => (b.assessment?.evidence_score||0) - (a.assessment?.evidence_score||0));
  if (key === 'score-asc')  return offers.sort((a,b) => (a.assessment?.evidence_score||0) - (b.assessment?.evidence_score||0));
  if (key === 'size-desc')  return offers.sort((a,b) => (b.offer_size_mn_tl||0) - (a.offer_size_mn_tl||0));
  return offers;
}

function renderOffers() {
  const grid     = document.getElementById('offerGrid');
  const template = document.getElementById('offerTemplate');
  grid.replaceChildren();

  const offers   = getSortedOffers();
  const active   = offers.filter(o => o.schedule_status === 'active').length;
  const pending  = offers.filter(o => o.schedule_status === 'pending_listing').length;
  const upcoming = offers.length - active - pending;
  const parts = [];
  if (active)   parts.push(`${active} aktif talep`);
  if (upcoming) parts.push(`${upcoming} yaklaşan arz`);
  if (pending)  parts.push(`${pending} işlem bekliyor`);
  document.getElementById('scheduleHeading').textContent =
    parts.length ? parts.join(' · ') : 'Takvimde kayıtlı arz yok';

  if (!offers.length) {
    grid.innerHTML = '<div class="empty-calendar">Şu an açık veya yaklaşan talep toplama kaydı bulunmuyor.</div>';
    return;
  }

  offers.forEach((offer, i) => {
    const card = template.content.firstElementChild.cloneNode(true);
    const a = offer.assessment;

    card.querySelector('.ticker').textContent  = offer.ticker;
    card.querySelector('.company').textContent = offer.company;
    card.querySelector('.date').textContent    = offer.calendar_date_text;

    const labelEl = card.querySelector('.label');
    if (offer.schedule_status === 'active') {
      labelEl.textContent = 'AKTİF'; labelEl.classList.add('label-active');
    } else if (offer.schedule_status === 'pending_listing') {
      // Talep toplandı ama BIST'te işlem başlamadı. Eski sürümde bu aşamadaki
      // arzlar hiçbir listede görünmüyordu.
      labelEl.textContent = 'İŞLEM BEKLİYOR'; labelEl.classList.add('label-pending');
      labelEl.title = 'Talep toplama tamamlandı, borsada işlem henüz başlamadı.';
    } else {
      labelEl.textContent = 'YAKLAŞAN'; labelEl.classList.add('label-upcoming');
    }

    // broker & distribution badges
    const brokerEl = card.querySelector('.offer-broker');
    const distEl   = card.querySelector('.offer-dist');
    if (offer.broker) brokerEl.textContent = offer.broker.split(' ')[0]; // first word
    else brokerEl.style.display = 'none';
    if (offer.distribution_method) distEl.textContent = offer.distribution_method.replace('**','').trim();
    else distEl.style.display = 'none';

    const scoreEl = card.querySelector('.score');
    scoreEl.textContent   = fmtScore(a.evidence_score);
    scoreEl.style.color   = scoreColor(a.evidence_score);
    card.querySelector('.coverage').textContent = `%${fmt(a.evidence_coverage_pct)} veri`;

    // Voting
    const vState = state.votes[offer.ticker] || { upvotes: 0, downvotes: 0 };
    card.querySelector('.up-count').textContent = vState.upvotes;
    card.querySelector('.down-count').textContent = vState.downvotes;
    
    const lsKey = `voted_${offer.ticker}`;
    const myVote = localStorage.getItem(lsKey);
    const btnUp = card.querySelector('.upvote');
    const btnDown = card.querySelector('.downvote');
    // reset classes and listeners for re-renders
    btnUp.className = 'vote-btn upvote';
    btnDown.className = 'vote-btn downvote';
    
    if (myVote === 'up') btnUp.classList.add('active');
    if (myVote === 'down') btnDown.classList.add('active');
    
    btnUp.onclick = (e) => { e.stopPropagation(); submitVote(offer.ticker, 'up'); };
    btnDown.onclick = (e) => { e.stopPropagation(); submitVote(offer.ticker, 'down'); };

    card.classList.toggle('selected', offer.ticker === state.selectedTicker);
    card.style.animationDelay = `${i * 0.07}s`;

    card.addEventListener('click', () => {
      state.selectedTicker = offer.ticker;
      state.selectedHistoricalTicker = null;
      renderOffers();
      openDrawer(offer, false);
    });
    grid.append(card);
  });
}

/* ─── Dağıtım bloğu ───
   Eski sürüm kişi başına düşen lotu "700 bin kişi katılır" varsayımıyla
   tahmin ediyordu. Kaynak bu bilgiyi kendisi yayımlıyor: tamamlanan arzlarda
   gerçekleşen dağıtım, açılmamış arzlarda farklı katılım senaryoları. */
function participantsLabel(count) {
  if (count == null) return '—';
  if (count >= 1_000_000) return `${fmt(Math.round(count / 100000) / 10)} mn kişi`;
  if (count >= 1000) return `${fmt(Math.round(count / 1000))} bin kişi`;
  return `${fmt(count)} kişi`;
}

/* Eşit dağıtımda toplam lot katılımcı sayısına bölününce çıkan sayı bir
   ORTALAMADIR, "herkesin aldığı" değil: az talep edenlerin payı dolunca kalan
   lot çok talep edenlere yeniden dağıtılır, dolayısıyla büyük başvuru yapan
   ortalamanın belirgin şekilde üzerinde alır. Bunu "kişi başına" diye sunmak
   yanıltıcıydı. */
function renderDistributionBlock(offer) {
  const groups = offer.allocation_groups || [];
  const avgLot = offer.realised_lot_per_person;
  const avgTl  = offer.realised_tl_per_person;

  if (avgLot != null || offer.participant_count != null) {
    const groupRows = groups.map(g => {
      const per = g.people && g.lots ? g.lots / g.people : null;
      const isHigh = /yüksek başvuru/i.test(g.group || '');
      return `
      <div class="dist-row${isHigh ? ' dist-row--high' : ''}">
        <span>${g.group}</span>
        <span>${g.people != null ? fmt(g.people) : '—'} kişi</span>
        <span>${g.lots != null ? fmt(g.lots) : '—'} lot</span>
        <span>${per != null ? 'ort. ' + fmt(Math.round(per)) : '—'}</span>
      </div>`;
    }).join('');

    const high = groups.find(g => /yüksek başvuru/i.test(g.group || ''));
    const highNote = high && high.people && high.lots && offer.ipo_price_tl
      ? `<div class="dist-note">Ayrıca <strong>${fmt(high.people)} kişilik</strong> “Yüksek Başvurulu”
         kademesine kişi başı ortalama <strong>${fmt(Math.round(high.lots / high.people))} lot</strong>
         (${fmt(Math.round((high.lots / high.people) * offer.ipo_price_tl))} ₺) düştü.</div>`
      : '';

    return `
      <div class="dist-card">
        <div class="dist-head">Gerçekleşen Dağıtım</div>
        <div class="dist-hero">
          <div><span>Bireysel katılım</span><strong>${participantsLabel(offer.participant_count)}</strong></div>
          <div><span>Kişi başı ortalama</span><strong class="accent">${avgLot != null ? fmt(avgLot) + ' lot' : '—'}</strong></div>
          <div><span>Ortalama tutar</span><strong>${avgTl != null ? fmt(Math.round(avgTl)) + ' ₺' : '—'}</strong></div>
        </div>
        ${groupRows ? `<div class="dist-table">${groupRows}</div>` : ''}
        ${highNote}
        <small><strong>Bu bir ortalamadır.</strong> Eşit dağıtımda küçük talepler dolduktan sonra
        kalan lotlar büyük taleplere yeniden dağıtıldığı için, yüksek tutarla başvuran yatırımcı
        ortalamanın birkaç katını alabilir. Kaynak yalnızca toplam lot ve kişi sayısını yayımlar;
        kişi bazındaki en yüksek dağıtım açıklanmaz.</small>
      </div>`;
  }

  const scenarios = offer.distribution_scenarios || [];
  if (scenarios.length) {
    const rows = scenarios.map(s => `
      <div class="dist-row dist-row--proj${s.is_likely ? ' dist-row--likely' : ''}">
        <span>${participantsLabel(s.participants)} katılırsa</span>
        <span class="accent">${fmt(s.lot_per_person)} lot</span>
        <span>${fmt(s.tl_per_person)} ₺</span>
      </div>`).join('');
    const band = state.report?.participation_band;
    const bandNote = band?.median
      ? `Son ${band.sample_size} arzda katılım ${participantsLabel(band.min)} – ${participantsLabel(band.max)}
         arasındaydı (medyan ${participantsLabel(band.median)}); işaretli satır bu medyana en yakın olan.`
      : 'Gerçekleşen dağıtım talep yoğunluğuna göre değişir.';
    return `
      <div class="dist-card">
        <div class="dist-head">Olası Dağıtım</div>
        <div class="dist-table">${rows}</div>
        <small>Bireysel yatırımcıya ayrılan ${fmt(offer.retail_lot_pool)} lot, farklı katılım
        sayılarına bölünerek hesaplandı. ${bandNote} Bu değerler <strong>ortalamadır</strong>;
        yüksek tutarla başvuranlar daha fazlasını alabilir.</small>
      </div>`;
  }
  return '';
}

/* ─── Detail Drawer ─── */
function findSimilarOffers(offer) {
  const all = state.report.historical_offers || [];
  if (!all.length) return [];

  const scored = all.map(h => {
    let score = 0;
    // 1. Sector similarity
    if (offer.sector && h.sector) {
      if (offer.sector.toLowerCase() === h.sector.toLowerCase()) score += 50;
      else {
        const os = offer.sector.split(' ')[0].toLowerCase();
        const hs = h.sector.split(' ')[0].toLowerCase();
        if (os && hs && os === hs) score += 20;
      }
    }
    // 2. Size similarity (within 30%)
    if (offer.offer_size_mn_tl && h.offer_size_mn_tl) {
      const diff = Math.abs(offer.offer_size_mn_tl - h.offer_size_mn_tl);
      const pct = diff / Math.max(offer.offer_size_mn_tl, h.offer_size_mn_tl);
      if (pct < 0.2) score += 20;
      else if (pct < 0.4) score += 10;
    }
    // 3. Broker similarity
    if (offer.broker && h.broker) {
      const ob = offer.broker.split(' ')[0].toLowerCase();
      const hb = h.broker.split(' ')[0].toLowerCase();
      if (ob === hb) score += 15;
    }
    return { ...h, simScore: score };
  });

  return scored
    .filter(s => s.historical_outcome && s.simScore >= 40)
    .sort((a,b) => b.simScore - a.simScore)
    .slice(0, 2);
}

function openDrawer(offer, isHistorical) {
  document.getElementById('detailOverlay').classList.add('open');
  document.getElementById('detailDrawer').classList.add('open');
  document.body.style.overflow = 'hidden';
  renderDrawerContent(offer, isHistorical);
  activateTab(state.currentTab);
}

function closeDrawer() {
  document.getElementById('detailOverlay').classList.remove('open');
  document.getElementById('detailDrawer').classList.remove('open');
  document.body.style.overflow = '';
  state.selectedTicker = null;
  state.selectedHistoricalTicker = null;
  renderOffers();
  renderHistory();
}

function activateTab(tabName) {
  state.currentTab = tabName;
  document.querySelectorAll('.dtab').forEach(b => b.classList.toggle('active', b.dataset.tab === tabName));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.dataset.tab === tabName));
}

function renderDrawerContent(offer, isHistorical) {
  const a   = offer.assessment;
  const ho  = offer.historical_outcome;
  const fin = offer.financials || {};

  // Build components HTML
  let compsHtml = (a.components || []).map(c => {
    const known = c.score != null;
    const bp    = known ? c.score : 0;
    const bs    = bp >= 70 ? 'background:linear-gradient(90deg,var(--accent),var(--blue))'
                : bp >= 45 ? 'background:linear-gradient(90deg,var(--amber),#e8a735)'
                           : 'background:linear-gradient(90deg,var(--red),#d44)';
    const notes = (c.notes||[]).map(n => `<small>${n}</small>`).join('');
    return `<div class="component">
      <div class="component-top">
        <span class="component-name">${c.name} <small>(${c.weight}p)</small></span>
        <span class="component-score ${known?'':'unknown'}" style="color:${known?scoreColor(c.score):'var(--amber)'}">
          ${known ? fmtScore(c.score) : 'Veri eksik'}
        </span>
      </div>
      <div class="bar"><i style="width:${bp}%;${known?bs:''}"></i></div>
      <small>Kanıt kapsamı: %${c.coverage_pct}</small>${notes}
    </div>`;
  }).join('');

  const coverage = a.evidence_coverage_pct;
  const knownScore = a.known_data_score;
  const evScore = a.evidence_score;

  if (coverage != null && coverage < 100 && knownScore != null && evScore != null && Math.abs(knownScore - evScore) >= 1) {
    compsHtml += `
      <div style="margin-top:24px;padding:14px;background:rgba(255,190,85,0.08);border:1px dashed rgba(255,190,85,0.3);border-radius:8px;font-size:12.5px;color:var(--text-secondary);line-height:1.5;">
        <strong style="color:var(--amber);font-size:14px;display:block;margin-bottom:6px;">🤔 Puan Ortalaması Neden Farklı?</strong>
        Halka arz belgelerinde eksik veya teyit edilemeyen veriler (Erişilen Veri: %${coverage.toFixed(1)}) "varsayılan olarak iyi" kabul edilmemek adına nötr 50 puana çekilir.<br><br>
        Bulunan güncel verilerin saf ortalaması <strong style="color:var(--text-primary)">${knownScore.toFixed(1)}</strong> iken, bilmediğimiz gizli risk payları nedeniyle şirketin genel güvenilirlik puanı <strong style="color:var(--text-primary)">${evScore.toFixed(1)}</strong> seviyesine temkinli olarak dengelenmiştir.
      </div>
    `;
  }

  // Source links
  const unique = new Map();
  [...(offer.metric_sources||[]), ...(offer.documents||[])].forEach(s => unique.set(s.url, s));
  const srcsHtml = [...unique.values()].map(s =>
    `<a class="source-link" target="_blank" rel="noreferrer" href="${s.url}">${s.name}</a>`
  ).join('') || '<span style="color:var(--text-secondary)">Kaynak bağlantısı henüz bulunmuyor.</span>';

  const debtEq   = fin.net_debt_to_equity;
  const debtEb   = fin.net_debt_to_ebitda;
  const deqCol   = debtEq!=null && debtEq>1 ? 'var(--red)' : debtEq!=null && debtEq>0.6 ? 'var(--amber)' : undefined;
  const debCol   = debtEb!=null && debtEb>4  ? 'var(--red)' : debtEb!=null && debtEb>2.5 ? 'var(--amber)' : undefined;

    const dict = {
      "Net borç/özkaynak": "Şirketin net borcunun özkaynaklarına oranı. 1.0'dan küçük olması tercih edilir.",
      "Net borç/FAVÖK": "Şirketin borcunu mevcut kârlılığıyla kaç yılda ödeyebileceği. 3.0 altı iyidir.",
      "Fiyat istikrarı": "Hisse fiyatı halka arz fiyatının altına düşerse kurumun piyasadan hisse alma taahhüdü.",
      "İskonto": "Hisse fiyatının gerçek değerine göre ne kadar indirimli satıldığı.",
      "Halka açıklık": "Şirketin yüzde kaçının borsada işlem göreceği. %20-30 arası idealdir.",
      "Cari oran": "Şirketin 1 yıl içindeki borçlarını ödeyebilme gücü. 1.5 ve üzeri idealdir."
    };

    const factHtml = (label, val, col) => {
      let lHtml = label;
      for (const [k, v] of Object.entries(dict)) {
        if (label.toLowerCase() === k.toLowerCase()) {
          lHtml = `<span class="tooltip-wrap">${label} <span class="tooltip-icon">?</span><span class="tooltip-text">${v}</span></span>`;
          break;
        }
      }
      return `<div class="fact"><span>${lHtml}</span><strong${col?` style="color:${col}"`:''}>${val||'—'}</strong></div>`;
    };

  // Similar offers for active/upcoming ones
  let similarHtml = '';
  if (!isHistorical) {
    const similar = findSimilarOffers(offer);
    if (similar.length) {
      similarHtml = `
        <div class="report-section" style="margin-top:20px;border-top:1px solid var(--border);padding-top:20px;">
          <h3 style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">Benzer Büyüklük/Sektördeki Geçmiş Arzlar</h3>
          <div style="display:flex;flex-direction:column;gap:8px;">
            ${similar.map(s => {
              const peakPct = maxGainPct(s.historical_outcome).pct;
              const tvn = peakPct != null ? `%${fmt(peakPct)}` : '—';
              return `
                <div style="display:flex;justify-content:space-between;align-items:center;background:rgba(14,20,42,0.4);padding:10px 12px;border-radius:6px;border:1px solid var(--border)">
                  <div>
                    <strong style="color:var(--accent);font-size:13px">${s.ticker}</strong>
                    <span style="font-size:12px;color:var(--text-secondary);margin-left:6px">${s.sector?.split(' ')[0]||''}</span>
                  </div>
                  <div style="text-align:right">
                    <div style="font-size:13px;font-weight:600;color:${returnColor(peakPct)}">${tvn}</div>
                    <div style="font-size:11px;color:var(--text-tertiary)">${fmt(s.offer_size_mn_tl/1000)} Mlr ₺</div>
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;
    }
  }

  // Radar chart data
  const radarLabels = (a.components||[]).map(c => {
    if (!c.name) return '';
    if (c.name.length > 18 && c.name.includes(' ')) {
      const words = c.name.split(' ');
      const half = Math.ceil(words.length / 2);
      return [words.slice(0, half).join(' '), words.slice(half).join(' ')];
    }
    return c.name;
  });
  const radarData   = (a.components||[]).map(c => c.score ?? 0);
  const radarMax    = (a.components||[]).map(c => c.weight ?? 100);

  let hoStrip = '';
  if (ho) {
    const { pct: maxTavanPct, exact: isExact } = maxGainPct(ho);
    const maxTlKar = maxProfitTl(offer);

    hoStrip = `
      <section class="outcome-strip" data-active="${ho.is_streak_active ? 'true' : 'false'}">
        <div><span>Arz Fiyatı</span><strong>${offer.ipo_price_tl ? fmt(offer.ipo_price_tl)+'&nbsp;₺' : '—'}</strong></div>
        <div><span>Max Tavan (%)</span><strong style="color:${returnColor(maxTavanPct)}">${maxTavanPct != null ? (isExact ? '' : '~') + '%'+fmt(maxTavanPct) : '—'}</strong></div>
        ${ho.is_streak_active ? `<div><span>Toplam El Değişim</span><strong style="color:${ho.latest_turnover_pct >= 15 ? 'var(--red)' : 'var(--text-primary)'}">${ho.latest_turnover_pct != null ? '%'+fmt(ho.latest_turnover_pct) : '—'}</strong></div>` : ''}
        <div><span>Max TL Kâr</span><strong style="color:${returnColor(maxTlKar)}">${maxTlKar != null ? '+'+fmt(Math.round(maxTlKar))+'&nbsp;₺' : '—'}</strong></div>
        <div><span>Arz→Bugün</span><strong style="color:${returnColor(ho.return_since_ipo_pct)}">${fmtPct(ho.return_since_ipo_pct)}</strong></div>
        <p>Puan yalnızca arz tarihinden önceki gözlemlerle hesaplanmıştır.</p>
      </section>`;
  }

  const body = document.getElementById('drawerBody');
  body.innerHTML = `
    <!-- Tab: Summary -->
    <div class="tab-pane" data-tab="summary">
      <div class="report-head">
        <div>
          <p class="report-eyebrow">${isHistorical?'GEÇMİŞ ARZ':'GÜNCEL ARZ'} · ${offer.market||'Pazar bilgisi yok'}</p>
          <h2>${offer.company}</h2>
          <p class="report-meta">${offer.calendar_date_text} · ${offer.distribution_method||'Dağıtım bilgisi yok'}</p>
          <div style="margin-top:10px">${riskBadge(a.evidence_score, a.decision_label)}</div>
        </div>
        <div class="assessment">
          <span>KANIT PUANI</span>
          <div class="score-gauge">${scoreGaugeSVG(a.evidence_score, 90)}</div>
          <em class="${labelClass(a.decision_label)}">${a.decision_label}</em>
          <span>Veri: %${fmt(a.evidence_coverage_pct)}</span>
        </div>
      </div>

      ${hoStrip}

      <div class="facts">
        ${factHtml('Halka arz fiyatı', offer.ipo_price_tl ? fmt(offer.ipo_price_tl)+' ₺' : null)}
        ${factHtml('Aracı kurum', offer.broker)}
        ${factHtml('Arz büyüklüğü', offer.offer_size_mn_tl ? fmt(offer.offer_size_mn_tl)+' mn ₺' : null)}
        ${factHtml('Halka açıklık', offer.float_pct!=null ? '%'+fmt(offer.float_pct) : null)}
        ${factHtml('Net borç/özkaynak', debtEq!=null ? fmt(debtEq)+'x' : 'İzahnameden ekle', deqCol)}
        ${factHtml('Net borç/FAVÖK', debtEb!=null ? fmt(debtEb)+'x' : 'İzahnameden ekle', debCol)}
      </div>
      
      ${(() => {
        if (!offer.use_of_proceeds) return '';
        return `
        <div style="margin-top:24px; border:1px solid var(--border); border-radius:12px; background:var(--bg-glass); overflow:hidden;">
          <div style="background:rgba(255,255,255,0.02); padding:12px 16px; border-bottom:1px solid var(--border); font-size:13px; font-weight:700; display:flex; align-items:center; gap:8px;">
            <svg width="16" height="16" fill="none" stroke="var(--accent)" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
            Fon Kullanım Yeri (Faydalanma)
          </div>
          <div style="padding:16px;">
            <div style="font-size:13.5px; color:var(--text-primary); line-height:1.6;">${offer.use_of_proceeds.replace(/ - /g, '<br>• ').replace('Fonun Kullanım Yeri', '').trim()}</div>
          </div>
        </div>
        `;
      })()}      
      ${similarHtml}

      ${renderDistributionBlock(offer)}
    </div>

    <!-- Tab: Score -->
    <div class="tab-pane" data-tab="score">
      <div class="radar-wrap"><canvas id="radarChart" width="280" height="280"></canvas></div>
      <div class="components">${compsHtml}</div>
    </div>

    <!-- Tab: Risk -->
    <div class="tab-pane" data-tab="risk">
      <div class="report-section">
        <h3>Risk İşaretleri</h3>
        ${a.red_flags?.length
          ? `<ul class="flag-list">${a.red_flags.map(f=>`<li>${f}</li>`).join('')}</ul>`
          : '<p style="color:var(--text-secondary)">Otomatik eşiklere göre kritik işaret bulunmadı — bu risk olmadığı anlamına gelmez.</p>'}
      </div>
      <div class="report-section">
        <h3>Belge Kontrol Listesi</h3>
        <ul class="question-list">${(a.review_questions||[]).map(q=>`<li>${q}</li>`).join('')}</ul>
      </div>
    </div>

    <!-- Tab: Docs -->
    <div class="tab-pane" data-tab="docs">
      <div class="report-section">
        <h3>Kaynaklar</h3>
        <div class="sources">${srcsHtml}</div>
      </div>
    </div>
  `;

  activateTab(state.currentTab);

  // Draw radar chart after DOM is updated
  setTimeout(() => {
    const ctx = document.getElementById('radarChart');
    if (!ctx) return;
    if (state.charts.radar) { state.charts.radar.destroy(); }
    state.charts.radar = new Chart(ctx, {
      type: 'radar',
      data: {
        labels: radarLabels,
        datasets: [{
          label: 'Puan',
          data: radarData,
          backgroundColor: 'rgba(69,245,186,0.12)',
          borderColor: 'rgba(69,245,186,0.8)',
          pointBackgroundColor: 'rgba(69,245,186,1)',
          pointRadius: 4,
          borderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        layout: { padding: 5 },
        scales: {
          r: {
            min: 0, max: 100,
            ticks: { display: false },
            grid: { color: 'rgba(56,78,126,0.3)' },
            angleLines: { color: 'rgba(56,78,126,0.3)' },
            pointLabels: { color: '#8494b8', font: { family: 'Inter', size: 10, weight: '600' } },
          }
        },
        plugins: { legend: { display: false } },
      }
    });
  }, 50);
}

/* ─── History ─── */
function getFilteredHistory() {
  let offers = [...(state.report.historical_offers || [])];
  const q = state.historyQuery.toLowerCase().trim();
  if (q) offers = offers.filter(o =>
    o.ticker?.toLowerCase().includes(q) || o.company?.toLowerCase().includes(q)
  );
  const sk = state.historySortKey;
  if (sk === 'date-desc')   offers.sort((a,b) => (b.start_date||'').localeCompare(a.start_date||''));
  if (sk === 'date-asc')    offers.sort((a,b) => (a.start_date||'').localeCompare(b.start_date||''));
  
  if (sk === 'ticker-desc') offers.sort((a,b) => (b.ticker||'').localeCompare(a.ticker||''));
  if (sk === 'ticker-asc')  offers.sort((a,b) => (a.ticker||'').localeCompare(b.ticker||''));

  if (sk === 'company-desc') offers.sort((a,b) => (b.company||'').localeCompare(a.company||''));
  if (sk === 'company-asc')  offers.sort((a,b) => (a.company||'').localeCompare(b.company||''));

  if (sk === 'return-desc') offers.sort((a,b) => (b.historical_outcome?.return_since_ipo_pct??-Infinity)-(a.historical_outcome?.return_since_ipo_pct??-Infinity));
  if (sk === 'return-asc')  offers.sort((a,b) => (a.historical_outcome?.return_since_ipo_pct??-Infinity)-(b.historical_outcome?.return_since_ipo_pct??-Infinity));
  
  if (sk === 'score-desc')  offers.sort((a,b) => (b.assessment?.evidence_score??-1)-(a.assessment?.evidence_score??-1));
  if (sk === 'score-asc')   offers.sort((a,b) => (a.assessment?.evidence_score??-1)-(b.assessment?.evidence_score??-1));

  if (sk === 'size-desc')   offers.sort((a,b) => (b.offer_size_mn_tl??0)-(a.offer_size_mn_tl??0));
  if (sk === 'size-asc')    offers.sort((a,b) => (a.offer_size_mn_tl??0)-(b.offer_size_mn_tl??0));

  if (sk === 'price-desc')  offers.sort((a,b) => (b.historical_outcome?.latest_close_tl??0)-(a.historical_outcome?.latest_close_tl??0));
  if (sk === 'price-asc')   offers.sort((a,b) => (a.historical_outcome?.latest_close_tl??0)-(b.historical_outcome?.latest_close_tl??0));

  const peak = (o) => maxGainPct(o.historical_outcome).pct ?? -Infinity;
  if (sk === 'tavan-desc')  offers.sort((a,b) => peak(b) - peak(a));
  if (sk === 'tavan-asc')   offers.sort((a,b) => peak(a) - peak(b));

  if (sk.startsWith('maxTl')) {
    const getTl = (o) => maxProfitTl(o) ?? -Infinity;
    if (sk === 'maxTl-desc') offers.sort((a,b) => getTl(b) - getTl(a));
    if (sk === 'maxTl-asc')  offers.sort((a,b) => getTl(a) - getTl(b));
  }
  return offers;
}

/* ─── Katılım hesapları ───
   Kaynak artık kişi başına düşen lotu doğrudan yayımlıyor; türetilmiş tahmin
   yalnızca o veri yokken devreye girer. */
function lotPerPerson(offer) {
  if (offer.realised_lot_per_person != null) return offer.realised_lot_per_person;
  if (offer.retail_lots_distributed && offer.participant_count) {
    return offer.retail_lots_distributed / offer.participant_count;
  }
  if (offer.offer_size_mn_tl && offer.retail_allocation_pct && offer.ipo_price_tl && offer.participant_count) {
    const retailTl = offer.offer_size_mn_tl * 1e6 * (offer.retail_allocation_pct / 100);
    return (retailTl / offer.ipo_price_tl) / offer.participant_count;
  }
  return null;
}

function investedTl(offer) {
  if (offer.realised_tl_per_person != null) return offer.realised_tl_per_person;
  const lot = lotPerPerson(offer);
  if (lot != null && lot >= 1 && offer.ipo_price_tl) return Math.floor(lot) * offer.ipo_price_tl;
  return null;
}

/* Zirve getirisi: en yüksek kapanışın halka arz fiyatına oranı.
   Bunu tavan serisinden türetmek yanlış sonuç veriyordu — tabandan açılan bir
   hisse dipten bir gün sıçrayınca "1 tavan" sayılıp +%10 kâr göstermiş oluyordu
   (SSAAT: zirvesi arz fiyatının %10 ALTINDA olmasına rağmen +%10 yazıyordu).
   Yükselmeye devam eden hisselerde ise seri kırıldıktan sonraki artışı
   kaçırıyordu (ISVEA: gerçek %112 iken %77 yazıyordu). */
function maxGainPct(outcome) {
  if (!outcome) return { pct: null, exact: false };
  if (outcome.peak_return_pct != null) {
    return { pct: outcome.peak_return_pct, exact: true, session: outcome.peak_session };
  }
  // Eski raporlarla geriye dönük uyum.
  if (outcome.max_return_15d_pct != null) return { pct: outcome.max_return_15d_pct, exact: true };
  return { pct: null, exact: false };
}

function maxProfitTl(offer) {
  const tl = investedTl(offer);
  const { pct } = maxGainPct(offer.historical_outcome);
  if (tl == null || pct == null) return null;
  return tl * (pct / 100);
}

function renderHistory() {
  const all     = getFilteredHistory();
  const body    = document.getElementById('historyBody');
  const summary = document.getElementById('historySummary');
  body.replaceChildren();

  const visible = state.historyExpanded ? all : all.slice(0, 10);
  const btn = document.getElementById('historyMore');
  btn.hidden = all.length <= 10;
  btn.textContent = state.historyExpanded ? 'Listeyi daralt' : `Tüm ${all.length} geçmiş arzı göster`;

  const allOffers   = state.report.historical_offers || [];
  const outcomes    = allOffers.map(o => o.historical_outcome).filter(Boolean);
  const positives   = outcomes.filter(o => o.max_return_15d_pct != null && o.max_return_15d_pct > 0).length;
  const withData    = outcomes.filter(o => o.max_return_15d_pct != null).length;
  const winRate     = withData > 0 ? ((positives / withData) * 100).toFixed(0) : '—';
  summary.textContent = `${allOffers.length} arz · İlk 15 günde %${winRate} pozitif oran · satıra tıkla`;

  // Yılbaşından bu yana, her arza kişi başı düşen lotla katılmış olsaydınız
  // toplam tepe kâr. Kaynak dağıtım verisi olmayan arzlar hesaba girmez.
  const currentYear = String(new Date().getFullYear());
  let ytdTotal = 0;
  let ytdCount = 0;
  allOffers.forEach(o => {
    if (!o.start_date?.startsWith(currentYear)) return;
    const profit = maxProfitTl(o);
    if (profit != null) { ytdTotal += profit; ytdCount++; }
  });
  const ytdSpan = document.getElementById('ytdTotalProfit');
  if (ytdSpan) {
    ytdSpan.textContent = ytdCount
      ? `${currentYear} tepe kârı: +${fmt(Math.round(ytdTotal))} ₺ (${ytdCount} arz)`
      : `${currentYear} tepe kârı: veri yok`;
  }

  if (!visible.length) {
    body.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:var(--text-secondary)">Sonuç bulunamadı.</td></tr>';
    return;
  }

  visible.forEach(offer => {
    const outcome = offer.historical_outcome || {};
    const row = document.createElement('tr');
    row.dataset.sector = offer.sector || '';
    row.classList.toggle('selected-history', offer.ticker === state.selectedHistoricalTicker);
    row.tabIndex = 0;

    // Katılım & Dağıtım sütunu — kaynağın yayımladığı gerçekleşen dağıtım.
    const lot = lotPerPerson(offer);
    const partLine1 = offer.participant_count != null
      ? participantsLabel(offer.participant_count)
      : '<span style="color:var(--text-tertiary)">Açıklanmadı</span>';
    let partLine2 = '';
    if (lot != null) {
      const tl = investedTl(offer);
      partLine2 = lot >= 1
        ? `ort. ${fmt(Math.floor(lot))} lot${tl != null ? ` · ${fmt(Math.round(tl))} ₺` : ''}`
        : `ort. ${fmt(Math.round(lot * 100) / 100)} lot`;
    }
    const partHTML = `<span style="font-weight:600">${partLine1}</span>${partLine2 ? `<br><small style="color:var(--text-tertiary);font-size:11px">${partLine2}</small>` : ''}`;

    const { pct: maxTavanPct, session: peakSession } = maxGainPct(outcome);
    const streak = outcome.max_limit_up_streak;
    const streakBadge = streak
      ? `<br><small style="color:var(--text-tertiary);font-size:11px">${streak} tavan</small>`
      : '';
    const maxTavanText = maxTavanPct != null
      ? `<span title="${peakSession ? peakSession + '. seansta zirve' : ''}">%${fmt(maxTavanPct)}</span>${streakBadge}`
      : '—';

    const maxTlKar = maxProfitTl(offer);
    const maxTlKarText = maxTlKar != null
      ? `${maxTlKar >= 0 ? '+' : ''}${fmt(Math.round(maxTlKar))}&nbsp;₺`
      : '—';
    
    let warningIcon = '';
    if (outcome.is_streak_active && outcome.latest_turnover_pct >= 15) {
      warningIcon = `<span class="warning-icon" title="Dikkat! Toplam el değiştirme oranı çok yüksek (%${outcome.latest_turnover_pct}), tavan serisi yakında bozulabilir.">🚨</span>`;
    }

    row.innerHTML = `
      <td style="color:var(--accent);font-weight:700;font-family:'JetBrains Mono',monospace">${offer.ticker}${warningIcon}</td>
      <td>${offer.company || '—'}</td>
      <td style="text-align:right;font-weight:600;padding-right:25px;color:var(--text-primary);white-space:nowrap">${outcome.latest_close_tl ? fmt(outcome.latest_close_tl) + '&nbsp;₺' : '—'}</td>
      <td style="text-align:center;font-weight:600;color:var(--text-secondary);padding:0 15px">${offer.offer_size_mn_tl ? fmt(offer.offer_size_mn_tl/1000) : '—'}</td>
      <td style="color:${scoreColor(offer.assessment?.evidence_score)};font-weight:700;padding-left:15px">${fmtScore(offer.assessment?.evidence_score)}</td>
      <td style="font-size:12px;line-height:1.6">${partHTML}</td>
      <td style="color:${returnColor(maxTavanPct)};font-weight:600">${maxTavanText}</td>
      <td style="color:${returnColor(outcome.return_since_ipo_pct)};font-weight:600">${fmtPct(outcome.return_since_ipo_pct)}</td>
      <td style="text-align:right;color:${returnColor(maxTlKar)};font-weight:600;white-space:nowrap">${maxTlKarText}</td>
    `;

    const open = () => {
      state.selectedHistoricalTicker = offer.ticker;
      state.selectedTicker = null;
      renderOffers();
      renderHistory();
      openDrawer(offer, true);
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    body.append(row);
  });

  // Draw return distribution chart
  renderReturnChart();
}

function renderReturnChart() {
  const offers = state.report.historical_offers || [];

  // Gerçek zirve getirisine göre grupla. Eski sürüm tavan serisine göre
  // gruplayıp kutulara "%0, %10, %21…" gibi bileşik etiketler koyuyordu; hiç
  // kâr görmemiş arzlar "%0" kutusunda görünüyor, zarar hiç yansımıyordu.
  const peaks = offers
    .map(o => maxGainPct(o.historical_outcome).pct)
    .filter(v => v != null);

  if (!peaks.length) return;

  const bins = [
    { label: 'Zarar',    min: -Infinity, max: 0,        color: 'rgba(255,107,133,0.7)' },
    { label: '%0–10',    min: 0,         max: 10,       color: 'rgba(255,190,85,0.7)'  },
    { label: '%10–25',   min: 10,        max: 25,       color: 'rgba(255,214,140,0.7)' },
    { label: '%25–50',   min: 25,        max: 50,       color: 'rgba(69,245,186,0.55)' },
    { label: '%50–100',  min: 50,        max: 100,      color: 'rgba(69,245,186,0.8)'  },
    { label: '%100+',    min: 100,       max: Infinity, color: 'rgba(94,175,255,0.8)'  },
  ];
  const counts = bins.map(b => peaks.filter(v => v >= b.min && v < b.max).length);
  const colors = bins.map(b => b.color);

  const ctx = document.getElementById('returnDistChart');
  if (!ctx) return;
  if (state.charts.returnDist) state.charts.returnDist.destroy();
  state.charts.returnDist = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: bins.map(b => b.label),
      datasets: [{ data: counts, backgroundColor: colors, borderRadius: 6, borderWidth: 0 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: {
        callbacks: { label: c => `${c.parsed.y} arz` }
      }},
      scales: {
        x: { grid: { color: 'rgba(56,78,126,0.2)' }, ticks: { color: '#8494b8', font: { size: 11 } } },
        y: { grid: { color: 'rgba(56,78,126,0.2)' }, ticks: { color: '#8494b8', font: { size: 11 }, stepSize: 1 } },
      }
    }
  });
}

/* ─── Brokers ─── */
function getSortedBrokers() {
  let bs = [...(state.report.broker_leaderboard || [])];
  
  const q = state.brokerQuery.toLowerCase().trim();
  if (q) bs = bs.filter(b => b.broker_key?.toLowerCase().includes(q));

  const k = state.brokerSortKey;
  if (k === 'score')    return bs.sort((a,b) => (b.stability_score||0)-(a.stability_score||0));
  if (k === 'sample')   return bs.sort((a,b) => (b.sample_size||0)-(a.sample_size||0));
  if (k === 'return')   return bs.sort((a,b) => (b.weighted_median_return_5d||0)-(a.weighted_median_return_5d||0));
  if (k === 'positive') return bs.sort((a,b) => (b.weighted_positive_rate_pct||0)-(a.weighted_positive_rate_pct||0));
  return bs;
}

function renderBrokers() {
  const body    = document.getElementById('brokerBody');
  const brokers = getSortedBrokers();
  if (!body) return;
  body.replaceChildren();

  if (!brokers.length) {
    body.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--text-secondary)">Yeterli doğrulanmış yakın dönem gözlemi olan aracı kurum henüz yok.</td></tr>';
    return;
  }

  brokers.forEach((broker, idx) => {
    const name     = cap(broker.broker_key);
    const scoreVal = broker.stability_score;
    const barColor = scoreVal >= 70
      ? 'linear-gradient(90deg,var(--accent),var(--blue))'
      : scoreVal >= 45
        ? 'linear-gradient(90deg,var(--amber),#e8a735)'
        : 'linear-gradient(90deg,var(--red),#d44)';

    const maxVals = (broker.recent_offers || []).map(o => o.max_return_15d_pct).filter(v => v != null);
    const avgMaxReturn = maxVals.length ? maxVals.reduce((a, b) => a + b, 0) / maxVals.length : null;

    const row = document.createElement('tr');
    row.dataset.brokerIdx = idx;
    row.innerHTML = `
      <td style="font-weight:700">${name}</td>
      <td>${broker.sample_size}</td>
      <td>${fmtPct(broker.weighted_positive_rate_pct)}</td>
      <td style="color:${returnColor(avgMaxReturn)};font-weight:600">${avgMaxReturn != null ? fmtPct(avgMaxReturn) : '—'}</td>
      <td>
        <div class="mini-bar-wrap">
          <strong style="color:${scoreColor(scoreVal)};min-width:34px;font-size:12px">${scoreVal?.toFixed(1) ?? '—'}</strong>
          <div class="mini-bar"><i style="width:${scoreVal??0}%;background:${barColor}"></i></div>
        </div>
      </td>
    `;

    // Expand/collapse on click
    row.style.cursor = 'pointer';
    row.title = 'Geçmiş arzları görmek için tıklayın';
    row.addEventListener('click', () => toggleBrokerExpand(idx, broker, row));
    body.append(row);
  });
}

function toggleBrokerExpand(idx, broker, row) {
  // Remove existing expand row if any
  const existing = document.querySelector('.broker-expand-row');
  if (existing) {
    const wasIdx = parseInt(existing.dataset.forIdx);
    existing.remove();
    if (wasIdx === idx) return; // toggle off
  }

  state.brokerExpandedRow = idx;
  const expandRow = document.createElement('tr');
  expandRow.className = 'broker-expand-row';
  expandRow.dataset.forIdx = idx;

  const offers = broker.recent_offers || [];
  const offersHtml = offers.length
    ? offers.map(o => `
        <div class="broker-expand-offer">
          <span>${o.ticker}</span>
          <span style="color:${returnColor(o.return_5d_pct)};font-weight:600">${fmtPct(o.return_5d_pct)}</span>
        </div>`).join('')
    : '<span style="color:var(--text-tertiary);font-size:12px">Kayıtlı arz verisi yok.</span>';

  expandRow.innerHTML = `
    <td colspan="5">
      <div class="broker-expand-inner">${offersHtml}</div>
    </td>`;
  row.after(expandRow);
}

/* ─── Taslak arzlar ───
   SPK sürecinde olan, henüz tarihi açıklanmamış şirketler. Kaynakta yayımlanan
   bu ~200 kayıt eski sürümde hiç okunmuyordu. */
function renderDrafts() {
  const grid = document.getElementById('draftGrid');
  const heading = document.getElementById('draftHeading');
  const more = document.getElementById('draftMore');
  if (!grid) return;

  const all = state.report.draft_offers || [];
  const q = state.draftQuery.toLowerCase().trim();
  const filtered = q
    ? all.filter(d => d.company?.toLowerCase().includes(q) || d.ticker?.toLowerCase().includes(q))
    : all;

  heading.textContent = q
    ? `${filtered.length} / ${all.length} şirket`
    : `${all.length} şirket sırada`;

  const visible = state.draftsExpanded || q ? filtered : filtered.slice(0, 24);
  more.hidden = Boolean(q) || filtered.length <= 24;
  more.textContent = state.draftsExpanded ? 'Listeyi daralt' : `Tüm ${filtered.length} taslağı göster`;

  if (!filtered.length) {
    grid.innerHTML = '<div class="empty-calendar">Eşleşen taslak arz bulunamadı.</div>';
    return;
  }

  grid.innerHTML = visible.map(d => `
    <a class="draft-card" href="${d.detail_url}" target="_blank" rel="noreferrer">
      <span class="draft-ticker">${d.ticker || '—'}</span>
      <span class="draft-company">${d.company}</span>
      ${d.sector ? `<span class="draft-sector">${d.sector}</span>` : ''}
    </a>`).join('');
}

/* ─── Master Render ─── */
function render() {
  renderHeader();
  renderHeroStats();
  renderOffers();
  renderHistory();
  renderBrokers();
  renderDrafts();
}

/* ─── Load ─── */
const REFRESH_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`;
const SPIN_ICON = REFRESH_ICON.replace('<svg ', '<svg class="spin" ');

function setButton(text, busy) {
  const button = document.getElementById('refreshButton');
  button.disabled = busy;
  button.innerHTML = `${busy ? SPIN_ICON : REFRESH_ICON} ${text}`;
}

function showError(message, hint) {
  document.getElementById('offerGrid').innerHTML =
    `<div class="error" style="grid-column:1/-1"><strong>Hata:</strong> ${message}${hint ? `<br>${hint}` : ''}</div>`;
}

async function load() {
  setButton('Yükleniyor…', true);
  try {
    const [report] = await Promise.all([requestReport(), requestVotes()]);
    state.report = report;
    render();
    // Sunucu açılışta yenileme başlatmış olabilir; sürüyorsa takip et.
    requestStatus().then(status => {
      state.status = status;
      renderFreshness();
      if (status.refresh?.running) trackBackgroundRefresh();
    }).catch(() => {});
  } catch (err) {
    // Rapor henüz üretilmediyse sunucu 503 + arka planda üretim başlatır.
    showError(err.message, 'Rapor ilk kez üretiliyor olabilir. Birkaç dakika içinde otomatik hazır olur.');
    trackBackgroundRefresh();
  } finally {
    setButton('Yenile', false);
  }
}

async function trackBackgroundRefresh() {
  setButton('Yenileniyor…', true);
  const status = await waitForRefresh(text => setButton(text || 'Yenileniyor…', true));
  state.status = status;
  try {
    state.report = await requestReport();
    await requestVotes();
    render();
  } catch (err) {
    showError(err.message);
  }
  renderFreshness();
  setButton('Yenile', false);
  if (status.refresh?.last_error) {
    console.warn('Yenileme hatası:', status.refresh.last_error);
  }
}

async function manualRefresh() {
  setButton('İstek gönderiliyor…', true);
  let result;
  try {
    result = await startRefresh();
  } catch (err) {
    setButton('Yenile', false);
    alert('Yenileme isteği gönderilemedi: ' + err.message);
    return;
  }
  if (!result.accepted) {
    setButton('Yenile', false);
    alert(result.message || 'Yenileme şu anda başlatılamadı.');
    return;
  }
  await trackBackgroundRefresh();
}

/* ─── Spinning icon CSS (injected) ─── */
const spinStyle = document.createElement('style');
spinStyle.textContent = `
  @keyframes spin { to { transform: rotate(360deg); } }
  .spin { animation: spin 0.9s linear infinite; }
`;
document.head.append(spinStyle);

/* ─── Events ─── */
document.getElementById('refreshButton').addEventListener('click', manualRefresh);

document.getElementById('historyMore').addEventListener('click', () => {
  state.historyExpanded = !state.historyExpanded;
  renderHistory();
});

// Drawer
document.getElementById('drawerClose').addEventListener('click', closeDrawer);
document.getElementById('detailOverlay').addEventListener('click', closeDrawer);

document.querySelectorAll('.dtab').forEach(btn => {
  btn.addEventListener('click', () => activateTab(btn.dataset.tab));
});

// History search & sort
document.getElementById('historySearch').addEventListener('input', e => {
  state.historyQuery = e.target.value;
  renderHistory();
});
document.getElementById('historySortSelect').addEventListener('change', e => {
  state.historySortKey = e.target.value;
  renderHistory();
});

// Offer sort
document.getElementById('offerSortSelect').addEventListener('change', e => {
  state.offerSortKey = e.target.value;
  renderOffers();
});

// Broker sort & search
document.getElementById('brokerSortSelect').addEventListener('change', e => {
  state.brokerSortKey = e.target.value;
  renderBrokers();
});
document.getElementById('brokerSearch').addEventListener('input', e => {
  state.brokerQuery = e.target.value;
  renderBrokers();
});

// Taslak arzlar
document.getElementById('draftSearch')?.addEventListener('input', e => {
  state.draftQuery = e.target.value;
  renderDrafts();
});
document.getElementById('draftMore')?.addEventListener('click', () => {
  state.draftsExpanded = !state.draftsExpanded;
  renderDrafts();
});

// Table column sorting (history)
document.querySelectorAll('.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const s = th.dataset.sort;
    if (s) {
      let newKey = `${s}-desc`;
      if (state.historySortKey === `${s}-desc`) {
        newKey = `${s}-asc`;
      }
      state.historySortKey = newKey;
      renderHistory();
      
      document.querySelectorAll('.sortable').forEach(t => t.classList.remove('sort-asc','sort-desc'));
      th.classList.add(newKey.endsWith('-desc') ? 'sort-desc' : 'sort-asc');
    }
  });
});

// Scroll to top button
const scrollTopBtn = document.getElementById('scrollTop');
window.addEventListener('scroll', () => {
  scrollTopBtn.classList.toggle('visible', window.scrollY > 400);
  document.querySelector('.topbar').classList.toggle('scrolled', window.scrollY > 10);
});
scrollTopBtn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

// Mobile menu
const hamburger = document.getElementById('hamburger');
const mobileNav = document.getElementById('mobileNav');
const mobileOverlay = document.getElementById('mobileNavOverlay');
const closeMobileNav = () => {
  hamburger.classList.remove('open');
  mobileNav.classList.remove('open');
  mobileOverlay.classList.remove('open');
  hamburger.setAttribute('aria-expanded', 'false');
};
hamburger.addEventListener('click', () => {
  const open = !mobileNav.classList.contains('open');
  hamburger.classList.toggle('open', open);
  mobileNav.classList.toggle('open', open);
  mobileOverlay.classList.toggle('open', open);
  hamburger.setAttribute('aria-expanded', open);
});
mobileOverlay.addEventListener('click', closeMobileNav);
document.querySelectorAll('.mobile-nav-link').forEach(a => a.addEventListener('click', closeMobileNav));

// Keyboard: close drawer on Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (document.getElementById('detailDrawer').classList.contains('open')) closeDrawer();
    else closeMobileNav();
  }
});

// Init
load();
