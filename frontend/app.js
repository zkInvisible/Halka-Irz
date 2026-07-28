/* ═══════════════════════════════════════════════════
   Arz Pusulası — Frontend Application
   ═══════════════════════════════════════════════════ */

const state = {
  report: null,
  selectedTicker: null,
  selectedHistoricalTicker: null,
  historyExpanded: false,
};

/* ─── Formatters ─── */
const formatScore = (v) =>
  v === null || v === undefined ? "—" : `${v.toFixed(1)} / 100`;
const formatNumber = (v) =>
  v === null || v === undefined
    ? "—"
    : new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(v);
const formatPercent = (v) =>
  v === null || v === undefined ? "—" : `%${formatNumber(v)}`;

/* ─── Score colour helpers ─── */
function scoreColor(score) {
  if (score === null || score === undefined) return "var(--text-secondary)";
  if (score >= 70) return "var(--accent)";
  if (score >= 50) return "var(--amber)";
  return "var(--red)";
}

function returnColor(pct) {
  if (pct === null || pct === undefined) return "var(--text-secondary)";
  if (pct >= 10) return "var(--accent)";
  if (pct >= 0) return "#8ed6b8";
  if (pct >= -10) return "var(--amber)";
  return "var(--red)";
}

function labelClass(label) {
  if (!label) return "";
  const l = label.toLowerCase();
  if (l.includes("öncelikli")) return "label-oncelikli";
  if (l.includes("temkinli")) return "label-temkinli";
  if (l.includes("yüksek risk")) return "label-yuksek-risk";
  return "label-veri-eksik";
}

/* ─── SVG Score Gauge ─── */
function scoreGaugeSVG(score, size = 80) {
  const r = (size - 10) / 2;
  const circumference = 2 * Math.PI * r;
  const pct = score !== null && score !== undefined ? score / 100 : 0;
  const offset = circumference * (1 - pct);
  const color = scoreColor(score);
  const text = score !== null && score !== undefined ? score.toFixed(1) : "—";
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="rgba(40,55,90,0.5)" stroke-width="5"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none"
      stroke="${color}" stroke-width="5" stroke-linecap="round"
      stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"
      transform="rotate(-90 ${size / 2} ${size / 2})"
      style="transition: stroke-dashoffset 0.8s cubic-bezier(0.22,1,0.36,1)"/>
    <text x="50%" y="50%" text-anchor="middle" dominant-baseline="central"
      fill="${color}" font-family="JetBrains Mono, monospace" font-weight="700"
      font-size="${size * 0.22}px">${text}</text>
  </svg>`;
}

/* ─── API ─── */
async function requestReport(refresh = false) {
  const endpoint = refresh ? "/api/refresh" : "/api/report";
  const response = await fetch(endpoint + (refresh ? "" : "?_t=" + Date.now()), { method: refresh ? "POST" : "GET" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Rapor alınamadı.");
  return data;
}

/* ─── Header ─── */
function renderHeader(report) {
  const dateStr = new Date(report.generated_at).toLocaleString("tr-TR", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  document.querySelector("#generatedAt").textContent = `Güncelleme: ${dateStr}`;
  document.querySelector("#sourceStatus").textContent =
    report.source_status === "live" ? "Canlı veri" : "Önbellek";
}

/* ─── Offer Cards ─── */
function renderOffers() {
  const grid = document.querySelector("#offerGrid");
  const template = document.querySelector("#offerTemplate");
  grid.replaceChildren();

  const offers = state.report.offers;
  const activeCount = offers.filter((o) => o.schedule_status === "active").length;
  const heading = document.querySelector("#scheduleHeading");

  if (activeCount && offers.length - activeCount > 0) {
    heading.textContent = `${activeCount} aktif talep · ${offers.length - activeCount} yaklaşan arz`;
  } else if (activeCount) {
    heading.textContent = `${activeCount} aktif talep toplama`;
  } else {
    heading.textContent = `${offers.length} yaklaşan arz`;
  }

  if (!offers.length) {
    grid.innerHTML =
      '<div class="empty-calendar">Şu an açık veya yaklaşan talep toplama kaydı bulunmuyor.</div>';
    return;
  }

  offers.forEach((offer, i) => {
    const card = template.content.firstElementChild.cloneNode(true);
    const a = offer.assessment;

    card.querySelector(".ticker").textContent = offer.ticker;

    const labelEl = card.querySelector(".label");
    if (offer.schedule_status === "active") {
      labelEl.textContent = "AKTİF TALEP";
      labelEl.classList.add("label-active");
    } else {
      labelEl.textContent = "YAKLAŞAN";
      labelEl.classList.add("label-upcoming");
    }

    card.querySelector(".company").textContent = offer.company;
    card.querySelector(".date").textContent = offer.calendar_date_text;

    const scoreEl = card.querySelector(".score");
    scoreEl.textContent = formatScore(a.evidence_score);
    scoreEl.style.color = scoreColor(a.evidence_score);

    card.querySelector(".coverage").textContent = `%${formatNumber(a.evidence_coverage_pct)} veri`;

    card.classList.toggle("selected", offer.ticker === state.selectedTicker);
    card.style.animationDelay = `${i * 0.06}s`;

    card.addEventListener("click", () => {
      state.selectedTicker = offer.ticker;
      state.selectedHistoricalTicker = null;
      renderOffers();
      renderHistory();
      renderDetail();
      document.querySelector("#detailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    grid.append(card);
  });
}

/* ─── History Table ─── */
function renderHistory() {
  const offers = state.report.historical_offers || [];
  const body = document.querySelector("#historyBody");
  const summary = document.querySelector("#historySummary");
  body.replaceChildren();

  const visibleOffers = state.historyExpanded ? offers : offers.slice(0, 10);
  const moreBtn = document.querySelector("#historyMore");
  moreBtn.hidden = offers.length <= 10;
  moreBtn.textContent = state.historyExpanded
    ? "Listeyi daralt"
    : `Tüm ${offers.length} geçmiş arzı göster`;

  // Summary stats
  const outcomes = offers.map((o) => o.historical_outcome).filter(Boolean);
  const positives = outcomes.filter(
    (o) => o.max_return_15d_pct != null && o.max_return_15d_pct > 0
  ).length;
  const totalWithData = outcomes.filter((o) => o.max_return_15d_pct != null).length;
  const winRate = totalWithData > 0 ? ((positives / totalWithData) * 100).toFixed(0) : "—";

  summary.textContent = `${offers.length} arz · İlk 15 günde %${winRate} pozitif oran · satıra tıkla`;

  if (!offers.length) {
    body.innerHTML =
      '<tr><td colspan="7">Doğrulanabilir geçmiş gerçekleşme henüz toplanamadı.</td></tr>';
    return;
  }

  visibleOffers.forEach((offer) => {
    const outcome = offer.historical_outcome || {};
    const row = document.createElement("tr");
    row.classList.toggle(
      "selected-history",
      offer.ticker === state.selectedHistoricalTicker
    );
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `${offer.ticker} detaylı rapor`);

    let allocationText = "";
    if (offer.retail_allocation_tl) {
      if (offer.retail_allocation_tl > 10000) {
        allocationText = " / İstendiği kadar";
      } else if (offer.ipo_price_tl) {
        const lot = Math.floor(offer.retail_allocation_tl / offer.ipo_price_tl);
        allocationText = ` / Max ${lot} Lot (${Math.round(offer.retail_allocation_tl)}₺)`;
      } else {
        allocationText = ` / ${Math.round(offer.retail_allocation_tl)}₺`;
      }
    }
    const partText = offer.participant_count ? `${formatNumber(offer.participant_count)} Kişi` + allocationText : "—";

    const columns = [
      { text: offer.ticker, style: `color:var(--accent);font-weight:700;font-family:'JetBrains Mono',monospace` },
      { text: offer.company || "—" },
      { text: formatScore(offer.assessment?.evidence_score), style: `color:${scoreColor(offer.assessment?.evidence_score)};font-weight:700` },
      { text: partText, style: `color:var(--text-secondary);font-weight:600;font-size:12px;` },
      {
        text:
          outcome.max_limit_up_streak != null
            ? `${outcome.max_limit_up_streak} gün`
            : "—",
      },
      { text: formatPercent(outcome.return_since_ipo_pct), style: `color:${returnColor(outcome.return_since_ipo_pct)};font-weight:600` },
    ];

    columns.forEach(({ text, style }) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (style) td.style.cssText = style;
      row.append(td);
    });

    const selectHistory = () => {
      state.selectedHistoricalTicker = offer.ticker;
      state.selectedTicker = null;
      renderOffers();
      renderHistory();
      renderDetail();
      document.querySelector("#detailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    };
    row.addEventListener("click", selectHistory);
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectHistory();
      }
    });
    body.append(row);
  });
}

/* ─── Broker Cards ─── */
function renderBrokers() {
  const grid = document.querySelector("#brokerGrid");
  const brokers = state.report.broker_leaderboard || [];
  grid.replaceChildren();

  if (!brokers.length) {
    grid.innerHTML =
      '<div class="empty-calendar">Yeterli doğrulanmış yakın dönem gözlemi olan aracı kurum henüz yok.</div>';
    return;
  }

  brokers.forEach((broker, i) => {
    const card = document.createElement("article");
    card.className = "broker-card";
    card.style.animationDelay = `${i * 0.06}s`;
    const name = broker.broker_key.replace(/\b\w/g, (l) => l.toUpperCase());
    const scoreVal = broker.stability_score;
    const barColor =
      scoreVal >= 70
        ? "linear-gradient(90deg, var(--accent), var(--blue))"
        : scoreVal >= 45
          ? "linear-gradient(90deg, var(--amber), #e8a735)"
          : "linear-gradient(90deg, var(--red), #d44)";

    card.innerHTML = `
      <div class="broker-card-top">
        <strong>${name}</strong>
        <b style="color:${scoreColor(scoreVal)}">${formatScore(scoreVal)}</b>
      </div>
      <div class="stability-bar"><i style="width:${scoreVal}%;background:${barColor}"></i></div>
      <div class="broker-metrics">
        <span>📊 ${broker.sample_size} arz gözlemi</span>
        <span>✅ %${formatNumber(broker.weighted_positive_rate_pct)} pozitif oran</span>
        <span>📈 %${formatNumber(broker.weighted_median_return_5d)} medyan getiri</span>
      </div>
      <div class="broker-offers" style="display:none; margin-top:12px; font-size:12px; border-top:1px solid var(--border); padding-top:8px;">
        <div style="color:var(--text-secondary); margin-bottom:4px; font-weight:600;">Geçmiş Arzları:</div>
        ${broker.recent_offers ? broker.recent_offers.map(o => 
          `<div style="display:flex; justify-content:space-between; margin-bottom:4px;">
            <span style="color:var(--accent);font-weight:600">${o.ticker}</span>
            <span style="color:${returnColor(o.return_5d_pct)}">${formatPercent(o.return_5d_pct)}</span>
          </div>`
        ).join("") : ""}
      </div>`;
      
    card.style.cursor = "pointer";
    card.title = "Hisseleri görmek için tıklayın";
    card.addEventListener("click", () => {
      const offersDiv = card.querySelector(".broker-offers");
      if (offersDiv.style.display === "none") {
        offersDiv.style.display = "block";
      } else {
        offersDiv.style.display = "none";
      }
    });

    grid.append(card);
  });
}

/* ─── Market Context ─── */
function renderMarket() {
  const market = state.report.market_context.overall;
  const collection = state.report.outcome_collection || {};
  const node = document.querySelector("#marketContext");
  if (!node) return;

  if (!market.is_ready) {
    node.innerHTML = `
      <strong style="color:var(--amber)">Henüz puanlanmıyor</strong>
      <p style="margin-top:8px">Son 365 gün içinde kaynak bağlantılı en az 6 gerçekleşme gerekir.
      Şu an <strong>${market.sample_size}</strong> kayıt var.</p>
      <p style="margin-top:4px;font-size:12px">Toplama: ${collection.mode || "bilinmiyor"}</p>`;
    return;
  }

  const medReturn = market.weighted_median_return_5d;
  const posRate = market.weighted_positive_rate_pct;
  const drawdown = market.weighted_median_drawdown_20d;
  const stddev = market.weighted_stddev_return_5d;

  node.innerHTML = `
    <div class="market-summary-grid">
      <div class="market-stat">
        <span>Medyan 5G getiri</span>
        <strong style="color:${returnColor(medReturn)}">%${formatNumber(medReturn)}</strong>
      </div>
      <div class="market-stat">
        <span>Pozitif oran</span>
        <strong>%${formatNumber(posRate)}</strong>
      </div>
      <div class="market-stat">
        <span>Örneklem</span>
        <strong>${market.sample_size}</strong>
        <em>arz</em>
      </div>
      ${drawdown != null ? `<div class="market-stat">
        <span>Med. Düşüş 20S</span>
        <strong style="color:var(--red)">%${formatNumber(drawdown)}</strong>
      </div>` : ""}
      ${stddev != null ? `<div class="market-stat">
        <span>Std. Sapma</span>
        <strong>%${formatNumber(stddev)}</strong>
      </div>` : ""}
    </div>
    <p style="margin-top:10px;color:var(--text-tertiary);font-size:11px">
      180 gün yarı ömürle ağırlıklandırılmış · Veri: ${collection.mode || "bilinmiyor"}
    </p>`;
}

/* ─── Broker Table ─── */
function renderBrokers() {
  const body = document.querySelector("#brokerBody");
  if (!body) return;
  const brokers = state.report.broker_leaderboard || [];
  body.replaceChildren();

  if (!brokers.length) {
    body.innerHTML =
      '<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--text-secondary)">Yeterli doğrulanmış yakın dönem gözlemi olan aracı kurum henüz yok.</td></tr>';
    return;
  }

  brokers.forEach((broker) => {
    const row = document.createElement("tr");
    const name = broker.broker_key.replace(/\b\w/g, (l) => l.toUpperCase());
    const scoreVal = broker.stability_score;
    const barColor =
      scoreVal >= 70
        ? "linear-gradient(90deg, var(--accent), var(--blue))"
        : scoreVal >= 45
          ? "linear-gradient(90deg, var(--amber), #e8a735)"
          : "linear-gradient(90deg, var(--red), #d44)";

    row.innerHTML = `
      <td style="font-weight:700">${name}</td>
      <td>${broker.sample_size}</td>
      <td>%${formatNumber(broker.weighted_positive_rate_pct)}</td>
      <td style="color:${returnColor(broker.weighted_median_return_5d)};font-weight:600">%${formatNumber(broker.weighted_median_return_5d)}</td>
      <td>
        <div class="mini-bar-wrap">
          <strong style="color:${scoreColor(scoreVal)};min-width:32px;font-size:12px;">${formatScore(scoreVal).split(" ")[0]}</strong>
          <div class="mini-bar"><i style="width:${scoreVal}%;background:${barColor}"></i></div>
        </div>
      </td>
    `;
    body.append(row);
  });
}



/* ─── Fact Card Helper ─── */
function fact(label, value, highlight) {
  const style = highlight ? `style="color:${highlight}"` : "";
  return `<div class="fact"><span>${label}</span><strong ${style}>${value || "—"}</strong></div>`;
}

/* ─── Source Links ─── */
function sourceLinks(offer) {
  const unique = new Map();
  [...(offer.metric_sources || []), ...(offer.documents || [])].forEach((s) =>
    unique.set(s.url, s)
  );
  return [...unique.values()]
    .map(
      (s) =>
        `<a class="source-link" target="_blank" rel="noreferrer" href="${s.url}">${s.name}</a>`
    )
    .join("");
}

/* ─── Detail Panel ─── */
function renderDetail() {
  const currentOffer = state.report.offers.find(
    (i) => i.ticker === state.selectedTicker
  );
  const historicalOffer = state.report.historical_offers?.find(
    (i) => i.ticker === state.selectedHistoricalTicker
  );
  const offer = currentOffer || historicalOffer;
  const node = document.querySelector("#detailPanel");

  if (!offer) {
    node.innerHTML =
      '<div class="empty-state">Bir arz kartı seçerek detaylı inceleme raporunu açın.</div>';
    return;
  }

  const a = offer.assessment;
  const ho = offer.historical_outcome;
  const fin = offer.financials || {};

  // Components
  const components = a.components
    .map((c) => {
      const known = c.score !== null && c.score !== undefined;
      const barPct = known ? c.score : 0;
      const barStyle =
        barPct >= 70
          ? "background:linear-gradient(90deg, var(--accent), var(--blue))"
          : barPct >= 45
            ? "background:linear-gradient(90deg, var(--amber), #e8a735)"
            : "background:linear-gradient(90deg, var(--red), #d44)";
      const notes = c.notes.map((n) => `<small>${n}</small>`).join("");
      return `<div class="component">
        <div class="component-top">
          <span class="component-name">${c.name} <small>(${c.weight}p)</small></span>
          <span class="component-score ${known ? "" : "unknown"}" style="color:${known ? scoreColor(c.score) : "var(--amber)"}">${known ? formatScore(c.score) : "Veri eksik"}</span>
        </div>
        <div class="bar"><i style="width:${barPct}%;${known ? barStyle : ""}"></i></div>
        <small>Kanıt kapsaması: %${c.coverage_pct}</small>${notes}
      </div>`;
    })
    .join("");

  // Decision label styling
  const labelCls = labelClass(a.decision_label);

  // Financial highlights
  const debtEquity = fin.net_debt_to_equity;
  const debtEbitda = fin.net_debt_to_ebitda;
  const debtEquityColor =
    debtEquity != null && debtEquity > 1
      ? "var(--red)"
      : debtEquity != null && debtEquity > 0.6
        ? "var(--amber)"
        : undefined;
  const debtEbitdaColor =
    debtEbitda != null && debtEbitda > 4
      ? "var(--red)"
      : debtEbitda != null && debtEbitda > 2.5
        ? "var(--amber)"
        : undefined;

  node.innerHTML = `
    <div class="report-head">
      <div>
        <p class="eyebrow">${ho ? "GEÇMİŞ ARZ" : "GÜNCEL ARZ"} · ${offer.ticker} · ${offer.market || "Pazar bilgisi yok"}</p>
        <h2>${offer.company}</h2>
        <p class="report-meta">${offer.calendar_date_text} · ${offer.distribution_method || "Dağıtım bilgisi yok"}</p>
      </div>
      <div class="assessment">
        <span>Kanıt puanı</span>
        <div class="score-gauge">${scoreGaugeSVG(a.evidence_score, 90)}</div>
        <em class="${labelCls}">${a.decision_label}</em>
        <span>Veri: %${formatNumber(a.evidence_coverage_pct)}</span>
      </div>
    </div>

    ${ho ? `<section class="outcome-strip">
      <div><span>Arz fiyatı</span><strong>${offer.ipo_price_tl ? `${formatNumber(offer.ipo_price_tl)} TL` : "—"}</strong></div>
      <div><span>15 Günde Max Kâr</span><strong style="color:${returnColor(ho.max_return_15d_pct)}">${formatPercent(ho.max_return_15d_pct)}</strong></div>
      <div><span>Max Tavan</span><strong>${ho.max_limit_up_streak != null ? `${ho.max_limit_up_streak} gün` : "—"}</strong></div>
      <div><span>Arz → Bugün</span><strong style="color:${returnColor(ho.return_since_ipo_pct)}">${formatPercent(ho.return_since_ipo_pct)}</strong></div>
      <p>Puan yalnızca arz tarihinden önceki gözlemlerle hesaplanmıştır. Tavan serisi hesaplaması yaklaşık göstergedir.</p>
    </section>` : ""}

    <div class="facts">
      ${fact("Halka arz fiyatı", offer.ipo_price_tl ? `${formatNumber(offer.ipo_price_tl)} TL` : "—")}
      ${fact("Aracı kurum", offer.broker)}
      ${fact("Arz büyüklüğü", offer.offer_size_mn_tl ? `${formatNumber(offer.offer_size_mn_tl)} mn TL` : "—")}
      ${fact("Halka açıklık", offer.float_pct != null ? `%${formatNumber(offer.float_pct)}` : "—")}
      ${fact("Net borç / özkaynak", debtEquity != null ? `${formatNumber(debtEquity)}x` : "İzahnameden ekle", debtEquityColor)}
      ${fact("Net borç / FAVÖK", debtEbitda != null ? `${formatNumber(debtEbitda)}x` : "İzahnameden ekle", debtEbitdaColor)}
    </div>

    <section class="report-section">
      <h3>Puan bileşenleri</h3>
      <div class="components">${components}</div>
    </section>

    <section class="report-section">
      <h3>Risk işaretleri</h3>
      ${a.red_flags.length ? `<ul class="flag-list">${a.red_flags.map((f) => `<li>${f}</li>`).join("")}</ul>` : '<p style="color:var(--text-secondary)">Otomatik eşiklere göre kritik işaret bulunmadı — bu, risk olmadığı anlamına gelmez.</p>'}
    </section>

    <section class="report-section">
      <h3>Belge kontrol listesi</h3>
      <ul class="question-list">${a.review_questions.map((q) => `<li>${q}</li>`).join("")}</ul>
    </section>

    <section class="report-section">
      <h3>Kaynaklar</h3>
      <div class="sources">${sourceLinks(offer) || '<span style="color:var(--text-secondary)">Kaynak bağlantısı henüz bulunmuyor.</span>'}</div>
    </section>`;
}

/* ─── Master Render ─── */
function render() {
  renderHeader(state.report);
  renderOffers();
  renderMarket();
  renderDetail();
  renderHistory();
  renderBrokers();
}

/* ─── Load ─── */
async function load(refresh = false) {
  const button = document.querySelector("#refreshButton");
  try {
    button.disabled = true;
    button.textContent = refresh ? "Yenileniyor…" : "Yükleniyor…";
    state.report = await requestReport(refresh);
    if (
      !state.selectedTicker &&
      !state.selectedHistoricalTicker &&
      state.report.offers.length
    ) {
      state.selectedTicker = state.report.offers[0].ticker;
    }
    render();
  } catch (error) {
    document.querySelector("#detailPanel").innerHTML = `
      <div class="error">
        <strong>Hata:</strong> ${error.message}<br>
        Yerel sunucuyu <code>python backend/app.py</code> ile başlatıp yeniden dene.
      </div>`;
  } finally {
    button.disabled = false;
    button.textContent = "Veriyi Yenile";
  }
}

/* ─── Events ─── */
document.querySelector("#refreshButton").addEventListener("click", () => load(true));
document.querySelector("#historyMore").addEventListener("click", () => {
  state.historyExpanded = !state.historyExpanded;
  renderHistory();
});
load();
