const form = document.querySelector('#finance-form');
const emptyState = document.querySelector('#finance-empty');
const results = document.querySelector('#finance-results');
const sourceSummary = document.querySelector('#source-summary');
const unitsNode = document.querySelector('#result-units');
const metricsOutput = document.querySelector('#metrics-output');
const limitationsOutput = document.querySelector('#limitations-output');
const labels = {
  revenue_growth_percent: 'Revenue growth',
  gross_margin_percent: 'Gross margin',
  operating_margin_percent: 'Operating margin',
  net_margin_percent: 'Net margin',
  working_capital: 'Working capital',
  current_ratio: 'Current ratio',
  quick_ratio: 'Quick ratio',
  debt_to_equity_ratio: 'Debt-to-equity',
  operating_cash_flow_to_net_income_ratio: 'Operating cash flow / net income',
  cash_flow_category_subtotal: 'Cash-flow category subtotal'
};
const amountNames = [
  'revenue', 'prior_revenue', 'gross_profit', 'operating_profit', 'net_income',
  'current_assets', 'current_liabilities', 'inventory', 'total_debt', 'total_equity',
  'operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow'
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
  const source = document.createElement('a');
  source.href = meta.source_url;
  source.rel = 'noopener noreferrer';
  source.target = '_blank';
  source.textContent = meta.source_url;
  sourceSummary.append(source);
  addText(sourceSummary, 'span', `Period: ${meta.period}${meta.prior_period ? ` · Prior comparison: ${meta.prior_period}` : ''} · Currency: ${meta.currency} · Source units: ${meta.unit}`);
  addText(sourceSummary, 'span', `Publication date: ${meta.publication_date || 'Not supplied'} · Retrieved: ${meta.retrieved_at}`);
  addText(sourceSummary, 'span', `Status: ${meta.review_status} · normalized calculation units: ${meta.calculation_unit}`);
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

function renderLimitations(items) {
  limitationsOutput.replaceChildren();
  if (!items.length) {
    addText(limitationsOutput, 'li', 'No calculation-specific limitations detected. Source review is still required.');
    return;
  }
  items.forEach(item => addText(limitationsOutput, 'li', item));
}

form.addEventListener('reset', () => {
  results.hidden = true;
  emptyState.hidden = false;
  sourceSummary.replaceChildren();
  metricsOutput.replaceChildren();
  limitationsOutput.replaceChildren();
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
    renderLimitations(data.limitations);
    emptyState.hidden = true;
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
