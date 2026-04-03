// Dark mode toggle
(function () {
  var saved = localStorage.getItem('theme');
  if (saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    document.documentElement.classList.add('dark');
  }

  var toggle = document.getElementById('theme-toggle');
  var icon = document.getElementById('theme-icon');
  if (!toggle) return;

  updateIcon();

  toggle.addEventListener('click', function () {
    var isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
    updateIcon();
  });

  function updateIcon() {
    icon.textContent = document.documentElement.classList.contains('dark') ? '\u2600\uFE0F' : '\uD83C\uDF19';
  }
})();

// CSRF helper
function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]').content;
}

// Create backup
function createBackup() {
  var btn = document.getElementById('btn-backup');
  btn.disabled = true;
  btn.textContent = 'Creating...';
  fetch(btn.dataset.url, {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.status === 'error') {
      alert(data.message || 'Backup failed');
    }
    location.reload();
  })
  .catch(function () {
    alert('Request failed');
    btn.disabled = false;
    btn.textContent = 'Create Backup';
  });
}

// Restore backup
function restoreBackup(id, filename) {
  if (!confirm('Restore from ' + filename + '?\n\nThis will overwrite current Node-RED files. A safety backup will be created first.')) {
    return;
  }
  fetch('/api/restore/' + id + '/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.status === 'success') {
      alert('Restore complete. Files restored: ' + data.restore.files_restored.join(', '));
    } else {
      alert('Restore failed: ' + (data.message || 'Unknown error'));
    }
    location.reload();
  })
  .catch(function () {
    alert('Request failed');
  });
}
