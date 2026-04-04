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

// Dropdown toggle
function toggleDropdown(event) {
  event.stopPropagation();
  var menu = event.currentTarget.nextElementSibling;
  // Close any other open dropdowns
  document.querySelectorAll('.dropdown-menu').forEach(function (el) {
    if (el !== menu) el.classList.add('hidden');
  });
  menu.classList.toggle('hidden');
}

document.addEventListener('click', function () {
  document.querySelectorAll('.dropdown-menu').forEach(function (el) {
    el.classList.add('hidden');
  });
});

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

// Set backup label
function setLabel(backupId, currentLabel) {
  var label = prompt('Enter label for this backup:', currentLabel || '');
  if (label === null) return; // cancelled
  fetch('/api/backup/' + backupId + '/label/', {
    method: 'POST',
    headers: {
      'X-CSRFToken': getCsrfToken(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ label: label }),
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.status === 'success') {
      location.reload();
    } else {
      alert(data.message || 'Failed to set label');
    }
  })
  .catch(function () {
    alert('Request failed');
  });
}

// Set backup notes (modal with textarea)
function setNotes(backupId, currentNotes) {
  // Create modal overlay
  var overlay = document.createElement('div');
  overlay.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
  overlay.onclick = function (e) { if (e.target === overlay) close(); };

  var card = document.createElement('div');
  card.className = 'mx-4 w-full max-w-lg rounded-lg border border-gray-200 bg-white p-5 shadow-lg dark:border-gray-700 dark:bg-gray-800';

  var title = document.createElement('h3');
  title.className = 'mb-3 text-lg font-semibold text-gray-900 dark:text-gray-100';
  title.textContent = 'Edit Notes';

  var textarea = document.createElement('textarea');
  textarea.className = 'w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200';
  textarea.rows = 6;
  textarea.value = currentNotes || '';
  textarea.placeholder = 'Add notes about this backup...';

  var btnRow = document.createElement('div');
  btnRow.className = 'mt-3 flex justify-end gap-2';

  var cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn-secondary';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = close;

  var saveBtn = document.createElement('button');
  saveBtn.className = 'btn-primary';
  saveBtn.textContent = 'Save';
  saveBtn.onclick = function () {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';
    fetch('/api/backup/' + backupId + '/notes/', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ notes: textarea.value }),
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.status === 'success') {
        location.reload();
      } else {
        alert(data.message || 'Failed to save notes');
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save';
      }
    })
    .catch(function () {
      alert('Request failed');
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save';
    });
  };

  btnRow.appendChild(cancelBtn);
  btnRow.appendChild(saveBtn);
  card.appendChild(title);
  card.appendChild(textarea);
  card.appendChild(btnRow);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  textarea.focus();

  function close() {
    document.body.removeChild(overlay);
  }
}

// Toggle pin
function togglePin(backupId) {
  fetch('/api/backup/' + backupId + '/pin/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.status === 'success') {
      location.reload();
    } else {
      alert(data.message || 'Failed to toggle pin');
    }
  })
  .catch(function () {
    alert('Request failed');
  });
}

// Delete backup
function deleteBackup(backupId, filename) {
  if (!confirm('Delete backup ' + filename + '?\n\nThis cannot be undone.')) {
    return;
  }
  var form = document.createElement('form');
  form.method = 'POST';
  form.action = '/backup/' + backupId + '/delete/';
  var csrf = document.createElement('input');
  csrf.type = 'hidden';
  csrf.name = 'csrfmiddlewaretoken';
  csrf.value = getCsrfToken();
  form.appendChild(csrf);
  document.body.appendChild(form);
  form.submit();
}

// Navigate to diff comparison
function compareDiff(backupId) {
  var select = document.getElementById('compare-select');
  var compareId = select.value;
  if (compareId) {
    window.location.href = '/diff/' + backupId + '/' + compareId + '/';
  }
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
