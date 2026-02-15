(function () {
  'use strict';

  const CHUNK_SIZE = 1000;
  const LAYER_COLORS = {
    'Subgrade': '#238636',
    'Embankment EW': '#8957e5',
  };
  const DEFAULT_COLORS = ['#238636', '#8957e5', '#1f6feb', '#d29922', '#db61a2'];

  let chart = null;

  function parseStretch(stretchStr) {
    if (!stretchStr || typeof stretchStr !== 'string') return null;
    const match = stretchStr.trim().match(/^(\d+)\s*-\s*(\d+)$/);
    if (!match) return null;
    const start = parseInt(match[1], 10);
    const end = parseInt(match[2], 10);
    return start <= end ? { start, end } : null;
  }

  function parseDate(s) {
    if (!s || typeof s !== 'string') return null;
    s = s.trim();
    let m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$/);
    if (m) {
      const d = parseInt(m[1], 10);
      const mo = parseInt(m[2], 10) - 1;
      let y = parseInt(m[3], 10);
      if (y < 100) y += 2000;
      const dt = new Date(y, mo, d);
      return isNaN(dt.getTime()) ? null : dt;
    }
    m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) {
      const dt = new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
      return isNaN(dt.getTime()) ? null : dt;
    }
    return null;
  }

  function findColumn(columns, candidates) {
    const lower = columns.map(c => (c || '').toLowerCase());
    for (const c of candidates) {
      const i = lower.indexOf(c.toLowerCase());
      if (i >= 0) return i;
    }
    return -1;
  }

  function parseCSV(text) {
    const parsed = Papa.parse(text, { header: true, skipEmptyLines: true });
    const rows = parsed.data;
    const cols = parsed.meta.fields || [];
    const itemIdx = findColumn(cols, ['Item', 'Layer', 'item', 'layer']);
    const stretchIdx = findColumn(cols, ['Stretch', 'stretch', 'Chainage', 'chainage']);
    const billIdx = findColumn(cols, ['Bill No', 'Bill', 'bill no', 'bill']);
    const estLengthIdx = findColumn(cols, ['Est Length', 'EstLength', 'est length']);
    const dateIdx = findColumn(cols, ['Date', 'date', 'End Date', 'end date']);

    if (itemIdx < 0 || stretchIdx < 0) {
      throw new Error('CSV must have "Item" (or Layer) and "Stretch" columns');
    }

    let routeExtent = 0;
    const records = [];
    for (const row of rows) {
      const raw = Array.isArray(row) ? row : Object.values(row);
      const item = (raw[itemIdx] || '').toString().trim();
      const stretch = (raw[stretchIdx] || '').toString().trim();
      if (!item || !stretch) continue;
      const span = parseStretch(stretch);
      if (!span) continue;
      const rec = { layer: item, ...span };
      if (billIdx >= 0) {
        const bill = (raw[billIdx] || '').toString().trim();
        if (bill) rec.bill = bill;
      }
      if (estLengthIdx >= 0) {
        const est = parseInt((raw[estLengthIdx] || '').toString().replace(/\D/g, ''), 10);
        if (est > 0) routeExtent = est;
      }
      if (dateIdx >= 0) {
        const dt = parseDate((raw[dateIdx] || '').toString());
        if (dt) rec.month = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`;
      }
      records.push(rec);
    }
    if (!routeExtent && records.length) {
      routeExtent = Math.max(...records.map(r => r.end)) - Math.min(...records.map(r => r.start));
    }
    return { records, routeExtent: routeExtent || 8000 };
  }

  function getChunkKey(start) {
    const chunkStart = Math.floor(start / CHUNK_SIZE) * CHUNK_SIZE;
    return chunkStart;
  }

  function buildStretchSegments(records) {
    const segments = [];
    const layerSet = new Set();
    const chunkSet = new Set();

    for (const r of records) {
      layerSet.add(r.layer);
      const chunkStart = getChunkKey(r.start);
      const chunkEnd = getChunkKey(r.end);

      for (let c = chunkStart; c <= chunkEnd; c += CHUNK_SIZE) {
        chunkSet.add(c);
        const segStart = Math.max(r.start, c);
        const segEnd = Math.min(r.end, c + CHUNK_SIZE);
        if (segStart >= segEnd) continue;

        const relStart = segStart - c;
        const relEnd = segEnd - c;

        segments.push({
          chunkStart: c,
          chunkLabel: `${c}-${c + CHUNK_SIZE}`,
          layer: r.layer,
          relStart,
          relEnd,
          absStart: segStart,
          absEnd: segEnd,
        });
      }
    }

    segments.sort((a, b) => a.chunkStart - b.chunkStart || a.layer.localeCompare(b.layer) || a.relStart - b.relStart);
    const layers = Array.from(layerSet).sort();
    const chunks = Array.from(chunkSet).sort((a, b) => a - b);

    return { segments, layers, chunks };
  }

  function mergeIntervals(intervals) {
    if (!intervals.length) return [];
    const sorted = [...intervals].sort((a, b) => a.start - b.start);
    const merged = [sorted[0]];
    for (let i = 1; i < sorted.length; i++) {
      const cur = sorted[i];
      const last = merged[merged.length - 1];
      if (cur.start <= last.end) {
        last.end = Math.max(last.end, cur.end);
      } else {
        merged.push(cur);
      }
    }
    return merged;
  }

  function findOverlapsWithinLayer(records) {
    const byLayer = new Map();
    for (const r of records) {
      if (!byLayer.has(r.layer)) byLayer.set(r.layer, []);
      byLayer.get(r.layer).push({ start: r.start, end: r.end });
    }
    const result = new Map();
    for (const [layer, intervals] of byLayer) {
      const overlaps = [];
      for (let i = 0; i < intervals.length; i++) {
        for (let j = i + 1; j < intervals.length; j++) {
          const a = intervals[i];
          const b = intervals[j];
          const start = Math.max(a.start, b.start);
          const end = Math.min(a.end, b.end);
          if (start < end) {
            overlaps.push({ start, end, len: end - start });
          }
        }
      }
      result.set(layer, mergeIntervals(overlaps));
    }
    return result;
  }

  function findGapsPerLayer(records) {
    const minStart = Math.min(...records.map(r => r.start));
    const maxEnd = Math.max(...records.map(r => r.end));
    const byLayer = new Map();
    for (const r of records) {
      if (!byLayer.has(r.layer)) byLayer.set(r.layer, []);
      byLayer.get(r.layer).push({ start: r.start, end: r.end });
    }
    const result = new Map();
    for (const [layer, intervals] of byLayer) {
      const merged = mergeIntervals(intervals);
      const gaps = [];
      let pos = minStart;
      for (const seg of merged) {
        if (pos < seg.start) {
          gaps.push({ start: pos, end: seg.start, len: seg.start - pos });
        }
        pos = Math.max(pos, seg.end);
      }
      if (pos < maxEnd) {
        gaps.push({ start: pos, end: maxEnd, len: maxEnd - pos });
      }
      result.set(layer, gaps);
    }
    return result;
  }

  function getColor(layer, index) {
    return LAYER_COLORS[layer] || DEFAULT_COLORS[index % DEFAULT_COLORS.length];
  }

  function renderChart(stretchData) {
    const { segments, layers, chunks } = stretchData;

    const labels = [];
    for (const c of chunks) {
      for (const layer of layers) {
        labels.push(`${c}-${c + CHUNK_SIZE} | ${layer}`);
      }
    }
    const getRowIndex = (chunkStart, layer) => chunks.indexOf(chunkStart) * layers.length + layers.indexOf(layer);
    const layerIdx = new Map(layers.map((l, i) => [l, i]));

    const datasets = segments.map((seg) => {
      const rowIdx = getRowIndex(seg.chunkStart, seg.layer);
      const data = labels.map((_, i) => i === rowIdx ? [seg.relStart, seg.relEnd] : null);
      return {
        segment: seg,
        label: seg.layer,
        data,
        backgroundColor: getColor(seg.layer, layerIdx.get(seg.layer) || 0),
        borderColor: getColor(seg.layer, layerIdx.get(seg.layer) || 0),
        borderWidth: 1,
        barThickness: 'flex',
        minBarLength: 2,
      };
    });

    if (chart) chart.destroy();
    const wrapper = document.querySelector('.chart-wrapper');
    wrapper.style.height = Math.max(360, Math.min(600, labels.length * 28)) + 'px';
    chart = new Chart(document.getElementById('chart'), {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const v = ctx.raw;
                const seg = ctx.dataset.segment;
                if (Array.isArray(v) && seg) {
                  return `${seg.layer}: ${seg.absStart}–${seg.absEnd} m (${v[1] - v[0]} m)`;
                }
                return '';
              },
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: 0,
            max: CHUNK_SIZE,
            title: { display: true, text: 'Chainage (0–1000 m)' },
            grid: { color: 'rgba(48, 54, 61, 0.5)' },
            ticks: { color: '#8b949e' },
          },
          y: {
            title: { display: true, text: 'Chunk (m)' },
            grid: { color: 'rgba(48, 54, 61, 0.5)' },
            ticks: { color: '#8b949e', maxRotation: 0, autoSkip: false },
          },
        },
      },
    });
  }

  function computeProgress(records, routeExtent) {
    const byLayerIntervals = new Map();
    const byBillLayer = new Map();
    for (const r of records) {
      const len = r.end - r.start;
      if (!byLayerIntervals.has(r.layer)) byLayerIntervals.set(r.layer, []);
      byLayerIntervals.get(r.layer).push({ start: r.start, end: r.end });
      if (r.bill) {
        const key = `${r.bill}|${r.layer}`;
        byBillLayer.set(key, (byBillLayer.get(key) || 0) + len);
      }
    }
    const byLayer = new Map();
    for (const [layer, intervals] of byLayerIntervals) {
      const merged = mergeIntervals(intervals);
      const len = merged.reduce((s, i) => s + (i.end - i.start), 0);
      byLayer.set(layer, len);
    }
    const allIntervals = records.map(r => ({ start: r.start, end: r.end }));
    const merged = mergeIntervals(allIntervals);
    const overallLen = merged.reduce((s, i) => s + (i.end - i.start), 0);
    const overallPct = routeExtent ? (overallLen / routeExtent * 100) : 0;
    const perLayer = new Map();
    for (const [layer, len] of byLayer) {
      perLayer.set(layer, { len, pct: routeExtent ? (len / routeExtent * 100) : 0 });
    }
    const perLayerPerBill = [];
    for (const [key, len] of byBillLayer) {
      const [bill, layer] = key.split('|');
      perLayerPerBill.push({ bill, layer, len, pct: routeExtent ? (len / routeExtent * 100) : 0 });
    }
    perLayerPerBill.sort((a, b) => a.bill.localeCompare(b.bill) || a.layer.localeCompare(b.layer));
    return { routeExtent, overallLen, overallPct, perLayer, perLayerPerBill };
  }

  function filterRecords(records, filterBills, filterMonths) {
    return records.filter(r => {
      if (filterBills.length && r.bill && !filterBills.includes(r.bill)) return false;
      if (filterMonths.length && r.month && !filterMonths.includes(r.month)) return false;
      return true;
    });
  }

  function renderProgressSection(records, routeExtent, layers, filterState = { bills: [], months: [] }) {
    const section = document.getElementById('progressSection');
    if (!section) return;
    const filtered = filterRecords(records, filterState.bills, filterState.months);
    const progressData = computeProgress(filtered, routeExtent);
    const { overallLen, overallPct, perLayer, perLayerPerBill } = progressData;

    const bills = [...new Set(records.map(r => r.bill).filter(Boolean))].sort();
    const months = [...new Set(records.map(r => r.month).filter(Boolean))].sort();

    const hasFilters = bills.length > 0 || months.length > 0;
    let html = `
      <h2>Progress</h2>
      ${hasFilters ? `
      <div class="progress-filters">
        ${bills.length ? `
        <div class="filter-group">
          <label>Bill No</label>
          <select id="filterBill" multiple>
            ${bills.map(b => `<option value="${b}" ${filterState.bills.includes(b) ? 'selected' : ''}>${b}</option>`).join('')}
          </select>
          <small>Hold Ctrl/Cmd to multi-select</small>
        </div>
        ` : ''}
        ${months.length ? `
        <div class="filter-group">
          <label>Month</label>
          <select id="filterMonth" multiple>
            ${months.map(m => {
              const [, mo] = m.split('-');
              const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
              const label = `${names[parseInt(mo,10)-1]} ${m.split('-')[0]}`;
              return `<option value="${m}" ${filterState.months.includes(m) ? 'selected' : ''}>${label}</option>`;
            }).join('')}
          </select>
          <small>Hold Ctrl/Cmd to multi-select</small>
        </div>
        ` : ''}
        <button type="button" id="applyProgressFilter" class="filter-btn">Apply</button>
      </div>
      <p class="filter-hint">${filtered.length} of ${records.length} records</p>
      ` : ''}
      <div class="progress-overall">
        <div class="progress-header">
          <span>Overall</span>
          <span>${overallLen.toFixed(0)} m / ${routeExtent} m · ${overallPct.toFixed(1)}%</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width: ${Math.min(100, overallPct)}%"></div></div>
      </div>
      <div class="progress-per-layer">
        <h3>Per layer</h3>
        ${layers.map((layer, i) => {
          const d = perLayer.get(layer) || { len: 0, pct: 0 };
          return `
            <div class="progress-row">
              <span class="progress-label" style="border-left-color: ${getColor(layer, i)}">${layer}</span>
              <span class="progress-value">${d.pct.toFixed(1)}%</span>
              <div class="progress-bar small"><div class="progress-fill" style="width: ${Math.min(100, d.pct)}%"></div></div>
            </div>
          `;
        }).join('')}
      </div>
    `;
    if (perLayerPerBill.length) {
      const byBill = new Map();
      for (const p of perLayerPerBill) {
        if (!byBill.has(p.bill)) byBill.set(p.bill, []);
        byBill.get(p.bill).push(p);
      }
      html += `<div class="progress-per-bill"><h3>Per layer in each bill</h3>`;
      for (const [bill, items] of byBill) {
        html += `<div class="progress-bill-group"><h4>${bill}</h4>`;
        for (const p of items) {
          const i = layers.indexOf(p.layer);
          const color = getColor(p.layer, i >= 0 ? i : 0);
          html += `
            <div class="progress-row">
              <span class="progress-label" style="border-left-color: ${color}">${p.layer}</span>
              <span class="progress-value">${p.pct.toFixed(1)}%</span>
              <div class="progress-bar small"><div class="progress-fill" style="width: ${Math.min(100, p.pct)}%"></div></div>
            </div>
          `;
        }
        html += `</div>`;
      }
      html += `</div>`;
    }
    section.innerHTML = html;
    section.style.display = 'block';

    const billSel = document.getElementById('filterBill');
    const monthSel = document.getElementById('filterMonth');
    const applyBtn = document.getElementById('applyProgressFilter');
    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        const selBills = billSel ? [...billSel.selectedOptions].map(o => o.value).filter(Boolean) : [];
        const selMonths = monthSel ? [...monthSel.selectedOptions].map(o => o.value).filter(Boolean) : [];
        progressFilterState = { bills: selBills, months: selMonths };
        renderProgressSection(records, routeExtent, layers, progressFilterState);
      });
    }
  }

  let progressFilterState = { bills: [], months: [] };

  function renderLegend(layers) {
    const html = layers.map((l, i) => `
      <div class="legend-item">
        <span style="background: ${getColor(l, i)}"></span>
        <span>${l}</span>
      </div>
    `).join('');
    document.getElementById('legend').innerHTML = html;
  }

  function renderOverlapAndGapAnalysis(layers, overlapsWithinLayer, gapsByLayer) {
    const container = document.getElementById('analysisSection');
    container.innerHTML = layers.map((layer, i) => {
      const withinOverlaps = overlapsWithinLayer.get(layer) || [];
      const gaps = gapsByLayer.get(layer) || [];
      const color = getColor(layer, i);
      return `
        <div class="panel layer-panel" style="border-left: 3px solid ${color}">
          <h2>${layer}</h2>
          <div class="layer-subsection">
            <h3>Overlaps within layer <span class="badge within-overlap-badge">${withinOverlaps.length}</span></h3>
            <ul class="stretch-list">
              ${withinOverlaps.length ? withinOverlaps.map(o => `
                <li><span class="range">${o.start}–${o.end} m</span><span class="length">${o.len} m</span></li>
              `).join('') : '<li class="empty-state">None</li>'}
            </ul>
          </div>
          <div class="layer-subsection">
            <h3>Gaps <span class="badge gap-badge">${gaps.length}</span></h3>
            <ul class="stretch-list">
              ${gaps.length ? gaps.map(g => `
                <li><span class="range">${g.start}–${g.end} m</span><span class="length">${g.len} m</span></li>
              `).join('') : '<li class="empty-state">None</li>'}
            </ul>
          </div>
        </div>
      `;
    }).join('');
    container.style.display = 'grid';
  }

  function processFile(file) {
    const loading = document.getElementById('loading');
    loading.style.display = 'block';
    document.getElementById('chartSection').style.display = 'none';
    document.getElementById('analysisSection').style.display = 'none';

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const { records, routeExtent } = parseCSV(e.target.result);
        if (!records.length) {
          alert('No valid records found. Ensure Stretch uses format like 500-1000.');
          loading.style.display = 'none';
          return;
        }

        const stretchData = buildStretchSegments(records);
        const overlapsWithinLayer = findOverlapsWithinLayer(records);
        const gapsByLayer = findGapsPerLayer(records);
        progressFilterState = { bills: [], months: [] };
        renderChart(stretchData);
        renderLegend(stretchData.layers);
        renderProgressSection(records, routeExtent, stretchData.layers);
        renderOverlapAndGapAnalysis(stretchData.layers, overlapsWithinLayer, gapsByLayer);

        document.getElementById('chartSection').style.display = 'block';
      } catch (err) {
        alert('Error: ' + (err.message || 'Failed to process file'));
      }
      loading.style.display = 'none';
    };
    reader.readAsText(file, 'UTF-8');
  }

  const uploadZone = document.getElementById('uploadZone');
  const fileInput = document.getElementById('fileInput');

  uploadZone.addEventListener('click', () => fileInput.click());
  uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
  });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    const file = e.dataTransfer?.files?.[0];
    if (file?.name?.toLowerCase().endsWith('.csv')) processFile(file);
    else alert('Please drop a CSV file.');
  });
  fileInput.addEventListener('change', () => {
    const file = fileInput.files?.[0];
    if (file) processFile(file);
  });

  document.getElementById('loadSample').addEventListener('click', (e) => {
    e.preventDefault();
    fetch('emb%20and%20subgrade.csv')
      .then(r => r.ok ? r.text() : Promise.reject(null))
      .then(text => {
        const blob = new Blob([text], { type: 'text/csv' });
        processFile(new File([blob], 'emb and subgrade.csv', { type: 'text/csv' }));
      })
      .catch(() => {
        const sample = `Item,Stretch
Subgrade,100-150
Subgrade,600-800
Subgrade,1400-1600
Embankment EW,100-150
Embankment EW,600-800
Embankment EW,1400-1600`;
        const blob = new Blob([sample], { type: 'text/csv' });
        processFile(new File([blob], 'sample.csv', { type: 'text/csv' }));
      });
  });
})();
