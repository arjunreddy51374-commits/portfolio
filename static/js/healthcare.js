const healthForm = document.querySelector('#health-form');
const healthEmpty = document.querySelector('#health-empty');
const healthResults = document.querySelector('#health-results');
const healthSource = document.querySelector('#health-source-summary');
const healthMetrics = document.querySelector('#health-metrics');
const healthLimitations = document.querySelector('#health-limitations');

function appendText(parent, tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
}

function renderHealthResult(data) {
  healthSource.replaceChildren();
  const source = document.createElement('a');
  source.href = data.metadata.source_url;
  source.target = '_blank';
  source.rel = 'noopener noreferrer';
  source.textContent = data.metadata.source_url;
  healthSource.append(source);
  appendText(healthSource, 'span', `Reporting period: ${data.metadata.period}`);
  appendText(healthSource, 'span', `Retrieved: ${data.metadata.retrieved_at} · Status: ${data.metadata.review_status}`);

  healthMetrics.replaceChildren();
  const metricEntries = Object.entries(data.metrics);
  if (!metricEntries.length) {
    appendText(healthMetrics, 'p', 'No complete KPI numerator/denominator pairs were provided.', 'no-metrics');
  }
  for (const [, metric] of metricEntries) {
    const card = document.createElement('article');
    card.className = 'metric-result';
    appendText(card, 'h3', metric.label);
    appendText(card, 'strong', `${metric.value_display}${metric.unit === 'percent' ? '%' : ` ${metric.unit}`}`, 'metric-value');
    appendText(card, 'p', `Formula: ${metric.formula}`, 'metric-formula');
    const inputs = Object.entries(metric.inputs).map(([name, value]) => `${data.field_definitions[name][0]} = ${value}`).join(' · ');
    appendText(card, 'p', `Inputs: ${inputs}`, 'metric-trace');
    healthMetrics.append(card);
  }

  healthLimitations.replaceChildren();
  if (!data.limitations.length) appendText(healthLimitations, 'li', 'No missing or denominator issues were detected. Source and cohort definitions still require review.');
  else data.limitations.forEach(note => appendText(healthLimitations, 'li', note));

  healthEmpty.hidden = true;
  healthResults.hidden = false;
}

healthForm.addEventListener('reset', () => {
  healthEmpty.hidden = false;
  healthResults.hidden = true;
  healthSource.replaceChildren();
  healthMetrics.replaceChildren();
  healthLimitations.replaceChildren();
});

healthForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = healthForm.querySelector('[type="submit"]');
  const raw = Object.fromEntries(new FormData(healthForm).entries());
  const values = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!['source_url', 'period'].includes(key) && value !== '') values[key] = value;
  }
  const payload = { source_url: raw.source_url, period: raw.period, values };
  button.disabled = true;
  button.classList.add('is-loading');
  button.setAttribute('aria-busy', 'true');
  button.querySelector('.button-text').textContent = 'Calculating…';
  try {
    const response = await fetch('/api/healthcare/kpis', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to calculate healthcare KPIs.');
    renderHealthResult(data);
  } catch (error) {
    healthEmpty.hidden = false;
    const oldError = healthEmpty.querySelector('.request-error');
    if (oldError) oldError.remove();
    appendText(healthEmpty, 'p', error.message || 'Please check the input values and try again.', 'request-error');
  } finally {
    button.disabled = false;
    button.classList.remove('is-loading');
    button.removeAttribute('aria-busy');
    button.querySelector('.button-text').textContent = 'Calculate available KPIs';
  }
});
