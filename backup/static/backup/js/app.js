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

// Instance URL bases from meta tags
function getApiBase() {
  var meta = document.querySelector('meta[name="instance-api-base"]');
  return meta ? meta.content : '/api/';
}

function getInstanceBase() {
  var meta = document.querySelector('meta[name="instance-base"]');
  return meta ? meta.content : '/';
}

// In-page alert banner (replaces browser alert())
function showBanner(message, type) {
  type = type || 'error';
  var colors = {
    success: 'border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-900/30 dark:text-green-200',
    error:   'border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-900/30 dark:text-red-200',
    warning: 'border-yellow-200 bg-yellow-50 text-yellow-800 dark:border-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-200',
    info:    'border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-200',
  };
  var dismiss = {
    success: 'text-green-600 hover:text-green-800 dark:text-green-400 dark:hover:text-green-200',
    error:   'text-red-600 hover:text-red-800 dark:text-red-400 dark:hover:text-red-200',
    warning: 'text-yellow-600 hover:text-yellow-800 dark:text-yellow-400 dark:hover:text-yellow-200',
    info:    'text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-200',
  };

  var div = document.createElement('div');
  div.className = 'mb-4 flex items-center justify-between rounded-lg border px-4 py-3 text-sm ' + (colors[type] || colors.error);
  div.setAttribute('role', 'alert');

  var span = document.createElement('span');
  span.textContent = message;

  var btn = document.createElement('button');
  btn.className = 'ml-4 ' + (dismiss[type] || dismiss.error);
  btn.textContent = '\u00D7';
  btn.onclick = function () { div.remove(); };

  div.appendChild(span);
  div.appendChild(btn);

  var main = document.querySelector('main');
  if (main) {
    main.insertBefore(div, main.firstChild);
    div.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

// Dropdown toggle
function toggleDropdown(event) {
  event.stopPropagation();
  var menu = event.currentTarget.nextElementSibling;
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

// Dismiss error banner
function dismissError() {
  var banner = document.getElementById('error-banner');
  if (banner) banner.remove();
  var apiBase = getApiBase();
  if (apiBase) {
    fetch(apiBase + 'clear-error/', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
    });
  }
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
      showBanner(data.message || 'Backup failed');
      btn.disabled = false;
      btn.textContent = 'Create Backup';
    } else {
      location.reload();
    }
  })
  .catch(function () {
    showBanner('Request failed');
    btn.disabled = false;
    btn.textContent = 'Create Backup';
  });
}

// Set backup label
function setLabel(backupId, currentLabel) {
  var label = prompt('Enter label for this backup:', currentLabel || '');
  if (label === null) return;
  fetch(getApiBase() + 'backup/' + backupId + '/label/', {
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
      showBanner(data.message || 'Failed to set label');
    }
  })
  .catch(function () {
    showBanner('Request failed');
  });
}

// Set backup notes (modal with textarea)
function setNotes(backupId, currentNotes) {
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
    fetch(getApiBase() + 'backup/' + backupId + '/notes/', {
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
        close();
        showBanner(data.message || 'Failed to save notes');
      }
    })
    .catch(function () {
      close();
      showBanner('Request failed');
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
  fetch(getApiBase() + 'backup/' + backupId + '/pin/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.status === 'success') {
      location.reload();
    } else {
      showBanner(data.message || 'Failed to toggle pin');
    }
  })
  .catch(function () {
    showBanner('Request failed');
  });
}

// Delete backup
function deleteBackup(backupId, filename) {
  if (!confirm('Delete backup ' + filename + '?\n\nThis cannot be undone.')) {
    return;
  }
  var form = document.createElement('form');
  form.method = 'POST';
  form.action = getInstanceBase() + 'backup/' + backupId + '/delete/';
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
    window.location.href = getInstanceBase() + 'diff/' + backupId + '/' + compareId + '/';
  }
}

// Bulk actions
function toggleSelectAll(checkbox) {
  document.querySelectorAll('.backup-checkbox').forEach(function (cb) {
    cb.checked = checkbox.checked;
  });
  updateBulkBar();
}

function updateBulkBar() {
  var count = getSelectedIds().length;
  var bar = document.getElementById('bulk-bar');
  var label = document.getElementById('bulk-count');
  if (!bar) return;
  if (count > 0) {
    bar.classList.remove('hidden');
    label.textContent = count + ' selected';
  } else {
    bar.classList.add('hidden');
  }
  var all = document.querySelectorAll('.backup-checkbox');
  var selectAll = document.getElementById('select-all');
  if (selectAll && all.length) {
    selectAll.checked = count === all.length;
  }
  var compareBtn = document.getElementById('bulk-compare');
  if (compareBtn) {
    if (count === 2) {
      compareBtn.classList.remove('hidden');
    } else {
      compareBtn.classList.add('hidden');
    }
  }
}

function getSelectedIds() {
  var ids = [];
  document.querySelectorAll('.backup-checkbox:checked').forEach(function (cb) {
    ids.push(parseInt(cb.value, 10));
  });
  return ids;
}

function bulkAction(action) {
  var ids = getSelectedIds();
  if (!ids.length) return;
  return fetch(getApiBase() + 'bulk/', {
    method: 'POST',
    headers: {
      'X-CSRFToken': getCsrfToken(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ids: ids, action: action }),
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.errors && data.errors.length) {
      showBanner(data.affected + ' succeeded, ' + data.errors.length + ' failed: ' + data.errors.join(', '), 'warning');
    }
    location.reload();
  })
  .catch(function () {
    showBanner('Request failed');
  });
}

function bulkPin() { bulkAction('pin'); }
function bulkUnpin() { bulkAction('unpin'); }

function bulkDelete() {
  var ids = getSelectedIds();
  if (!ids.length) return;
  if (!confirm('Delete ' + ids.length + ' backup' + (ids.length > 1 ? 's' : '') + '?\n\nThis cannot be undone.')) return;
  bulkAction('delete');
}

function bulkCompare() {
  var ids = getSelectedIds();
  if (ids.length !== 2) return;
  var a = Math.min(ids[0], ids[1]);
  var b = Math.max(ids[0], ids[1]);
  window.location.href = getInstanceBase() + 'diff/' + b + '/' + a + '/';
}

function bulkDownload() {
  var ids = getSelectedIds();
  ids.forEach(function (id) {
    window.open(getInstanceBase() + 'backup/' + id + '/download/', '_blank');
  });
}

// Test notification
function testNotification() {
  var btn = document.getElementById('btn-test-notification');
  btn.disabled = true;
  btn.textContent = 'Sending...';
  fetch(getApiBase() + 'notifications/test/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    btn.disabled = false;
    btn.textContent = 'Send Test Notification';
    if (data.status === 'success') {
      showBanner('Test notification sent to: ' + data.backends.join(', '), 'success');
    } else if (data.status === 'partial') {
      showBanner('Sent to ' + data.backends.join(', ') + '. Errors: ' + data.errors.join(', '), 'warning');
    } else {
      showBanner(data.message || data.errors.join(', '));
    }
  })
  .catch(function () {
    btn.disabled = false;
    btn.textContent = 'Send Test Notification';
    showBanner('Request failed');
  });
}

// Import backup (upload archive)
function importBackup() {
  var overlay = document.createElement('div');
  overlay.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
  overlay.onclick = function (e) { if (e.target === overlay) close(); };

  var card = document.createElement('div');
  card.className = 'mx-4 w-full max-w-lg rounded-lg border border-gray-200 bg-white p-5 shadow-lg dark:border-gray-700 dark:bg-gray-800';

  var title = document.createElement('h3');
  title.className = 'mb-3 text-lg font-semibold text-gray-900 dark:text-gray-100';
  title.textContent = 'Import Backup';

  // File input
  var fileLabel = document.createElement('label');
  fileLabel.className = 'mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300';
  fileLabel.textContent = 'Archive file (.tar.gz)';

  var fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = '.tar.gz,.tgz';
  fileInput.className = 'mb-3 block w-full text-sm text-gray-500 file:mr-3 file:rounded-md file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-700 hover:file:bg-blue-100 dark:text-gray-400 dark:file:bg-blue-900/30 dark:file:text-blue-300';

  // Label input
  var labelLabel = document.createElement('label');
  labelLabel.className = 'mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300';
  labelLabel.textContent = 'Label (optional)';

  var labelInput = document.createElement('input');
  labelInput.type = 'text';
  labelInput.maxLength = 200;
  labelInput.placeholder = 'e.g. Migrated from server-2';
  labelInput.className = 'mb-3 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200';

  // Notes textarea
  var notesLabel = document.createElement('label');
  notesLabel.className = 'mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300';
  notesLabel.textContent = 'Notes (optional)';

  var notesInput = document.createElement('textarea');
  notesInput.rows = 3;
  notesInput.placeholder = 'Add notes about this import...';
  notesInput.className = 'mb-3 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200';

  // Buttons
  var btnRow = document.createElement('div');
  btnRow.className = 'mt-1 flex justify-end gap-2';

  var cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn-secondary';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = close;

  var importBtn = document.createElement('button');
  importBtn.className = 'btn-primary';
  importBtn.textContent = 'Import';
  importBtn.onclick = function () {
    if (!fileInput.files || !fileInput.files[0]) {
      showBanner('Please select a file to import');
      return;
    }
    importBtn.disabled = true;
    importBtn.textContent = 'Importing...';

    var formData = new FormData();
    formData.append('archive', fileInput.files[0]);
    formData.append('label', labelInput.value);
    formData.append('notes', notesInput.value);

    fetch(getApiBase() + 'import/', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() },
      body: formData,
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.status === 'success') {
        close();
        if (data.backup && data.backup.duplicate_warning) {
          showBanner(data.backup.duplicate_warning, 'warning');
        }
        showBanner('Backup imported: ' + data.backup.filename, 'success');
        setTimeout(function () { location.reload(); }, 1500);
      } else {
        importBtn.disabled = false;
        importBtn.textContent = 'Import';
        close();
        showBanner(data.message || 'Import failed');
      }
    })
    .catch(function () {
      importBtn.disabled = false;
      importBtn.textContent = 'Import';
      close();
      showBanner('Request failed');
    });
  };

  btnRow.appendChild(cancelBtn);
  btnRow.appendChild(importBtn);

  card.appendChild(title);
  card.appendChild(fileLabel);
  card.appendChild(fileInput);
  card.appendChild(labelLabel);
  card.appendChild(labelInput);
  card.appendChild(notesLabel);
  card.appendChild(notesInput);
  card.appendChild(btnRow);
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  function close() {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
  }
}

// Restore backup
function restoreBackup(id, filename) {
  if (!confirm('Restore from ' + filename + '?\n\nThis will overwrite current Node-RED files. A safety backup will be created first.')) {
    return;
  }
  fetch(getApiBase() + 'restore/' + id + '/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.status === 'success') {
      showBanner('Restore complete. Files restored: ' + data.restore.files_restored.join(', '), 'success');
      setTimeout(function () { location.reload(); }, 2000);
    } else {
      showBanner('Restore failed: ' + (data.message || 'Unknown error'));
    }
  })
  .catch(function () {
    showBanner('Request failed');
  });
}
