const form = document.querySelector('#finance-form');
const emptyState = document.querySelector('#finance-empty');
const results = document.querySelector('#finance-results');
const sourceSummary = document.querySelector('#source-summary');
const reviewWarning = document.querySelector('#analysis-review-warning');
const unitsNode = document.querySelector('#result-units');
const metricsOutput = document.querySelector('#metrics-output');
const limitationsOutput = document.querySelector('#limitations-output');
const sourceKindField = form.querySelector('input[name="source_kind"]');
const sourceUrlField = form.querySelector('input[name="source_url"]');
const modelAssumptionsForm = document.querySelector('#model-assumptions-form');
const modelError = document.querySelector('#model-error');
const modelResults = document.querySelector('#model-results');
const historyImportForm = document.querySelector('#history-import-form');
const historyImportError = document.querySelector('#history-import-error');
const historyResults = document.querySelector('#history-results');
const historyCompanySelect = document.querySelector('#history-company-select');
let importedHistory = null;
let lastStatementPayload = null;
let currentModel = null;
let selectedScenario = 'base';
const labels = {
  revenue_growth_percent: 'Revenue growth',
  gross_margin_percent: 'Gross margin',
  operating_margin_percent: 'Operating margin',
  net_margin_percent: 'Net margin',
  working_capital: 'Working capital',
  current_ratio: 'Current ratio',
  quick_ratio: 'Quick ratio',
  debt_to_equity_ratio: 'Debt-to-equity',
  ebitda: 'EBITDA',
  ebitda_margin_percent: 'EBITDA margin',
  net_debt: 'Net debt',
  interest_coverage_ratio: 'Interest coverage',
  return_on_assets_percent: 'Return on assets',
  return_on_equity_percent: 'Return on equity',
  asset_turnover_ratio: 'Asset turnover',
  inventory_turnover_ratio: 'Inventory turnover',
  inventory_days: 'Inventory days',
  receivables_days: 'Receivables days',
  payables_days: 'Payables days',
  working_capital_days: 'Working-capital days',
  cash_conversion_cycle_days: 'Cash conversion cycle',
  operating_cash_flow_to_net_income_ratio: 'Operating cash flow / net income',
  free_cash_flow: 'Free cash flow',
  cash_flow_category_subtotal: 'Cash-flow category subtotal'
};
const amountNames = [
  'revenue', 'prior_revenue', 'gross_profit', 'operating_profit', 'net_income',
  'current_assets', 'current_liabilities', 'inventory', 'total_debt', 'total_equity',
  'operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow', 'cost_of_revenue',
  'average_total_assets', 'average_total_equity', 'average_inventory', 'average_receivables',
  'average_payables', 'capital_expenditure', 'cash_and_equivalents', 'noncurrent_assets',
  'other_liabilities', 'depreciation', 'interest_expense', 'tax_expense'
];

function addText(parent, tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  parent.append(element);
  return element;
}

function renderSource(meta) {
  sourceSummary.replaceChildren();
  reviewWarning.replaceChildren();
  if (meta.source_kind === 'ILLUSTRATIVE_SAMPLE') {
    addText(sourceSummary, 'strong', 'Meridian Consumer Products — fictional sample');
    addText(sourceSummary, 'span', 'Synthetic figures only · no external filing or source · not publishable as reported facts');
    addText(reviewWarning, 'strong', 'Illustrative only.');
    reviewWarning.append(document.createTextNode(' These synthetic values do not describe a real company or source and must not be used as reported facts.'));
  } else {
    const source = document.createElement('a');
    source.href = meta.source_url;
    source.rel = 'noopener noreferrer';
    source.target = '_blank';
    source.textContent = meta.source_url;
    sourceSummary.append(source);
    addText(reviewWarning, 'strong', 'Not reviewed.');
    reviewWarning.append(document.createTextNode(' This calculator has not independently checked the source or the values. Verify figures, definitions, period, and accounting classification before using or publishing results.'));
  }
  addText(sourceSummary, 'span', `Period: ${meta.period}${meta.prior_period ? ` · Prior comparison: ${meta.prior_period}` : ''} · Currency: ${meta.currency} · Source units: ${meta.unit}`);
  if (meta.source_kind === 'ILLUSTRATIVE_SAMPLE') {
    addText(sourceSummary, 'span', `Status: illustrative only · no source was retrieved · normalized calculation units: ${meta.calculation_unit}`);
  } else {
    addText(sourceSummary, 'span', `Publication date: ${meta.publication_date || 'Not supplied'} · Retrieved: ${meta.retrieved_at}`);
    addText(sourceSummary, 'span', `Status: ${meta.review_status} · normalized calculation units: ${meta.calculation_unit}`);
  }
}

function renderMetrics(metrics, metadata) {
  metricsOutput.replaceChildren();
  const entries = Object.entries(metrics);
  if (!entries.length) {
    addText(metricsOutput, 'p', 'No complete metric inputs were supplied. Enter the required figures listed in the limitation notes.', 'no-metrics');
    return;
  }
  for (const [key, metric] of entries) {
    const card = document.createElement('article');
    card.className = 'metric-result';
    addText(card, 'h3', labels[key] || key.replaceAll('_', ' '));
    addText(card, 'strong', `${metric.value_display}${key.endsWith('_percent') ? '%' : ''}`, 'metric-value');
    addText(card, 'p', `Formula: ${metric.formula}`, 'metric-formula');
    const trace = Object.entries(metric.inputs).map(([name, value]) => `${name.replaceAll('_', ' ')}=${value} ${metadata.calculation_unit}`).join(' · ');
    addText(card, 'p', `Inputs: ${trace}`, 'metric-trace');
    card.title = `Decimal result: ${metric.value_decimal}`;
    metricsOutput.append(card);
  }
}

function renderMetricStates(states) {
  const list = document.querySelector('#metric-states-output');
  list.replaceChildren();
  for (const [metricId, state] of Object.entries(states || {})) {
    const item = document.createElement('li');
    item.className = `metric-state metric-state-${state.toLowerCase()}`;
    addText(item, 'strong', labels[metricId] || metricId.replaceAll('_', ' '));
    addText(item, 'span', state.replaceAll('_', ' '));
    list.append(item);
  }
}

function renderLimitations(items) {
  limitationsOutput.replaceChildren();
  if (!items.length) {
    addText(limitationsOutput, 'li', 'No calculation-specific limitations detected. Source review is still required.');
    return;
  }
  items.forEach(item => addText(limitationsOutput, 'li', item));
}

function prepareModelForm(defaults, error) {
  const status = document.querySelector('#model-input-status');
  const button = document.querySelector('#run-model');
  const version = document.querySelector('#model-version');
  if (!defaults) {
    modelAssumptionsForm.hidden = true;
    button.disabled = true;
    status.textContent = error || 'The selected statement is missing model inputs. Add a complete, balanced annual balance sheet to enable forecasts.';
    version.textContent = 'Model unavailable';
    return;
  }
  for (const [name, value] of Object.entries(defaults.values)) {
    const field = modelAssumptionsForm.elements.namedItem(name);
    if (field) field.value = value;
    const origin = modelAssumptionsForm.querySelector(`[data-origin="${name}"]`);
    if (origin) origin.textContent = defaults.origins[name] || 'Editable assumption.';
  }
  document.querySelector('#model-debt-unit').textContent = `${defaults.currency} ${defaults.unit}`;
  document.querySelector('#model-assumptions-form [name="forecast_years"]').value = '5';
  modelAssumptionsForm.hidden = false;
  button.disabled = false;
  status.textContent = `Starting points from ${defaults.source_period}; review and edit every assumption before generating scenarios.`;
  version.textContent = `Ready · ${defaults.model_version}`;
  modelResults.hidden = true;
}

function modelField(record, name) {
  const item = record.balance_sheet?.[name] || record.income_statement?.[name] || record.cash_flow_statement?.[name];
  return item?.value_display ?? '—';
}

function renderScenarioDetails(name) {
  const scenario = currentModel?.scenarios?.[name];
  const details = document.querySelector('#scenario-details');
  details.replaceChildren();
  if (!scenario) return;
  const heading = document.createElement('div');
  heading.className = 'scenario-detail-heading';
  addText(heading, 'h3', `${name[0].toUpperCase()}${name.slice(1)} case · ${scenario.length}-year projection`);
  addText(heading, 'span', `${currentModel.currency} ${currentModel.unit} · ${currentModel.model_version}`);
  details.append(heading);
  const tableWrap = document.createElement('div');
  tableWrap.className = 'table-wrap model-table-wrap';
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['Line item', ...scenario.map(item => item.period)]) addText(headRow, 'th', label);
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement('tbody');
  const rows = [
    ['Revenue', 'income_statement', 'revenue'],
    ['Gross profit', 'income_statement', 'gross_profit'],
    ['Operating profit', 'income_statement', 'operating_profit'],
    ['EBITDA', 'income_statement', 'ebitda'],
    ['Interest expense', 'income_statement', 'interest_expense'],
    ['Net income', 'income_statement', 'net_income'],
    ['Operating cash flow', 'cash_flow_statement', 'operating_cash_flow'],
    ['Capital expenditure', 'cash_flow_statement', 'capital_expenditure'],
    ['Financing cash flow', 'cash_flow_statement', 'financing_cash_flow'],
    ['Ending cash', 'balance_sheet', 'cash_and_equivalents'],
    ['Total assets', 'balance_sheet', 'total_assets'],
    ['Total equity', 'balance_sheet', 'total_equity'],
    ['Balance difference', 'balance_sheet', 'balance_difference']
  ];
  for (const [label, statement, field] of rows) {
    const row = document.createElement('tr');
    addText(row, 'th', label);
    for (const record of scenario) addText(row, 'td', record[statement][field].value_display);
    tbody.append(row);
  }
  table.append(tbody);
  tableWrap.append(table);
  details.append(tableWrap);
  const statuses = scenario.map(item => `${item.period}: ${item.balance_sheet.balance_status}`).join(' · ');
  addText(details, 'p', `Balance check · ${statuses}`, 'scenario-balance-status');
  const warnings = scenario.flatMap(item => item.warnings.map(warning => `${item.period}: ${warning}`));
  if (warnings.length) {
    const list = document.createElement('ul');
    list.className = 'scenario-warning-list';
    warnings.forEach(warning => addText(list, 'li', warning));
    details.append(list);
  }
}

function renderModel(data) {
  currentModel = data;
  const summaries = document.querySelector('#scenario-summary');
  summaries.replaceChildren();
  for (const name of ['downside', 'base', 'upside']) {
    const scenario = data.scenarios[name];
    const last = scenario[scenario.length - 1];
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'scenario-card';
    card.setAttribute('aria-pressed', String(name === selectedScenario));
    card.addEventListener('click', () => {
      selectedScenario = name;
      summaries.querySelectorAll('.scenario-card').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.scenario === name)));
      renderScenarioDetails(name);
    });
    card.dataset.scenario = name;
    addText(card, 'strong', `${name[0].toUpperCase()}${name.slice(1)} · ${last.period}`);
    addText(card, 'span', `Revenue · ${last.income_statement.revenue.value_display} ${data.unit}`);
    addText(card, 'span', `Net income · ${last.income_statement.net_income.value_display} ${data.unit}`);
    addText(card, 'span', `Ending cash · ${last.balance_sheet.cash_and_equivalents.value_display} ${data.unit}`);
    summaries.append(card);
  }
  selectedScenario = 'base';
  summaries.querySelectorAll('.scenario-card').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.scenario === selectedScenario)));
  document.querySelector('#model-version').textContent = `${data.review_status} · ${data.model_version}`;
  const status = document.querySelector('#model-input-status');
  status.textContent = `Opening balance check: ${data.opening_balance_check.status} · difference ${data.opening_balance_check.difference} ${data.currency} ${data.unit}. Assumptions and results are retained only for this page session.`;
  modelResults.hidden = false;
  renderScenarioDetails(selectedScenario);
}

function svgElement(tag, attributes = {}, text = '') {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
  if (text) element.textContent = text;
  return element;
}

function renderRevenueHistory(history) {
  const chart = document.querySelector('#history-chart');
  chart.replaceChildren();
  const points = history.periods.map(item => ({
    period: item.metadata.period,
    value: item.normalized_inputs.revenue === null ? null : Number(item.normalized_inputs.revenue)
  })).filter(item => Number.isFinite(item.value));
  document.querySelector('#history-chart-unit').textContent = `${history.currency} base currency units`;
  if (points.length < 2) {
    chart.append(svgElement('text', { x: 320, y: 110, 'text-anchor': 'middle', class: 'chart-period' }, 'At least two reported revenue values are needed for a trend.'));
    return;
  }
  const left = 48, right = 612, top = 28, bottom = 184;
  const min = Math.min(...points.map(item => item.value));
  const max = Math.max(...points.map(item => item.value));
  const spread = max - min || Math.max(Math.abs(max) * .1, 1);
  const coords = points.map((item, index) => ({
    ...item,
    x: left + (right - left) * index / (points.length - 1),
    y: bottom - ((item.value - (min - spread * .1)) / (spread * 1.2)) * (bottom - top)
  }));
  for (let line = 0; line < 4; line += 1) {
    const y = top + (bottom - top) * line / 3;
    chart.append(svgElement('path', { d: `M${left} ${y}H${right}`, class: 'chart-gridline' }));
  }
  chart.append(svgElement('path', { d: coords.map((item, index) => `${index ? 'L' : 'M'}${item.x} ${item.y}`).join(' '), class: 'chart-trend' }));
  for (const point of coords) {
    const dot = svgElement('circle', { cx: point.x, cy: point.y, r: 4.5, class: 'chart-point' });
    dot.append(svgElement('title', {}, `${point.period}: ${point.value} ${history.currency} base units`));
    chart.append(dot);
    chart.append(svgElement('text', { x: point.x, y: bottom + 27, 'text-anchor': 'middle', class: 'chart-period' }, point.period));
  }
  chart.setAttribute('aria-label', `Revenue history for ${history.company_name} in ${history.currency} base currency units.`);
}

function renderHistoryTable(history) {
  const host = document.querySelector('#history-table');
  host.replaceChildren();
  const table = document.createElement('table');
  const head = document.createElement('thead');
  const headerRow = document.createElement('tr');
  for (const label of ['Period', `Revenue (${history.currency} base units)`, 'Growth', 'Gross margin', 'Operating margin', 'Net margin', 'Current ratio', 'Debt / equity', 'Evidence']) addText(headerRow, 'th', label);
  head.append(headerRow);
  table.append(head);
  const body = document.createElement('tbody');
  const growth = new Map((history.metric_series.revenue_growth_percent || []).map(item => [item.period, item.value_decimal]));
  for (const item of history.periods) {
    const row = document.createElement('tr');
    addText(row, 'th', item.metadata.period);
    addText(row, 'td', item.normalized_inputs.revenue ?? '—');
    addText(row, 'td', growth.has(item.metadata.period) ? `${Number(growth.get(item.metadata.period)).toFixed(2)}%` : '—');
    for (const metric of ['gross_margin_percent', 'operating_margin_percent', 'net_margin_percent', 'current_ratio', 'debt_to_equity_ratio']) {
      const result = item.metrics[metric];
      addText(row, 'td', result ? `${result.value_display}${metric.endsWith('_percent') ? '%' : ''}` : item.metric_states?.[metric] || '—');
    }
    const evidenceCell = document.createElement('td');
    const source = document.createElement('a');
    source.href = item.metadata.source_url;
    source.target = '_blank';
    source.rel = 'noopener noreferrer';
    source.textContent = 'Open source';
    evidenceCell.append(source);
    row.append(evidenceCell);
    body.append(row);
  }
  table.append(body);
  host.append(table);
}

function renderPeerComparison(items) {
  const section = document.querySelector('#peer-comparison');
  const host = document.querySelector('#peer-table');
  host.replaceChildren();
  if (!items?.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const table = document.createElement('table');
  const header = document.createElement('thead');
  const headerRow = document.createElement('tr');
  for (const label of ['Company', 'Period', 'Revenue (base currency units)', 'Growth', 'Gross margin', 'Operating margin', 'Net margin', 'Current ratio', 'Debt / equity']) addText(headerRow, 'th', label);
  header.append(headerRow);
  table.append(header);
  const body = document.createElement('tbody');
  for (const item of items) {
    const row = document.createElement('tr');
    addText(row, 'th', item.company_name);
    addText(row, 'td', item.period);
    addText(row, 'td', item.normalized_inputs.revenue ?? '—');
    for (const metric of ['revenue_growth_percent', 'gross_margin_percent', 'operating_margin_percent', 'net_margin_percent', 'current_ratio', 'debt_to_equity_ratio']) {
      const result = item.metrics[metric];
      addText(row, 'td', result ? `${result.value_display}${metric.endsWith('_percent') ? '%' : ''}` : '—');
    }
    body.append(row);
  }
  table.append(body);
  host.append(table);
}

function renderImportedHistory(data) {
  importedHistory = data;
  const select = historyCompanySelect;
  select.replaceChildren();
  for (const [index, company] of data.companies.entries()) {
    const option = document.createElement('option');
    option.value = String(index);
    option.textContent = company.company_name;
    select.append(option);
  }
  document.querySelector('#history-company-select-label').hidden = data.companies.length < 2;
  const warnings = document.querySelector('#history-warnings');
  warnings.replaceChildren();
  for (const message of [...(data.warnings || []), ...data.companies.flatMap(item => item.warnings || [])]) addText(warnings, 'p', message, 'history-warning');
  renderPeerComparison(data.peer_comparison);
  renderOneHistory(data.companies[0]);
  historyResults.hidden = false;
}

function renderOneHistory(history) {
  if (!history) return;
  const kpis = document.querySelector('#history-kpis');
  kpis.replaceChildren();
  const latest = history.periods[history.periods.length - 1];
  const growth = (history.metric_series.revenue_growth_percent || []).at(-1);
  const cards = [
    [`Periods analyzed`, String(history.periods.length)],
    [`${latest.metadata.period} revenue`, `${latest.normalized_inputs.revenue ?? '—'} ${history.currency} units`],
    ['Latest revenue growth', growth ? `${Number(growth.value_decimal).toFixed(2)}%` : '—'],
    ['Revenue CAGR', history.revenue_cagr ? `${Number(history.revenue_cagr.value_decimal).toFixed(2)}%` : 'Not defined']
  ];
  for (const [label, value] of cards) {
    const card = document.createElement('article');
    card.className = 'history-kpi';
    addText(card, 'small', label);
    addText(card, 'strong', value);
    kpis.append(card);
  }
  renderRevenueHistory(history);
  renderHistoryTable(history);
}

form.addEventListener('reset', () => {
  sourceKindField.value = 'PUBLIC_SOURCE';
  sourceUrlField.required = true;
  delete form.dataset.sampleMode;
  lastStatementPayload = null;
  currentModel = null;
  results.hidden = true;
  emptyState.hidden = false;
  sourceSummary.replaceChildren();
  metricsOutput.replaceChildren();
  limitationsOutput.replaceChildren();
  modelAssumptionsForm.hidden = true;
  modelResults.hidden = true;
  document.querySelector('#model-version').textContent = 'Model not run';
});

form.addEventListener('input', () => {
  if (lastStatementPayload) {
    lastStatementPayload = null;
    currentModel = null;
    modelResults.hidden = true;
    document.querySelector('#run-model').disabled = true;
    document.querySelector('#model-input-status').textContent = 'Inputs changed. Recalculate the statement before modelling to keep the forecast linked to the displayed source data.';
  }
});

sourceUrlField.addEventListener('input', () => {
  if (sourceUrlField.value.trim()) {
    sourceKindField.value = 'PUBLIC_SOURCE';
    sourceUrlField.required = true;
    delete form.dataset.sampleMode;
  }
});

const illustrativeSample = {
  source_kind: 'ILLUSTRATIVE_SAMPLE',
  source_url: '',
  period: 'FY2025',
  prior_period: 'FY2024',
  publication_date: '',
  currency: 'INR',
  unit: 'millions',
  revenue: '1000',
  prior_revenue: '870',
  gross_profit: '420',
  operating_profit: '150',
  net_income: '92',
  cost_of_revenue: '580',
  depreciation: '35',
  interest_expense: '20',
  tax_expense: '38',
  current_assets: '600',
  current_liabilities: '300',
  inventory: '120',
  total_debt: '240',
  total_equity: '400',
  cash_and_equivalents: '100',
  noncurrent_assets: '500',
  other_liabilities: '160',
  average_total_assets: '1050',
  average_total_equity: '380',
  average_inventory: '110',
  average_receivables: '140',
  average_payables: '120',
  operating_cash_flow: '110',
  investing_cash_flow: '-65',
  financing_cash_flow: '-30',
  capital_expenditure: '65'
};

document.querySelectorAll('.load-sample').forEach(button => {
  button.addEventListener('click', () => {
    for (const [name, value] of Object.entries(illustrativeSample)) {
      const field = form.elements.namedItem(name);
      if (field) field.value = value;
    }
    sourceKindField.value = 'ILLUSTRATIVE_SAMPLE';
    sourceUrlField.required = false;
    form.dataset.sampleMode = 'true';
    form.requestSubmit();
  });
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  const submit = form.querySelector('[type="submit"]');
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.currency = payload.currency.toUpperCase();
  const amounts = {};
  for (const name of amountNames) {
    if (payload[name] !== '') amounts[name] = payload[name];
    delete payload[name];
  }
  payload.amounts = amounts;
  lastStatementPayload = structuredClone(payload);

  submit.disabled = true;
  submit.classList.add('is-loading');
  submit.setAttribute('aria-busy', 'true');
  submit.querySelector('.button-text').textContent = 'Calculating…';
  try {
    const response = await fetch('/api/finance/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to calculate these figures.');
    renderSource(data.metadata);
    unitsNode.textContent = `${data.metadata.currency} · ${data.metadata.unit} entered`;
    renderMetrics(data.metrics, data.metadata);
    renderMetricStates(data.metric_states);
    renderLimitations(data.limitations);
    prepareModelForm(data.model_defaults, data.model_defaults_error);
    emptyState.hidden = data.metadata.source_kind !== 'ILLUSTRATIVE_SAMPLE';
    results.hidden = false;
    results.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
  } catch (error) {
    emptyState.hidden = false;
    const oldError = emptyState.querySelector('.request-error');
    if (oldError) oldError.remove();
    addText(emptyState, 'p', error.message || 'Please check the source details and try again.', 'request-error');
  } finally {
    submit.disabled = false;
    submit.classList.remove('is-loading');
    submit.removeAttribute('aria-busy');
    submit.querySelector('.button-text').textContent = 'Calculate metrics';
  }
});

document.querySelector('#run-model').addEventListener('click', async event => {
  const button = event.currentTarget;
  if (!lastStatementPayload) {
    modelError.textContent = 'Recalculate the statement before running a model.';
    modelError.hidden = false;
    return;
  }
  const formValues = Object.fromEntries(new FormData(modelAssumptionsForm).entries());
  const forecastYears = Number(formValues.forecast_years);
  delete formValues.forecast_years;
  modelError.hidden = true;
  button.disabled = true;
  button.textContent = 'Generating scenarios…';
  button.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch('/api/finance/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ statement: lastStatementPayload, assumptions: formValues, forecast_years: forecastYears })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to build this model.');
    renderModel(data);
    modelResults.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'nearest' });
  } catch (error) {
    modelError.textContent = error.message || 'Please review the assumptions and try again.';
    modelError.hidden = false;
  } finally {
    button.disabled = !lastStatementPayload;
    button.textContent = 'Generate three scenarios';
    button.removeAttribute('aria-busy');
  }
});

historyCompanySelect.addEventListener('change', () => {
  const index = Number(historyCompanySelect.value);
  if (Number.isInteger(index)) renderOneHistory(importedHistory?.companies?.[index]);
});

historyImportForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = historyImportForm.querySelector('[type="submit"]');
  const fileInput = document.querySelector('#history-file');
  if (!fileInput.files?.length) {
    historyImportError.textContent = 'Choose a CSV or XLSX file.';
    historyImportError.hidden = false;
    return;
  }
  historyImportError.hidden = true;
  button.disabled = true;
  button.textContent = 'Validating periods…';
  button.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch('/api/finance/import', { method: 'POST', body: new FormData(historyImportForm) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to import this statement history.');
    renderImportedHistory(data);
    historyResults.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'nearest' });
  } catch (error) {
    historyImportError.textContent = error.message || 'Check the import template and try again.';
    historyImportError.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Analyze imported periods';
    button.removeAttribute('aria-busy');
  }
});
