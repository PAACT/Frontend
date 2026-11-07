const form = document.getElementById('configForm');
const metricsHost = document.getElementById('metrics');
const historyHost = document.getElementById('historyTable');
const runLog = document.getElementById('runLog');
const runSearchButton = document.getElementById('runSearch');
const runDemoButton = document.getElementById('runDemo');
const resetButton = document.getElementById('resetForm');

const defaultConfig = {
  origin: 'HYD',
  destination: 'DFW',
  adults: 2,
  currency: 'USD',
  threshold: 2200,
  realert_drop: 25,
  searches_per_run: 2,
  check_every_hours: 6,
  exact_stops: 1,
  serpapi_key: '',
  departure_window: { from: '2027-03-17', to: '2027-04-30' },
  trip_length: { min_days: 160, max_days: 165, step_days: 1 },
  alerts: { macos: true }
};

let config = null;

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function setStatus(message) {
  runLog.textContent = message;
}

function applyConfigToForm(cfg) {
  const values = {
    origin: cfg.origin ?? defaultConfig.origin,
    destination: cfg.destination ?? defaultConfig.destination,
    adults: cfg.adults ?? defaultConfig.adults,
    currency: cfg.currency ?? defaultConfig.currency,
    threshold: cfg.threshold ?? defaultConfig.threshold,
    realert_drop: cfg.realert_drop ?? defaultConfig.realert_drop,
    searches_per_run: cfg.searches_per_run ?? defaultConfig.searches_per_run,
    check_every_hours: cfg.check_every_hours ?? defaultConfig.check_every_hours,
    exact_stops: cfg.exact_stops ?? defaultConfig.exact_stops,
    serpapi_key: cfg.serpapi_key ?? '',
    departure_window_from: cfg.departure_window?.from ?? defaultConfig.departure_window.from,
    departure_window_to: cfg.departure_window?.to ?? defaultConfig.departure_window.to,
    trip_length_min_days: cfg.trip_length?.min_days ?? defaultConfig.trip_length.min_days,
    trip_length_max_days: cfg.trip_length?.max_days ?? defaultConfig.trip_length.max_days,
    trip_length_step_days: cfg.trip_length?.step_days ?? defaultConfig.trip_length.step_days,
    alerts_macos: Boolean(cfg.alerts?.macos ?? true)
  };

  for (const [name, value] of Object.entries(values)) {
    const control = form.elements.namedItem(name);
    if (!control) continue;
    if (control.type === 'checkbox') {
      control.checked = Boolean(value);
    } else {
      control.value = value ?? '';
    }
  }
}

function readConfigFromForm() {
  const formData = new FormData(form);
  const cfg = {
    origin: String(formData.get('origin') || defaultConfig.origin).toUpperCase(),
    destination: String(formData.get('destination') || defaultConfig.destination).toUpperCase(),
    adults: Number(formData.get('adults') || defaultConfig.adults),
    currency: String(formData.get('currency') || defaultConfig.currency).toUpperCase(),
    threshold: Number(formData.get('threshold') || defaultConfig.threshold),
    realert_drop: Number(formData.get('realert_drop') || defaultConfig.realert_drop),
    searches_per_run: Number(formData.get('searches_per_run') || defaultConfig.searches_per_run),
    check_every_hours: Number(formData.get('check_every_hours') || defaultConfig.check_every_hours),
    exact_stops: Number(formData.get('exact_stops') || defaultConfig.exact_stops),
    serpapi_key: String(formData.get('serpapi_key') || ''),
    departure_window: {
      from: String(formData.get('departure_window_from') || defaultConfig.departure_window.from),
      to: String(formData.get('departure_window_to') || defaultConfig.departure_window.to)
    },
    trip_length: {
      min_days: Number(formData.get('trip_length_min_days') || defaultConfig.trip_length.min_days),
      max_days: Number(formData.get('trip_length_max_days') || defaultConfig.trip_length.max_days),
      step_days: Number(formData.get('trip_length_step_days') || defaultConfig.trip_length.step_days)
    },
    alerts: {
      macos: Boolean(formData.get('alerts_macos'))
    }
  };

  return cfg;
}

function buildMetric(label, value, detail) {
  return `
    <div class="metric">
      <span class="label">${label}</span>
      <div class="value">${value}</div>
      <div class="muted">${detail}</div>
    </div>
  `;
}

function formatMoney(value, currency = 'USD') {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0
  }).format(Number(value || 0));
}

function renderMetrics(cfg, history) {
  const entries = history.latest ?? history.checks ?? [];
  const prices = entries.map((item) => Number(item.price || 0));
  const cheapest = prices.length ? Math.min(...prices) : 0;
  const average = prices.length ? (prices.reduce((acc, price) => acc + price, 0) / prices.length) : 0;

  metricsHost.innerHTML = [
    buildMetric('Threshold', formatMoney(cfg.threshold, cfg.currency), `Target ${cfg.origin} → ${cfg.destination}`),
    buildMetric('Cheapest', formatMoney(cheapest, cfg.currency), 'Lowest known fare'),
    buildMetric('Average', formatMoney(average, cfg.currency), `${entries.length} tracked dates`),
    buildMetric('Adults', String(cfg.adults), `${cfg.currency} fares`)
  ].join('');
}

function renderHistory(history) {
  const records = [...(history.latest ?? history.checks ?? [])].sort((a, b) => Number(a.price) - Number(b.price));
  const rows = records.slice(0, 10).map((entry) => `
    <div class="row">
      <div>${entry.depart || '-'} → ${entry.return || '-'}</div>
      <div>${formatMoney(entry.price, config?.currency || 'USD')}</div>
      <div>${entry.airlines || 'N/A'}</div>
      <div>${entry.summary || '—'}</div>
    </div>
  `);

  historyHost.innerHTML = `
    <div class="row header">
      <div>Dates</div>
      <div>Price</div>
      <div>Airlines</div>
      <div>Route</div>
    </div>
    ${rows.join('') || '<div class="row"><div>No prices recorded yet.</div></div>'}
  `;
}

async function loadDashboard() {
  try {
    config = await fetchJson('/api/config');
    const history = await fetchJson('/api/history');
    applyConfigToForm(config);
    renderMetrics(config, history);
    renderHistory(history);
    setStatus('Dashboard loaded.');
  } catch (err) {
    setStatus(`Could not load dashboard: ${err.message}`);
  }
}

async function saveConfig(event) {
  event.preventDefault();
  const payload = readConfigFromForm();
  try {
    const result = await fetchJson('/api/config', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    config = result.config;
    applyConfigToForm(config);
    renderMetrics(config, await fetchJson('/api/history'));
    setStatus('Settings saved successfully.');
  } catch (err) {
    setStatus(`Failed to save settings: ${err.message}`);
  }
}

async function runSearch(demo = false) {
  try {
    const params = new URLSearchParams({ demo: String(demo), all: 'false' });
    const result = await fetchJson(`/api/run?${params.toString()}`, { method: 'POST' });
    setStatus(result.stdout || result.stderr || 'No output returned.');
    await loadDashboard();
  } catch (err) {
    setStatus(`Search failed: ${err.message}`);
  }
}

form.addEventListener('submit', saveConfig);
runSearchButton.addEventListener('click', () => runSearch(false));
runDemoButton.addEventListener('click', () => runSearch(true));
resetButton.addEventListener('click', () => applyConfigToForm(defaultConfig));

loadDashboard();
