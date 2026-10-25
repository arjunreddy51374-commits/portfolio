const assessmentForm = document.querySelector('#assessment-form');
const resultPanel = document.querySelector('#prediction-result');
const imageReviewForm = document.querySelector('#image-review-form');
const imageFileInput = document.querySelector('#image-file');
const imagePreview = document.querySelector('#image-preview');
const imageEmptyState = document.querySelector('#image-empty-state');
const imageFileDetails = document.querySelector('#image-file-details');
const imageModality = document.querySelector('#image-modality');
const analyzeMammogramButton = document.querySelector('#analyze-mammogram');
const imageAnalysisResult = document.querySelector('#image-analysis-result');
const imageModelReady = document.querySelector('#main').dataset.imageModelReady === 'true';
let previewObjectUrl = null;

function updateImageAnalysisControl() {
  const isMammography = imageModality.value === 'Mammography';
  analyzeMammogramButton.hidden = !isMammography;
  analyzeMammogramButton.disabled = !imageModelReady || !imageFileInput.files?.length;
}

function clearImagePreview() {
  if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
  previewObjectUrl = null;
  imagePreview.removeAttribute('src');
  imagePreview.hidden = true;
  imageEmptyState.hidden = false;
  imageFileDetails.hidden = true;
  imageFileDetails.textContent = '';
}

imageFileInput.addEventListener('change', () => {
  clearImagePreview();
  updateImageAnalysisControl();
  const file = imageFileInput.files?.[0];
  if (!file) return;

  const supportedTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp', 'image/tiff'];
  if (!supportedTypes.includes(file.type)) {
    imageFileInput.value = '';
    imageFileDetails.textContent = 'Unsupported file type. Choose a JPEG, PNG, WebP, BMP, or TIFF image.';
    imageFileDetails.hidden = false;
    return;
  }
  if (file.size > 12 * 1024 * 1024) {
    imageFileInput.value = '';
    updateImageAnalysisControl();
    imageFileDetails.textContent = 'File exceeds the 12 MB upload limit.';
    imageFileDetails.hidden = false;
    return;
  }

  previewObjectUrl = URL.createObjectURL(file);
  imagePreview.onload = () => {
    imageEmptyState.hidden = true;
    imagePreview.hidden = false;
    imageFileDetails.textContent = `${file.name} · ${imagePreview.naturalWidth} × ${imagePreview.naturalHeight} px · ${(file.size / 1024).toFixed(1)} KB · preview stays in this browser`;
    imageFileDetails.hidden = false;
  };
  imagePreview.onerror = () => {
    clearImagePreview();
    imageFileDetails.textContent = 'This image could not be decoded by the browser.';
    imageFileDetails.hidden = false;
  };
  imagePreview.src = previewObjectUrl;
});

imageModality.addEventListener('change', updateImageAnalysisControl);

analyzeMammogramButton.addEventListener('click', async () => {
  if (!imageModelReady || imageModality.value !== 'Mammography' || !imageFileInput.files?.[0]) return;

  const file = imageFileInput.files[0];
  const formData = new FormData();
  formData.append('modality', 'Mammography');
  formData.append('image', file);
  analyzeMammogramButton.disabled = true;
  analyzeMammogramButton.setAttribute('aria-busy', 'true');
  imageAnalysisResult.replaceChildren();
  const pending = document.createElement('p');
  pending.textContent = 'Running the optional local mammography research model…';
  imageAnalysisResult.append(pending);
  imageAnalysisResult.hidden = false;

  try {
    const response = await fetch('/predict-image', { method: 'POST', body: formData });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Image analysis failed.');

    const heading = document.createElement('h3');
    heading.textContent = `Model output: ${data.prediction}`;
    const scores = document.createElement('p');
    scores.textContent = `Mammography model scores: ${Object.entries(data.model_scores).map(([label, score]) => `${label}: ${score}`).join(' · ')}`;
    const warning = document.createElement('p');
    warning.className = 'result-warning';
    warning.textContent = data.disclaimer;
    imageAnalysisResult.replaceChildren(heading, scores, warning);
  } catch (error) {
    const message = document.createElement('p');
    message.textContent = error.message || 'Image analysis failed.';
    imageAnalysisResult.replaceChildren(message);
  } finally {
    analyzeMammogramButton.removeAttribute('aria-busy');
    updateImageAnalysisControl();
  }
});

imageReviewForm.addEventListener('reset', () => {
  clearImagePreview();
  imageAnalysisResult.replaceChildren();
  imageAnalysisResult.hidden = true;
  setTimeout(updateImageAnalysisControl, 0);
});

function clearFieldErrors() {
  assessmentForm.querySelectorAll('[aria-invalid="true"]').forEach(input => input.removeAttribute('aria-invalid'));
  assessmentForm.querySelectorAll('.field-error').forEach(error => { error.textContent = ''; });
}

function showResult(title, message, warning = '', focusResult = true) {
  resultPanel.replaceChildren();
  const heading = document.createElement('h3');
  heading.textContent = title;
  resultPanel.append(heading);
  if (message) {
    const paragraph = document.createElement('p');
    paragraph.textContent = message;
    resultPanel.append(paragraph);
  }
  if (warning) {
    const note = document.createElement('p');
    note.className = 'result-warning';
    note.textContent = warning;
    resultPanel.append(note);
  }
  resultPanel.hidden = false;
  if (focusResult) resultPanel.focus();
}

assessmentForm.addEventListener('reset', () => {
  clearFieldErrors();
  resultPanel.hidden = true;
});

assessmentForm.addEventListener('submit', async event => {
  event.preventDefault();
  clearFieldErrors();

  const inputs = [...assessmentForm.querySelectorAll('input[required]')];
  const invalid = inputs.filter(input => !input.value || !Number.isFinite(Number(input.value)) || Number(input.value) < 0 || Number(input.value) > 1000000);
  if (invalid.length) {
    invalid.forEach(input => {
      input.setAttribute('aria-invalid', 'true');
      input.parentElement.querySelector('.field-error').textContent = 'Enter a number from 0 to 1,000,000.';
    });
    invalid[0].focus();
    showResult('Check the highlighted fields', `${invalid.length} required value${invalid.length === 1 ? '' : 's'} need attention.`, '', false);
    return;
  }

  const submitButton = assessmentForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.classList.add('is-loading');
  submitButton.setAttribute('aria-busy', 'true');
  submitButton.querySelector('.button-label').textContent = 'Evaluating…';
  showResult('Assessment in progress', 'Submitting structured values to the local research model.');
  try {
    const values = Object.fromEntries(new FormData(assessmentForm).entries());
    const response = await fetch('/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(values)
    });
    const data = await response.json();
    if (!response.ok) throw new Error((data.details || [data.error || 'Prediction request failed.']).join(' '));

    const scores = data.model_output;
    showResult(
      data.prediction,
      `${data.model_name} · ${data.model_version}. Model scores — malignant: ${scores.malignant_score}; benign: ${scores.benign_score}. ${scores.interpretation}`,
      data.disclaimer
    );
  } catch (error) {
    showResult('Unable to complete assessment', error.message || 'Check your connection and try again.');
  } finally {
    submitButton.disabled = false;
    submitButton.classList.remove('is-loading');
    submitButton.removeAttribute('aria-busy');
    submitButton.querySelector('.button-label').textContent = 'Run research demo';
  }
});