const adminToken = document.querySelector('#admin-token');
const reviewerToken = document.querySelector('#reviewer-token');
const notice = document.querySelector('#notice');
let catalog = null;
let workspaceData = null;
let selectedRelease = '';
let activeIngestionJob = null;

function textNode(parent, tag, value, className = '') {
  const element = document.createElement(tag);
  element.textContent = value ?? '';
  if (className) element.className = className;
  parent.append(element);
  return element;
}

function setNotice(message, kind = '') {
  notice.textContent = message;
  notice.className = `notice ${kind}`.trim();
}

async function api(path, { method = 'GET', body, role = 'public' } = {}) {
  const token = role === 'admin' ? adminToken.value : role === 'reviewer' ? reviewerToken.value : '';
  const headers = { Accept: 'application/json' };
  if (token) headers['X-FIP-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function optionList(select, items, labelFn, valueFn) {
  select.replaceChildren();
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = 'Choose a record';
  select.append(empty);
  for (const item of items) {
    const option = document.createElement('option');
    option.value = valueFn(item);
    option.textContent = labelFn(item);
    select.append(option);
  }
}

async function loadCatalog() {
  catalog = await api('/api/fip/metric-catalog');
  const unitSelect = document.querySelector('#observation-unit');
  const periodSelect = document.querySelector('#observation-period-type');
  optionList(unitSelect, catalog.units, value => value, value => value);
  optionList(periodSelect, catalog.period_types, value => value[0].toUpperCase() + value.slice(1), value => value);
  optionList(document.querySelector('#observation-fx'), [], value => value, value => value);
  const frameworkChoices = Object.entries(catalog.frameworks);
  optionList(document.querySelector('#mapping-framework'), frameworkChoices,
    item => `${item[0]} · ${item[1].name}`, item => item[0]);
  optionList(document.querySelector('#observation-framework'), frameworkChoices,
    item => `${item[0]} · ${item[1].name}`, item => item[0]);
  document.querySelector('#observation-framework').value = 'IFRS';
  updateMetricChoices();
  updateMappingChoices();
}

function updateMetricChoices() {
  if (!catalog) return;
  const domain = document.querySelector('#observation-domain').value;
  const metrics = catalog[domain];
  optionList(document.querySelector('#observation-metric'), Object.entries(metrics), entry => entry[1], entry => entry[0]);
  document.querySelector('#government-fields').hidden = domain !== 'government';
  const framework = document.querySelector('#observation-framework');
  if (domain === 'government' && framework.value === 'IFRS') framework.value = 'GOVERNMENT_FISCAL';
  if (domain === 'corporate' && framework.value === 'GOVERNMENT_FISCAL') framework.value = 'IFRS';
}

function updateMappingChoices() {
  if (!catalog) return;
  const domain = document.querySelector('#mapping-domain').value;
  optionList(document.querySelector('#mapping-metric'), Object.entries(catalog[domain]),
    item => item[1], item => item[0]);
}

async function loadSummary() {
  const data = await api('/api/fip/summary');
  document.querySelector('#count-entities').textContent = data.entities;
  document.querySelector('#count-sources').textContent = data.sources;
  document.querySelector('#count-observations').textContent = data.observations;
  document.querySelector('#count-published').textContent = data.published_observations;
}

function renderPublic(data) {
  const summary = document.querySelector('#release-summary');
  if (data.release) {
    summary.textContent = `Current approved release ${data.release.version} · approved ${data.release.approved_at} · ${data.observations.length} facts and ${data.calculations.length} calculation runs in this view.`;
    document.querySelector('#excel-export').hidden = false;
    document.querySelector('#csv-export').hidden = false;
    document.querySelector('#powerbi-export').hidden = false;
    const query = `?release_id=${encodeURIComponent(data.release.id)}`;
    document.querySelector('#excel-export').href = `/api/fip/delivery/excel.xlsx${query}`;
    document.querySelector('#csv-export').href = `/api/fip/delivery/excel.csv${query}`;
    document.querySelector('#powerbi-export').href = `/api/fip/delivery/powerbi${query}`;
  } else {
    summary.textContent = 'No approved release snapshot yet. Individually published records remain outside the public dataset until a release is approved.';
    document.querySelector('#excel-export').hidden = true;
    document.querySelector('#csv-export').hidden = true;
    document.querySelector('#powerbi-export').hidden = true;
  }
  const body = document.querySelector('#public-observations');
  body.replaceChildren();
  if (!data.observations.length) {
    const row = body.insertRow();
    textNode(row.insertCell(), 'span', 'No approved observations have been published yet.', 'empty-row');
    row.firstChild.colSpan = 5;
  } else {
    for (const item of data.observations) {
      const row = body.insertRow();
      textNode(row.insertCell(), 'strong', item.canonical_name);
      textNode(row.insertCell(), 'span', `${item.metric_id.replaceAll('_', ' ')} · ${item.reported_label} · v${item.version}`);
      const valueCell = row.insertCell();
      textNode(valueCell, 'span', `${item.value_decimal} ${item.unit} ${item.currency}`);
      textNode(valueCell, 'small', `Standardized · ${item.standardized_value_decimal} ${item.standardized_currency}`);
      textNode(row.insertCell(), 'span', `${item.period_id} · ${item.reporting_basis}`);
      const evidenceCell = row.insertCell();
      const link = document.createElement('a');
      link.href = item.source_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = `${item.source_title} · ${item.evidence_reference} · freshness ${item.freshness_status.toLowerCase()}`;
      evidenceCell.append(link);
    }
  }
  const calculations = document.querySelector('#public-calculations');
  calculations.replaceChildren();
  for (const run of data.calculations) {
    const article = document.createElement('article');
    article.className = 'published-calculation';
    article.dataset.domain = run.domain;
    textNode(article, 'p', `${run.canonical_name} · ${run.period_id} · Formula catalogue ${run.formula_catalog_version}`, 'calc-overline');
    textNode(article, 'h3', run.domain === 'government' ? 'Government finance results' : 'Corporate finance results');
    const grid = document.createElement('div');
    grid.className = 'published-metrics';
    for (const metric of Object.values(run.result.metrics)) {
      const item = document.createElement('span');
      textNode(item, 'small', metric.label);
      const suffix = metric.unit === '%' ? '%' : metric.unit === 'amount' ? ` ${run.result.currency} units` : '';
      textNode(item, 'strong', `${metric.value_decimal}${suffix}`);
      textNode(item, 'small', `${metric.formula_id} v${metric.formula_version}`);
      const inputTrace = Object.entries(metric.inputs || {}).map(([name, value]) => `${name}: ${value}`).join(' · ');
      if (inputTrace) textNode(item, 'small', `Inputs · ${inputTrace}`, 'metric-input-trace');
      grid.append(item);
    }
    article.append(grid);
    const sourceTrace = (run.result.input_observations || []).map(input => `${input.metric_id} ${input.id} v${input.version}`).join(' · ');
    if (sourceTrace) textNode(article, 'p', `Source observation lineage · ${sourceTrace}`, 'calc-source-trace');
    if (run.result.limitations?.length) {
      const list = document.createElement('ul');
      run.result.limitations.forEach(note => textNode(list, 'li', note));
      article.append(list);
    }
    calculations.append(article);
  }
}

async function refreshPublic() {
  const url = selectedRelease ? `/api/fip/publications?release_id=${encodeURIComponent(selectedRelease)}` : '/api/fip/publications';
  const data = await api(url);
  renderPublic(data);
  const releases = await api('/api/fip/releases');
  const select = document.querySelector('#release-select');
  const oldValue = selectedRelease;
  select.replaceChildren();
  const latest = document.createElement('option'); latest.value = ''; latest.textContent = 'Latest approved release'; select.append(latest);
  for (const release of releases.releases) {
    const option = document.createElement('option'); option.value = release.id;
    option.textContent = `${release.version} · ${release.approved_at} · ${release.coverage.fact_count} facts`;
    select.append(option);
  }
  select.value = oldValue;
  await loadSummary();
}

function fillStaffSelectors(data) {
  const entities = data.entities;
  const sources = data.sources;
  const selectedIngestionSource = document.querySelector('#ingestion-source').value;
  const selectedJobSource = document.querySelector('#ingestion-job-source').value;
  optionList(document.querySelector('#ingestion-source'), sources,
    item => `${item.title} · ${item.id} · ${item.approval_status}`, item => item.id);
  optionList(document.querySelector('#ingestion-job-source'), sources,
    item => `${item.title} · ${item.id}`, item => item.id);
  if (sources.some(item => item.id === selectedIngestionSource)) document.querySelector('#ingestion-source').value = selectedIngestionSource;
  if (sources.some(item => item.id === selectedJobSource)) document.querySelector('#ingestion-job-source').value = selectedJobSource;
  optionList(document.querySelector('#observation-entity'), entities,
    item => `${item.canonical_name} · ${item.id} · ${item.review_status}`, item => item.id);
  optionList(document.querySelector('#observation-source'), sources,
    item => `${item.title} · ${item.id} · ${item.approval_status} · ${item.permitted_use}`, item => item.id);
  optionList(document.querySelector('#fx-source'), sources,
    item => `${item.title} · ${item.id} · ${item.approval_status} · ${item.permitted_use}`, item => item.id);
  const entityLabel = item => `${item.canonical_name} · ${item.id} · ${item.review_status}`;
  optionList(document.querySelector('#alias-entity'), entities, entityLabel, item => item.id);
  optionList(document.querySelector('#relationship-from'), entities, entityLabel, item => item.id);
  optionList(document.querySelector('#relationship-to'), entities, entityLabel, item => item.id);
  optionList(document.querySelector('#alias-source'), sources, item => `${item.title} · ${item.approval_status}`, item => item.id);
  optionList(document.querySelector('#relationship-source'), sources, item => `${item.title} · ${item.approval_status}`, item => item.id);
  optionList(document.querySelector('#mapping-source'), sources, item => `${item.title} · ${item.approval_status}`, item => item.id);
  const fxSelect = document.querySelector('#observation-fx');
  fxSelect.replaceChildren();
  const noFx = document.createElement('option'); noFx.value = ''; noFx.textContent = 'No currency conversion'; fxSelect.append(noFx);
  for (const rate of data.fx_rates.filter(item => item.status === 'APPROVED')) {
    const option = document.createElement('option');
    option.value = rate.id;
    option.textContent = `${rate.source_currency} → ${rate.target_currency} · ${rate.period_id} · ${rate.conversion_convention} · ${rate.rate_decimal}`;
    fxSelect.append(option);
  }
  const verified = entities.filter(item => item.review_status === 'VERIFIED');
  optionList(document.querySelector('#calculation-entity'), verified,
    item => `${item.canonical_name} · ${item.id}`, item => item.id);
  const correctionSelect = document.querySelector('#observation-supersedes');
  correctionSelect.replaceChildren();
  const noCorrection = document.createElement('option');
  noCorrection.value = ''; noCorrection.textContent = 'New observation'; correctionSelect.append(noCorrection);
  for (const item of data.observations.filter(value => ['PUBLISHED', 'VALIDATION_FAILED', 'REJECTED', 'SUPERSEDED'].includes(value.status))) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = `${item.canonical_name} · ${item.metric_id} · ${item.period_id} · v${item.version} · ${item.status}`;
    correctionSelect.append(option);
  }
}

function renderIngestionSettings(settings) {
  const globalMode = document.querySelector('#global-ingestion-mode');
  globalMode.value = settings.global_mode;
  const selectedSource = document.querySelector('#ingestion-source');
  const sourceId = selectedSource.value;
  const source = settings.sources.find(item => item.id === sourceId);
  if (source) {
    document.querySelector('#source-ingestion-mode').value = source.ingestion_mode || '';
    document.querySelector('#source-connector-format').value = source.connector_format;
    document.querySelector('#source-schedule-interval').value = source.schedule_interval_minutes ?? '';
    document.querySelector('#source-connector-config').value = JSON.stringify(source.connector_config, null, 2);
    const review = document.querySelector('#connector-review-state');
    review.textContent = source.connector_format === 'manual_only'
      ? `Manual-only source · effective mode ${source.ingestion_mode || settings.global_mode}.`
      : `Connector ${source.ingestion_config_status.toLowerCase()} · source ${source.approval_status.toLowerCase()} · next scheduled run ${source.next_scheduled_at || 'not scheduled'}.`;
    document.querySelector('#approve-source-connector').disabled = source.ingestion_config_status === 'APPROVED';
  }
  renderIngestionJobs(settings.jobs);
}

function renderIngestionJobs(jobs) {
  const root = document.querySelector('#ingestion-job-list');
  root.replaceChildren();
  if (!jobs.length) {
    textNode(root, 'p', 'No ingestion jobs yet.', 'muted-text');
    return;
  }
  for (const job of jobs) {
    const row = document.createElement('article');
    row.className = 'ingestion-job';
    const details = document.createElement('div');
    textNode(details, 'strong', job.source_title || job.source_id);
    textNode(details, 'small', `${job.id} · attempt ${job.attempt_count} · ${job.connector_version} · updated ${job.last_updated_at || job.created_at}`);
    row.append(details);
    textNode(row, 'span', `${job.mode} · ${job.trigger_type}`);
    textNode(row, 'span', job.status, `state ingestion-status-${job.status.toLowerCase()}`);
    textNode(row, 'span', `${job.observations_created} created · ${job.observations_skipped} skipped`);
    const controls = document.createElement('div');
    if (job.status === 'QUEUED' && job.mode !== 'MANUAL') {
      const run = textNode(controls, 'button', 'Run now', 'button action-button');
      run.type = 'button';
      run.addEventListener('click', async () => {
        try {
          await api(`/api/fip/ingestion/jobs/${job.id}/run`, { method: 'POST', role: 'admin' });
          setNotice(`Ingestion job ${job.id} finished. Review its status and exceptions below.`, 'success');
          await refreshWorkspace();
        } catch (error) { setNotice(error.message, 'error'); }
      });
    } else if (job.mode === 'MANUAL' && ['QUEUED', 'AWAITING_REVIEW'].includes(job.status)) {
      const use = textNode(controls, 'button', 'Use manual entry', 'button action-button');
      use.type = 'button';
      use.addEventListener('click', () => {
        activeIngestionJob = { id: job.id, source_id: job.source_id, mode: job.mode, exceptions: job.exceptions || [] };
        document.querySelector('#active-ingestion-job').textContent = `Active MANUAL job ${job.id}. Enter observations below and select source ${job.source_id}.`;
        document.querySelector('#observation-source').value = job.source_id;
        document.querySelector('#observation-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    } else if (job.mode === 'HYBRID' && job.status === 'AWAITING_REVIEW' && job.exceptions?.some(item => item.status === 'OPEN')) {
      const correct = textNode(controls, 'button', 'Correct exception', 'button action-button');
      correct.type = 'button';
      correct.addEventListener('click', () => {
        activeIngestionJob = { id: job.id, source_id: job.source_id, mode: job.mode, exceptions: job.exceptions || [] };
        document.querySelector('#active-ingestion-job').textContent = `Active HYBRID correction for job ${job.id}. Use the observation form below; you will be prompted for the exception row number.`;
        document.querySelector('#observation-source').value = job.source_id;
        document.querySelector('#observation-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    }
    if (job.status === 'FAILED') {
      const retry = textNode(controls, 'button', 'Retry', 'button action-button');
      retry.type = 'button';
      retry.addEventListener('click', async () => {
        try {
          const queued = await api(`/api/fip/ingestion/jobs/${job.id}/retry`, { method: 'POST', role: 'admin' });
          if (queued.mode !== 'MANUAL') {
            const result = await api(`/api/fip/ingestion/jobs/${job.id}/run`, { method: 'POST', role: 'admin' });
            setNotice(`Retry attempt ${result.attempt_count}: ${result.status.toLowerCase()}.`, result.status === 'FAILED' ? 'error' : 'success');
          } else {
            activeIngestionJob = { id: job.id, source_id: job.source_id, mode: job.mode, exceptions: [] };
            setNotice(`Manual retry attempt ${queued.attempt_count} is queued.`, 'success');
          }
          await refreshWorkspace();
        } catch (error) { setNotice(error.message, 'error'); }
      });
    }
    if (job.status === 'QUEUED') {
      const cancel = textNode(controls, 'button', 'Cancel', 'button action-button');
      cancel.type = 'button';
      cancel.addEventListener('click', async () => {
        try {
          const reason = window.prompt('Reason for cancelling this queued ingestion job:');
          if (reason === null || !reason.trim()) return;
          await api(`/api/fip/ingestion/jobs/${job.id}/cancel`, {
            method: 'POST', role: 'admin', body: { reason: reason.trim() },
          });
          if (activeIngestionJob?.id === job.id) activeIngestionJob = null;
          setNotice(`Ingestion job ${job.id} cancelled.`, 'success');
          await refreshWorkspace();
        } catch (error) { setNotice(error.message, 'error'); }
      });
    }
    row.append(controls);
    if (job.error_message) textNode(row, 'p', job.error_message, 'ingestion-job-exceptions');
    for (const exception of job.exceptions || []) {
      if (exception.status === 'OPEN') textNode(row, 'p', `Row ${exception.row_number}: ${exception.error}`, 'ingestion-job-exceptions');
    }
    root.append(row);
  }
}

async function refreshIngestionSettings() {
  const role = adminToken.value ? 'admin' : 'reviewer';
  const data = await api('/api/fip/ingestion/settings', { role });
  renderIngestionSettings(data);
  return data;
}

async function refreshIngestionJobs() {
  const role = adminToken.value ? 'admin' : 'reviewer';
  const data = await api('/api/fip/ingestion/jobs', { role });
  renderIngestionJobs(data.jobs);
}

function nextAction(record) {
  if (record.record_type === 'entity' && record.review_status === 'NEEDS_REVIEW') return ['Verify identity', 'reviewer', `/api/fip/entities/${record.id}/verify`];
  if (record.record_type === 'entity_alias' && record.review_status === 'NEEDS_REVIEW') return ['Verify alias', 'reviewer', `/api/fip/entity-aliases/${record.id}/verify`];
  if (record.record_type === 'entity_relationship' && record.review_status === 'NEEDS_REVIEW') return ['Verify relationship', 'reviewer', `/api/fip/relationships/${record.id}/verify`];
  if (record.record_type === 'metric_mapping' && record.review_status === 'NEEDS_REVIEW') return ['Verify mapping', 'reviewer', `/api/fip/metric-mappings/${record.id}/verify`];
  if (record.record_type === 'source' && record.approval_status === 'PENDING_REVIEW') return ['Approve source', 'reviewer', `/api/fip/sources/${record.id}/approve`];
  if (record.record_type === 'fx_rate' && record.status === 'PENDING_REVIEW') return ['Approve FX rate', 'reviewer', `/api/fip/fx-rates/${record.id}/approve`];
  if (record.record_type === 'observation') {
    if (record.status === 'INGESTED') return ['Validate', 'reviewer', `/api/fip/observations/${record.id}/validate`];
    if (record.status === 'VALIDATED') return ['Approve', 'reviewer', `/api/fip/observations/${record.id}/approve`];
    if (record.status === 'APPROVED') return ['Publish fact', 'reviewer', `/api/fip/observations/${record.id}/publish`];
  }
  if (record.record_type === 'calculation' && record.status === 'REVIEW_REQUIRED') return ['Publish calculation', 'reviewer', `/api/fip/calculations/${record.id}/publish`];
  if (record.record_type === 'release' && record.status === 'DRAFT') return ['Approve release', 'reviewer', `/api/fip/releases/${record.id}/approve`];
  return null;
}

function recordState(record) {
  return record.status || record.review_status || record.approval_status;
}

function renderQueue(data) {
  const body = document.querySelector('#review-queue');
  body.replaceChildren();
  const items = [
    ...data.entities.map(item => ({ ...item, record_type: 'entity', subject: item.canonical_name, context: `${item.entity_type} · ${item.jurisdiction_code}` })),
    ...data.aliases.map(item => ({ ...item, record_type: 'entity_alias', subject: `${item.canonical_name} · ${item.alias}`, context: `${item.alias_type} · ${item.source_id}` })),
    ...data.relationships.map(item => ({ ...item, record_type: 'entity_relationship', subject: `${item.from_name} → ${item.to_name}`, context: `${item.relationship_type} · ${item.source_id}` })),
    ...data.metric_mappings.map(item => ({ ...item, record_type: 'metric_mapping', subject: `${item.reported_label} → ${item.metric_id}`, context: `${item.framework_id} ${item.framework_version} · ${item.jurisdiction_code} · ${item.source_id}` })),
    ...data.sources.map(item => ({ ...item, record_type: 'source', subject: item.title, context: `${item.publisher} · ${item.permitted_use}` })),
    ...data.fx_rates.map(item => ({ ...item, record_type: 'fx_rate', subject: `${item.source_currency} → ${item.target_currency}`, context: `${item.rate_decimal} · ${item.period_id} · ${item.conversion_convention} · ${item.source_title}` })),
    ...data.observations.map(item => ({ ...item, record_type: 'observation', subject: `${item.canonical_name} · ${item.metric_id}`, context: `${item.period_id} · ${item.source_title} · ${item.evidence_reference}` })),
    ...data.calculations.map(item => ({ ...item, record_type: 'calculation', subject: `${item.canonical_name} · ${item.domain} calculation`, context: `${item.period_id} · ${Object.keys(item.result.metrics).length} metrics · catalog ${item.formula_catalog_version}` })),
    ...data.reconciliation_runs.map(item => ({ ...item, record_type: 'reconciliation', subject: `${item.entity_id} · ${item.domain} reconciliation`, context: `${item.period_id} · ${item.status} · ${item.id}` })),
    ...data.releases.map(item => ({ ...item, record_type: 'release', subject: item.version, context: `${item.notes} · created by ${item.created_by}` }))
  ].sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (!items.length) {
    const row = body.insertRow();
    textNode(row.insertCell(), 'span', 'No records yet.', 'empty-row'); row.firstChild.colSpan = 5;
    return;
  }
  for (const item of items) {
    const row = body.insertRow();
    textNode(row.insertCell(), 'span', `${item.record_type} · ${item.id}`, 'record-id');
    textNode(row.insertCell(), 'strong', item.subject);
    textNode(row.insertCell(), 'span', item.context);
    const state = recordState(item);
    textNode(row.insertCell(), 'span', state, `state state-${state.toLowerCase()}`);
    const actionCell = row.insertCell();
    const action = nextAction(item);
    if (action) {
      const button = textNode(actionCell, 'button', action[0], 'button action-button');
      button.type = 'button';
      button.addEventListener('click', async () => {
        try {
          const reason = window.prompt(`Reason for ${action[0].toLowerCase()}:`);
          if (reason === null || !reason.trim()) return;
          await api(action[2], { method: 'POST', role: action[1], body: { reason: reason.trim() } });
          if (item.record_type === 'release') selectedRelease = '';
          setNotice(`${action[0]} completed for ${item.id}.`, 'success');
          await refreshWorkspace(); await refreshPublic();
        } catch (error) { setNotice(error.message, 'error'); }
      });
    } else {
      textNode(actionCell, 'span', item.validation_message || 'No action available', 'muted-text');
    }
  }
  const auditFeed = document.querySelector('#audit-feed');
  auditFeed.replaceChildren();
  if (!data.audit_events.length) {
    textNode(auditFeed, 'p', 'No audit actions have been recorded.');
  } else {
    for (const event of data.audit_events) {
      const line = document.createElement('article');
      textNode(line, 'strong', event.action.replaceAll('_', ' '));
      textNode(line, 'span', `${event.created_at} · ${event.actor_role} · ${event.record_type} ${event.record_id} · ${event.reason}`);
      auditFeed.append(line);
    }
  }
}

async function refreshWorkspace() {
  const data = await api('/api/fip/workspace', { role: adminToken.value ? 'admin' : 'reviewer' });
  workspaceData = data;
  fillStaffSelectors(data);
  renderQueue(data);
  await refreshIngestionSettings();
  document.querySelector('#workspace-role').textContent = `Staff view · ${data.role}`;
  return data;
}

async function refreshReadiness() {
  const role = adminToken.value ? 'admin' : reviewerToken.value ? 'reviewer' : 'public';
  const data = await api('/api/fip/readiness', { role });
  const summary = document.querySelector('#readiness-summary');
  const outcome = data.production_ready ? 'Production gates complete' : 'Production readiness not demonstrated';
  summary.textContent = `${outcome} · ${data.outstanding_release_gates.length} open release gates · ${data.outstanding_nonfunctional_requirements.length} open non-functional requirements.`;
  const gateRoot = document.querySelector('#release-gates');
  gateRoot.replaceChildren();
  for (const gate of data.release_gates) {
    const row = document.createElement('div'); row.className = 'register-row';
    const label = document.createElement('div');
    textNode(label, 'strong', gate.label); textNode(label, 'small', `${gate.gate_id} · ${gate.status}`);
    const status = document.createElement('select');
    for (const value of ['PENDING', 'PASS', 'FAIL', 'NOT_APPLICABLE']) {
      const option = document.createElement('option'); option.value = value; option.textContent = value; status.append(option);
    }
    status.value = gate.status;
    const evidence = document.createElement('input'); evidence.value = gate.evidence_reference || ''; evidence.placeholder = 'Evidence reference or N/A rationale';
    const button = textNode(document.createElement('div'), 'button', 'Save gate', 'button action-button');
    button.type = 'button'; button.addEventListener('click', async () => {
      try {
        await api(`/api/fip/release-gates/${gate.gate_id}`, { method: 'PUT', role: 'reviewer', body: { status: status.value, evidence_reference: evidence.value } });
        setNotice(`Release gate ${gate.gate_id} updated.`, 'success'); await refreshReadiness();
      } catch (error) { setNotice(error.message, 'error'); }
    });
    row.append(label, status, evidence, button.parentElement);
    gateRoot.append(row);
  }
  const nfrRoot = document.querySelector('#nfr-register');
  nfrRoot.replaceChildren();
  for (const item of data.nonfunctional_requirements) {
    const row = document.createElement('div'); row.className = 'register-row nfr-row';
    const label = document.createElement('div');
    textNode(label, 'strong', item.description); textNode(label, 'small', `${item.category} · ${item.status}`);
    const target = document.createElement('input'); target.value = item.target || ''; target.placeholder = 'Agreed measurable target';
    const status = document.createElement('select');
    for (const value of ['PENDING', 'DEFINED', 'VERIFIED']) {
      const option = document.createElement('option'); option.value = value; option.textContent = value; status.append(option);
    }
    status.value = item.status;
    const evidence = document.createElement('input'); evidence.value = item.evidence_reference || ''; evidence.placeholder = 'Verification evidence';
    const button = textNode(document.createElement('div'), 'button', 'Save NFR', 'button action-button');
    button.type = 'button'; button.addEventListener('click', async () => {
      try {
        await api(`/api/fip/nfr/${item.requirement_id}`, { method: 'PUT', role: 'reviewer', body: { target: target.value, status: status.value, evidence_reference: evidence.value } });
        setNotice(`Non-functional requirement ${item.requirement_id} updated.`, 'success'); await refreshReadiness();
      } catch (error) { setNotice(error.message, 'error'); }
    });
    row.append(label, target, status, evidence, button.parentElement);
    nfrRoot.append(row);
  }
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

document.querySelector('#observation-domain').addEventListener('change', updateMetricChoices);
document.querySelector('#mapping-domain').addEventListener('change', updateMappingChoices);
document.querySelector('#observation-supersedes').addEventListener('change', event => {
  const prior = workspaceData?.observations.find(item => item.id === event.target.value);
  if (!prior) return;
  document.querySelector('#observation-domain').value = prior.domain;
  updateMetricChoices();
  document.querySelector('#observation-metric').value = prior.metric_id;
  document.querySelector('#observation-entity').value = prior.entity_id;
  const form = document.querySelector('#observation-form');
  form.elements.period_id.value = prior.period_id;
  form.elements.period_type.value = prior.period_type;
  form.elements.period_start.value = prior.period_start;
  form.elements.period_end.value = prior.period_end;
  form.elements.reporting_basis.value = prior.reporting_basis;
  form.elements.framework_id.value = prior.framework_id;
  form.elements.framework_version.value = prior.framework_version;
  form.elements.freshness_status.value = prior.freshness_status;
  form.elements.fiscal_scope.value = prior.fiscal_scope || '';
  form.elements.budget_stage.value = prior.budget_stage || '';
  form.elements.currency.value = prior.currency;
  form.elements.unit.value = prior.unit;
  form.elements.fx_rate_id.value = prior.fx_rate_id || '';
  form.elements.reported_label.value = prior.reported_label;
  setNotice(`Creating a new version of ${prior.id}. Enter the corrected value and attach the applicable source evidence.`, 'success');
});
document.querySelector('#refresh-public').addEventListener('click', async () => {
  try { await refreshPublic(); setNotice('Published data refreshed.', 'success'); }
  catch (error) { setNotice(error.message, 'error'); }
});
document.querySelector('#open-release').addEventListener('click', async () => {
  selectedRelease = document.querySelector('#release-select').value;
  try { await refreshPublic(); setNotice(selectedRelease ? 'Historical approved release loaded.' : 'Latest approved release loaded.', 'success'); }
  catch (error) { setNotice(error.message, 'error'); }
});
document.querySelector('#prepare-release').addEventListener('click', async () => {
  try {
    const notes = window.prompt('Release notes: describe coverage or material data/formula changes.');
    if (notes === null || !notes.trim()) return;
    const release = await api('/api/fip/releases', { method: 'POST', role: 'admin', body: { notes: notes.trim() } });
    setNotice(`Snapshot ${release.version} is ready for reviewer approval (${release.coverage.fact_count} facts, ${release.coverage.calculation_count} calculation runs).`, 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});
document.querySelector('#load-workspace').addEventListener('click', async () => {
  try {
    await refreshWorkspace();
    await loadCatalog();
    await refreshReadiness();
    setNotice('Staff workspace loaded. New items are still unpublished until reviewed.', 'success');
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#ingestion-source').addEventListener('change', async () => {
  try { await refreshIngestionSettings(); }
  catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#save-global-ingestion-mode').addEventListener('click', async () => {
  try {
    const mode = document.querySelector('#global-ingestion-mode').value;
    await api('/api/fip/ingestion/settings', { method: 'PUT', role: 'admin', body: { mode } });
    setNotice(`Global ingestion default saved as ${mode}. No job was started.`, 'success');
    await refreshIngestionSettings();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#save-source-ingestion-settings').addEventListener('click', async () => {
  const sourceId = document.querySelector('#ingestion-source').value;
  if (!sourceId) { setNotice('Choose a source first.', 'error'); return; }
  try {
    const config = JSON.parse(document.querySelector('#source-connector-config').value || '{}');
    const rawInterval = document.querySelector('#source-schedule-interval').value;
    const body = {
      mode: document.querySelector('#source-ingestion-mode').value || null,
      connector_format: document.querySelector('#source-connector-format').value,
      connector_config: config,
      schedule_interval_minutes: rawInterval === '' ? null : Number(rawInterval),
    };
    const saved = await api(`/api/fip/sources/${sourceId}/ingestion-settings`, { method: 'PUT', role: 'admin', body });
    const approvalNote = saved.ingestion_config_status === 'PENDING_REVIEW' && saved.connector_format !== 'manual_only'
      ? ' Connector approval is pending; any schedule is paused and must be re-enabled after approval.' : '';
    setNotice(`Source ingestion settings saved.${approvalNote}`, 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error instanceof SyntaxError ? 'Connector configuration must be valid JSON.' : error.message, 'error'); }
});

document.querySelector('#approve-source-connector').addEventListener('click', async () => {
  const sourceId = document.querySelector('#ingestion-source').value;
  if (!sourceId) { setNotice('Choose a source first.', 'error'); return; }
  try {
    const reason = window.prompt('Reason for approving this source connector configuration:');
    if (reason === null || !reason.trim()) return;
    await api(`/api/fip/sources/${sourceId}/ingestion-settings/approve`, {
      method: 'POST', role: 'reviewer', body: { reason: reason.trim() },
    });
    setNotice('Source connector approved. Scheduled or on-demand retrieval may now use it.', 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#queue-ingestion-job').addEventListener('click', async () => {
  const sourceId = document.querySelector('#ingestion-job-source').value;
  if (!sourceId) { setNotice('Choose a source for the ingestion job.', 'error'); return; }
  try {
    const requestedMode = document.querySelector('#ingestion-job-mode').value || null;
    const job = await api('/api/fip/ingestion/jobs', {
      method: 'POST', role: 'admin', body: { source_id: sourceId, mode: requestedMode },
    });
    if (job.mode === 'MANUAL') {
      activeIngestionJob = { id: job.id, source_id: job.source_id, mode: job.mode, exceptions: [] };
      document.querySelector('#active-ingestion-job').textContent = `Active MANUAL job ${job.id}. Enter observations below and select source ${job.source_id}.`;
      document.querySelector('#observation-source').value = job.source_id;
      setNotice(`Manual job ${job.id} queued. It will not fetch the source; add figures using the form below.`, 'success');
    } else {
      const result = await api(`/api/fip/ingestion/jobs/${job.id}/run`, { method: 'POST', role: 'admin' });
      setNotice(`${result.mode} job ${result.id}: ${result.status.toLowerCase()} · ${result.observations_created} observations created.`, result.status === 'FAILED' ? 'error' : 'success');
    }
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#entity-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await api('/api/fip/entities', { method: 'POST', role: 'admin', body: formObject(event.currentTarget) });
    event.currentTarget.reset();
    setNotice('Identity record created in NEEDS_REVIEW state.', 'success');
    await refreshWorkspace(); await refreshPublic();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#source-form').addEventListener('submit', async event => {
  event.preventDefault();
  const body = formObject(event.currentTarget);
  body.publication_date ||= null;
  try {
    await api('/api/fip/sources', { method: 'POST', role: 'admin', body });
    event.currentTarget.reset();
    setNotice('Source registered in PENDING_REVIEW state.', 'success');
    await refreshWorkspace(); await refreshPublic();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#fx-form').addEventListener('submit', async event => {
  event.preventDefault();
  const body = formObject(event.currentTarget);
  body.source_currency = body.source_currency.toUpperCase();
  body.target_currency = body.target_currency.toUpperCase();
  try {
    await api('/api/fip/fx-rates', { method: 'POST', role: 'admin', body });
    event.currentTarget.reset();
    setNotice('FX rate registered in PENDING_REVIEW state.', 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#alias-form').addEventListener('submit', async event => {
  event.preventDefault();
  const body = formObject(event.currentTarget);
  try {
    await api(`/api/fip/entities/${body.entity_id}/aliases`, { method: 'POST', role: 'admin', body });
    event.currentTarget.reset();
    setNotice('Entity alias added in NEEDS_REVIEW state.', 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#relationship-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await api('/api/fip/relationships', { method: 'POST', role: 'admin', body: formObject(event.currentTarget) });
    event.currentTarget.reset();
    setNotice('Entity relationship added in NEEDS_REVIEW state.', 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#mapping-form').addEventListener('submit', async event => {
  event.preventDefault();
  const body = formObject(event.currentTarget);
  try {
    await api('/api/fip/metric-mappings', { method: 'POST', role: 'admin', body });
    event.currentTarget.reset();
    document.querySelector('#mapping-domain').value = 'corporate';
    updateMappingChoices();
    setNotice('Metric mapping proposed for reviewer approval.', 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#observation-form').addEventListener('submit', async event => {
  event.preventDefault();
  const body = formObject(event.currentTarget);
  body.metric_id = body.metric_id;
  if (body.domain !== 'government') { body.fiscal_scope = ''; body.budget_stage = ''; }
  try {
    if (activeIngestionJob?.mode === 'MANUAL') {
      await api(`/api/fip/ingestion/jobs/${activeIngestionJob.id}/observations`, {
        method: 'POST', role: 'admin', body: { observation: body },
      });
    } else if (activeIngestionJob?.mode === 'HYBRID') {
      const openException = activeIngestionJob.exceptions.find(item => item.status === 'OPEN');
      const defaultRow = openException?.row_number || '';
      const rowNumber = Number(window.prompt('Exception row number to correct:', defaultRow));
      if (!Number.isInteger(rowNumber) || rowNumber < 1) return;
      await api(`/api/fip/ingestion/jobs/${activeIngestionJob.id}/corrections`, {
        method: 'POST', role: 'admin', body: { row_number: rowNumber, observation: body },
      });
    } else {
      await api('/api/fip/observations', { method: 'POST', role: 'admin', body });
    }
    event.currentTarget.reset();
    if (activeIngestionJob) {
      const completedMode = activeIngestionJob.mode;
      activeIngestionJob = null;
      document.querySelector('#active-ingestion-job').textContent = `Last ${completedMode} record saved. Select the job again for more entries or corrections.`;
    }
    document.querySelector('#observation-domain').value = 'corporate';
    updateMetricChoices();
    setNotice('Observation saved with its ingestion job and evidence lineage. Reviewer validation and approval are still required.', 'success');
    await refreshWorkspace(); await refreshPublic();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#calculation-form').addEventListener('submit', async event => {
  event.preventDefault();
  const body = formObject(event.currentTarget);
  body.prior_period_id ||= null;
  try {
    const result = await api('/api/fip/calculations', { method: 'POST', role: 'admin', body });
    const names = Object.values(result.metrics).map(metric => metric.label).join(', ');
    setNotice(`Calculation ${result.id} created for reviewer approval. Metrics: ${names}.`, 'success');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

document.querySelector('#run-reconciliation').addEventListener('click', async () => {
  const form = document.querySelector('#calculation-form');
  const body = formObject(form);
  const output = document.querySelector('#reconciliation-output');
  try {
    const result = await api('/api/fip/reconciliation', { method: 'POST', role: 'admin', body });
    output.textContent = JSON.stringify(result, null, 2);
    output.hidden = false;
    setNotice(`Reconciliation result: ${result.overall_status}. Missing or conflicting components remain explicit.`, result.overall_status === 'PASS' ? 'success' : '');
    await refreshWorkspace();
  } catch (error) { setNotice(error.message, 'error'); }
});

(async () => {
  try { await loadCatalog(); await refreshPublic(); await refreshReadiness(); }
  catch (error) { setNotice(error.message, 'error'); }
})();

window.setInterval(() => {
  if (adminToken.value || reviewerToken.value) refreshIngestionJobs().catch(() => {});
}, 30_000);
