/* ═══════════════════════════════════════════════════
   Halka Irz — Frontend Application
   ═══════════════════════════════════════════════════ */

/* ─── State ─── */
const state = {
  report: null,
  selectedTicker: null,
  selectedHistoricalTicker: null,
  historyExpanded: false,
  currentTab: 'summary',
  historySortKey: 'date-desc',
  historyQuery: '',
  brokerSortKey: 'score',
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
async function requestReport(refresh = false) {
  const ep = refresh ? '/api/refresh' : '/api/report';
  const res = await fetch(ep + (refresh ? '' : '?_t=' + Date.now()), { method: refresh ? 'POST' : 'GET' });
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 429) {
      alert(data.error || "Spam koruması: En fazla 5 dakikada bir veri çekebilirsiniz.");
      return null; // Signals load() to not overwrite state
    }
    throw new Error(data.error || 'Rapor alınamadı.');
  }
  return data;
}

/* ─── Hero Stats ─── */
function renderHeroStats() {
  const r  = state.report;
  const ho = r.historical_offers || [];

  // Max tavan % bileşik getiri
  const tavanPcts = ho
    .map(o => o.historical_outcome?.max_limit_up_streak)
    .filter(v => v != null)
    .map(s => (Math.pow(1.10, s) - 1) * 100);
  const avgTavan = tavanPcts.length ? (tavanPcts.reduce((a,b) => a+b,0) / tavanPcts.length) : null;
  const posTavan = tavanPcts.filter(v => v > 0).length;

  document.getElementById('hsActiveOffers').textContent = r.offers.length;
  document.getElementById('hsHistorical').textContent   = ho.length;
  document.getElementById('hsAvgReturn').textContent    = avgTavan != null ? `%${fmt(avgTavan)}` : '—';
  document.getElementById('hsPosRate').textContent      = tavanPcts.length
    ? `%${fmt((posTavan / tavanPcts.length) * 100)}`
    : '—';

  const avgEl = document.getElementById('hsAvgReturn');
  if (avgTavan != null) avgEl.style.color = returnColor(avgTavan);
}

/* ─── Header ─── */
function renderHeader() {
  const dateStr = new Date(state.report.generated_at).toLocaleString('tr-TR', {
    day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  document.getElementById('generatedAt').textContent   = `Son güncelleme: ${dateStr}`;
  document.getElementById('sourceStatus').textContent  = state.report.source_status === 'live' ? 'Canlı' : 'Önbellek';
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

  const offers = getSortedOffers();
  const active = offers.filter(o => o.schedule_status === 'active').length;
  const h = document.getElementById('scheduleHeading');
  if (active && offers.length - active > 0)
    h.textContent = `${active} aktif talep · ${offers.length - active} yaklaşan arz`;
  else if (active)
    h.textContent = `${active} aktif talep toplama`;
  else
    h.textContent = `${offers.length} yaklaşan arz`;

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
  const compsHtml = (a.components || []).map(c => {
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

  const factHtml = (label, val, col) =>
    `<div class="fact"><span>${label}</span><strong${col?` style="color:${col}"`:''}>${val||'—'}</strong></div>`;

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
              const strk = s.historical_outcome?.max_limit_up_streak;
              const tvn  = strk != null ? `%${fmt((Math.pow(1.10, strk) - 1) * 100)}` : '—';
              return `
                <div style="display:flex;justify-content:space-between;align-items:center;background:rgba(14,20,42,0.4);padding:10px 12px;border-radius:6px;border:1px solid var(--border)">
                  <div>
                    <strong style="color:var(--accent);font-size:13px">${s.ticker}</strong>
                    <span style="font-size:12px;color:var(--text-secondary);margin-left:6px">${s.sector?.split(' ')[0]||''}</span>
                  </div>
                  <div style="text-align:right">
                    <div style="font-size:13px;font-weight:600">${tvn}</div>
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
  const radarLabels = (a.components||[]).map(c => c.name);
  const radarData   = (a.components||[]).map(c => c.score ?? 0);
  const radarMax    = (a.components||[]).map(c => c.weight ?? 100);

  let hoStrip = '';
  if (ho) {
    const streak = ho.max_limit_up_streak;
    const maxTavanPct = streak != null ? (Math.pow(1.10, streak) - 1) * 100 : null;
    
    let maxTlKar = null;
    if (maxTavanPct != null && offer.ipo_price_tl) {
      let lotPerPerson = null;
      if (offer.retail_allocation_tl) {
        lotPerPerson = offer.retail_allocation_tl / offer.ipo_price_tl;
        if (lotPerPerson >= 10000) lotPerPerson = null;
      }
      if (!lotPerPerson && offer.offer_size_mn_tl && offer.retail_allocation_pct && offer.participant_count) {
        const totalRetailTl = (offer.offer_size_mn_tl * 1000000) * (offer.retail_allocation_pct / 100);
        lotPerPerson = (totalRetailTl / offer.ipo_price_tl) / offer.participant_count;
      }
      if (lotPerPerson && lotPerPerson >= 1) {
        const yatirilanTl = Math.floor(lotPerPerson) * offer.ipo_price_tl;
        maxTlKar = yatirilanTl * (maxTavanPct / 100);
      }
    }

    hoStrip = `
      <section class="outcome-strip" data-active="${ho.is_streak_active ? 'true' : 'false'}">
        <div><span>Arz Fiyatı</span><strong style="white-space:nowrap">${offer.ipo_price_tl ? fmt(offer.ipo_price_tl)+'&nbsp;₺' : '—'}</strong></div>
        <div><span>Max Tavan (%)</span><strong style="color:${returnColor(maxTavanPct)}">${maxTavanPct != null ? '%'+fmt(maxTavanPct) : '—'}</strong></div>
        ${ho.is_streak_active ? `<div><span>Toplam El Değişim</span><strong style="color:${ho.latest_turnover_pct >= 15 ? 'var(--red)' : 'var(--text-primary)'}">${ho.latest_turnover_pct != null ? '%'+fmt(ho.latest_turnover_pct) : '—'}</strong></div>` : ''}
        <div><span>Max TL Kâr</span><strong style="color:${returnColor(maxTlKar)};white-space:nowrap">${maxTlKar != null ? '+'+fmt(Math.round(maxTlKar))+'&nbsp;₺' : '—'}</strong></div>
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
      
      ${similarHtml}

      ${(() => {
        if (isHistorical || !offer.offered_lots || !offer.ipo_price_tl) return '';
        const pct = offer.retail_allocation_pct != null ? offer.retail_allocation_pct : 100;
        const lotPerPerson = Math.floor((offer.offered_lots * (pct / 100)) / 700000);
        if (lotPerPerson <= 0) return '';
        
        let rec = lotPerPerson * 1.1;
        let recLot = Math.ceil(rec);
        if (rec > 100) recLot = Math.ceil(rec / 5) * 5;
        else if (rec > 50) recLot = Math.ceil(rec / 2) * 2;
        
        return `
          <div style="background: rgba(69, 245, 186, 0.08); border: 1px solid rgba(69, 245, 186, 0.2); border-radius: 8px; padding: 16px; margin-top: 24px; display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div style="color: var(--text-secondary); font-weight: 800; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px;">💡 Tahmini Katılım (700B Kişi)</div>
              <div style="color: var(--text-primary); font-size: 13px;">Düz hesap +%10 garanti payı ile tavsiye</div>
            </div>
            <div style="text-align: right;">
              <div style="color: var(--accent); font-weight: 800; font-size: 18px;">${recLot} Lot</div>
              <div style="color: var(--text-primary); font-weight: 600; font-size: 14px; margin-top: 2px;">${fmt(Math.round(recLot * offer.ipo_price_tl))} ₺</div>
            </div>
          </div>
        `;
      })()}
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
        scales: {
          r: {
            min: 0, max: 100,
            ticks: { display: false },
            grid: { color: 'rgba(56,78,126,0.3)' },
            angleLines: { color: 'rgba(56,78,126,0.3)' },
            pointLabels: { color: '#8494b8', font: { family: 'Inter', size: 11, weight: '600' } },
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
  if (sk === 'return-desc') offers.sort((a,b) => (b.historical_outcome?.return_since_ipo_pct??-Infinity)-(a.historical_outcome?.return_since_ipo_pct??-Infinity));
  if (sk === 'return-asc')  offers.sort((a,b) => (a.historical_outcome?.return_since_ipo_pct??Infinity)-(b.historical_outcome?.return_since_ipo_pct??Infinity));
  if (sk === 'score-desc')  offers.sort((a,b) => (b.assessment?.evidence_score??-1)-(a.assessment?.evidence_score??-1));
  if (sk === 'size-desc')   offers.sort((a,b) => (b.offer_size_mn_tl??0)-(a.offer_size_mn_tl??0));
  if (sk === 'price-desc')  offers.sort((a,b) => (b.historical_outcome?.latest_close_tl??0)-(a.historical_outcome?.latest_close_tl??0));
  if (sk === 'maxTl-desc')  offers.sort((a,b) => {
    const getTl = (o) => {
      let lot = null;
      if (o.retail_allocation_tl && o.ipo_price_tl) {
        lot = o.retail_allocation_tl / o.ipo_price_tl;
        if (lot >= 10000) lot = null;
      }
      if (!lot && o.offer_size_mn_tl && o.retail_allocation_pct && o.ipo_price_tl && o.participant_count) {
        lot = ((o.offer_size_mn_tl * 1000000) * (o.retail_allocation_pct / 100) / o.ipo_price_tl) / o.participant_count;
      }
      if (!lot || lot < 1 || o.historical_outcome?.max_limit_up_streak == null) return -Infinity;
      const tPct = (Math.pow(1.10, o.historical_outcome.max_limit_up_streak) - 1) * 100;
      return (Math.floor(lot) * o.ipo_price_tl) * (tPct / 100);
    };
    return getTl(b) - getTl(a);
  });
  return offers;
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

  // Calculate YTD Total Max TL Profit
  const currentYear = new Date().getFullYear().toString();
  let ytdTotal = 0;
  allOffers.forEach(o => {
    if (o.start_date && o.start_date.startsWith(currentYear)) {
      let lot = null;
      if (o.retail_allocation_tl && o.ipo_price_tl) {
        lot = o.retail_allocation_tl / o.ipo_price_tl;
        if (lot >= 10000) lot = null;
      }
      if (!lot && o.offer_size_mn_tl && o.retail_allocation_pct && o.ipo_price_tl && o.participant_count) {
        lot = ((o.offer_size_mn_tl * 1000000) * (o.retail_allocation_pct / 100) / o.ipo_price_tl) / o.participant_count;
      }
      if (lot && lot >= 1 && o.historical_outcome?.max_limit_up_streak != null) {
        const tPct = (Math.pow(1.10, o.historical_outcome.max_limit_up_streak) - 1) * 100;
        ytdTotal += (Math.floor(lot) * o.ipo_price_tl) * (tPct / 100);
      }
    }
  });
  const ytdSpan = document.getElementById('ytdTotalProfit');
  if (ytdSpan) {
    ytdSpan.textContent = `Yılbaşından beri max tavan kârı: +${fmt(Math.round(ytdTotal))} ₺`;
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

    // Katılım & Dağıtım sütunu
    let partLine1 = '—'; // kaç kisi
    let partLine2 = '';  // kişi başı düşen lot / TL
    let lotPerPerson = null;

    if (offer.participant_count) {
      const binKisi = (offer.participant_count / 1000).toFixed(0);
      partLine1 = `${binKisi} bin kişi`;
      if (offer.retail_allocation_tl && offer.ipo_price_tl) {
        lotPerPerson = offer.retail_allocation_tl / offer.ipo_price_tl;
        if (lotPerPerson >= 10000) lotPerPerson = null;
      }
      if (!lotPerPerson && offer.offer_size_mn_tl && offer.retail_allocation_pct && offer.ipo_price_tl && offer.participant_count) {
        const totalRetailTl = (offer.offer_size_mn_tl * 1000000) * (offer.retail_allocation_pct / 100);
        lotPerPerson = (totalRetailTl / offer.ipo_price_tl) / offer.participant_count;
      }

      if (lotPerPerson) {
        if (lotPerPerson >= 1) {
          partLine2 = `Kişi başı ~${fmt(Math.floor(lotPerPerson))} Lot`;
        } else {
          partLine2 = `Kişi başı ~${Math.round(lotPerPerson * 100) / 100} Lot`;
        }
      } else if (offer.retail_allocation_tl && offer.retail_allocation_tl < 1000000) {
        partLine2 = `Kişi başı ${fmt(offer.retail_allocation_tl)} ₺`;
      }
    } else {
      // Katılımcı sayısı yoksa sadece Limitsiz yaz (Kullanıcı isteği)
      partLine1 = 'Limitsiz';
      partLine2 = '';
    }
    const partHTML = `<span style="font-weight:600">${partLine1}</span>${partLine2 ? `<br><small style="color:var(--text-tertiary);font-size:11px">${partLine2}</small>` : ''}`;

    // Max tavan % hesapla: bileşik getiri formülü
    const streak = outcome.max_limit_up_streak;
    const maxTavanPct = streak != null ? (Math.pow(1.10, streak) - 1) * 100 : null;
    const maxTavanText = maxTavanPct != null ? `%${fmt(maxTavanPct)}` : '—';

    let maxTlKar = null;
    if (lotPerPerson && lotPerPerson >= 1 && offer.ipo_price_tl && maxTavanPct != null) {
      const yatirilanTl = Math.floor(lotPerPerson) * offer.ipo_price_tl;
      maxTlKar = yatirilanTl * (maxTavanPct / 100);
    }
    const maxTlKarText = maxTlKar != null ? `+${fmt(Math.round(maxTlKar))}&nbsp;₺` : '—';
    
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

  // Max tavan gün (streak)
  const streaks = offers
    .map(o => o.historical_outcome?.max_limit_up_streak)
    .filter(v => v != null);

  if (!streaks.length) return;

  // Buckets by streak directly
  const bins = [
    { label: '%0', min: 0, max: 0.5 },
    { label: '%10', min: 0.5, max: 1.5 },
    { label: '%21', min: 1.5, max: 2.5 },
    { label: '%33', min: 2.5, max: 3.5 },
    { label: '%46', min: 3.5, max: 4.5 },
    { label: '%61', min: 4.5, max: 5.5 },
    { label: '%77-135', min: 5.5, max: 9.5 },
    { label: '%159+', min: 9.5, max: Infinity },
  ];
  const counts = bins.map(b => streaks.filter(v => v >= b.min && v < b.max).length);
  const colors = bins.map(b =>
    b.min === 0    ? 'rgba(255,107,133,0.65)' // kirmizi - tavan yok
  : b.min <= 1.5   ? 'rgba(255,190,85,0.7)'  // sari - az
  : b.min <= 4.5   ? 'rgba(69,245,186,0.65)' // yesil - orta
                   : 'rgba(94,175,255,0.75)' // mavi - cok iyi
  );

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
  const bs = [...(state.report.broker_leaderboard || [])];
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

    const row = document.createElement('tr');
    row.dataset.brokerIdx = idx;
    row.innerHTML = `
      <td style="font-weight:700">${name}</td>
      <td>${broker.sample_size}</td>
      <td>${fmtPct(broker.weighted_positive_rate_pct)}</td>
      <td style="color:${returnColor(broker.weighted_median_return_5d)};font-weight:600">${fmtPct(broker.weighted_median_return_5d)}</td>
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

  renderBrokerChart(brokers);
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

function renderBrokerChart(brokers) {
  const ctx = document.getElementById('brokerBarChart');
  if (!ctx) return;
  if (state.charts.brokerBar) state.charts.brokerBar.destroy();

  const labels = brokers.map(b => cap(b.broker_key));
  const scores = brokers.map(b => b.stability_score ?? 0);
  const colors = scores.map(s =>
    s >= 70 ? 'rgba(69,245,186,0.75)'
  : s >= 45 ? 'rgba(255,190,85,0.75)'
            : 'rgba(255,107,133,0.75)'
  );

  state.charts.brokerBar = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: scores,
        backgroundColor: colors,
        borderRadius: 6, borderWidth: 0,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: {
        callbacks: { label: ctx => `Skor: ${ctx.parsed.x?.toFixed(1)}` }
      }},
      scales: {
        x: { min: 0, max: 100, grid: { color: 'rgba(56,78,126,0.2)' }, ticks: { color: '#8494b8', font: { size: 11 } } },
        y: { grid: { display: false }, ticks: { color: '#e8edf8', font: { size: 12, weight: '600' } } },
      }
    }
  });
}

/* ─── Master Render ─── */
function render() {
  renderHeader();
  renderHeroStats();
  renderOffers();
  renderHistory();
  renderBrokers();
}

/* ─── Load ─── */
async function load(refresh = false) {
  const button = document.getElementById('refreshButton');
  try {
    button.disabled = true;
    button.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" class="spin"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> ${refresh ? 'Yenileniyor…' : 'Yükleniyor…'}`;
    const newReport = await requestReport(refresh);
    if (newReport) {
      state.report = newReport;
      render();
    }
  } catch (err) {
    document.getElementById('offerGrid').innerHTML =
      `<div class="error" style="grid-column:1/-1"><strong>Hata:</strong> ${err.message}<br>
       Yerel sunucuyu <code>python backend/app.py</code> ile başlatıp yeniden dene.</div>`;
  } finally {
    button.disabled = false;
    button.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Yenile`;
  }
}

/* ─── Spinning icon CSS (injected) ─── */
const spinStyle = document.createElement('style');
spinStyle.textContent = `
  @keyframes spin { to { transform: rotate(360deg); } }
  .spin { animation: spin 0.9s linear infinite; }
`;
document.head.append(spinStyle);

/* ─── Events ─── */
document.getElementById('refreshButton').addEventListener('click', () => load(true));

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

// Broker sort
document.getElementById('brokerSortSelect').addEventListener('change', e => {
  state.brokerSortKey = e.target.value;
  renderBrokers();
});

// Table column sorting (history)
document.querySelectorAll('.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const s = th.dataset.sort;
    let newKey;
    if      (s === 'ticker')  newKey = 'date-desc';
    else if (s === 'company') newKey = 'date-desc';
    else if (s === 'score')   newKey = state.historySortKey === 'score-desc' ? 'score-asc' : 'score-desc';
    else if (s === 'return')  newKey = state.historySortKey === 'return-desc' ? 'return-asc' : 'return-desc';
    else if (s === 'size')    newKey = 'size-desc';
    else if (s === 'price')   newKey = 'price-desc';
    else if (s === 'maxTl')   newKey = 'maxTl-desc';
    else if (s === 'tavan')   newKey = 'date-desc';
    if (newKey) { state.historySortKey = newKey; renderHistory(); }

    document.querySelectorAll('.sortable').forEach(t => t.classList.remove('sort-asc','sort-desc'));
    const dir = newKey?.endsWith('-desc') ? 'sort-desc' : 'sort-asc';
    th.classList.add(dir);
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
