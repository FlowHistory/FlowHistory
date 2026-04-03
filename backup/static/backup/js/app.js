// Dark mode toggle
(function () {
  const toggle = document.getElementById('theme-toggle');
  const icon = document.getElementById('theme-icon');
  if (!toggle) return;

  const saved = localStorage.getItem('theme');
  if (saved) {
    document.documentElement.setAttribute('data-bs-theme', saved);
  }
  updateIcon();

  toggle.addEventListener('click', function () {
    const current = document.documentElement.getAttribute('data-bs-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    updateIcon();
  });

  function updateIcon() {
    const theme = document.documentElement.getAttribute('data-bs-theme');
    icon.textContent = theme === 'dark' ? '☀️' : '🌙';
  }
})();
