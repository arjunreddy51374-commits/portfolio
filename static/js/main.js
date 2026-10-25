const header = document.querySelector('#siteHeader');
addEventListener('scroll', () => header.classList.toggle('scrolled', scrollY > 16));
const cinematicHero = document.querySelector('.hero-cinematic');
const setCinematicEntryState = () => {
  if (cinematicHero) document.body.classList.toggle('cinematic-entry', scrollY < cinematicHero.offsetHeight * .78);
};
setCinematicEntryState();
addEventListener('scroll', setCinematicEntryState, { passive: true });
const enterWorld = document.querySelector('.enter-world');
const journeyTransition = document.querySelector('#journeyTransition');
if (enterWorld && journeyTransition) {
  enterWorld.addEventListener('click', event => {
    event.preventDefault();
    if (document.body.classList.contains('journey-running')) return;
    const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduceMotion) { document.querySelector('#about').scrollIntoView(); return; }
    document.body.classList.add('journey-running');
    journeyTransition.setAttribute('aria-hidden', 'false');
    setTimeout(() => {
      document.querySelector('#about').scrollIntoView({ behavior: 'instant', block: 'start' });
      document.body.classList.remove('journey-running');
      document.body.classList.add('journey-complete');
      journeyTransition.setAttribute('aria-hidden', 'true');
    }, 2400);
  });
}
const nav = document.querySelector('#navLinks');
document.querySelector('#navToggle').addEventListener('click', () => nav.classList.toggle('open'));
nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => nav.classList.remove('open')));
document.querySelectorAll('.fold-trigger').forEach(trigger => trigger.addEventListener('click', () => {
  const card = trigger.closest('.fold-card'); const open = card.classList.toggle('open'); trigger.setAttribute('aria-expanded', open);
}));
const observer = new IntersectionObserver(entries => entries.forEach(entry => { if(entry.isIntersecting){entry.target.classList.add('visible');observer.unobserve(entry.target)} }), {threshold:.12});
document.querySelectorAll('.reveal').forEach(item => observer.observe(item));
document.querySelector('#year').textContent = new Date().getFullYear();
document.querySelector('#predictionForm').addEventListener('submit', async event => {
  event.preventDefault(); const result = document.querySelector('#predictionResult'); const form = new FormData(event.currentTarget);
  result.textContent = 'Running the model…';
  try { const response = await fetch('/api/predict', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(form))}); const data = await response.json(); if(!response.ok) throw new Error(data.error); result.innerHTML = `<strong>${data.classification}</strong> · ${data.probability}% confidence<br>${data.disclaimer}`; } catch(error) { result.textContent = error.message; }
});
