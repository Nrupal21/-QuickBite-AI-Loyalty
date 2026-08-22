/* QuickBite AI + Loyalty — Super Admin audit log viewer. */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var admin = window.QuickBiteAdmin;
    if (!admin) return;

    function showError(message) {
      var box = document.getElementById('admin-error');
      if (!box) return;
      box.textContent = message;
      box.classList.remove('hidden');
    }

    var rowsMount = document.querySelector('[data-log-rows]');
    var emptyState = document.querySelector('[data-log-empty]');

    function renderEntries(entries) {
      rowsMount.innerHTML = '';
      emptyState.classList.toggle('hidden', entries.length > 0);
      entries.forEach(function (entry) {
        var tr = document.createElement('tr');
        tr.className = 'border-b border-outline-variant/10';
        tr.innerHTML =
          '<td class="px-5 py-3 text-brand-muted">' + new Date(entry.created_at).toLocaleString() + '</td>' +
          '<td class="px-5 py-3 font-mono text-xs text-brand-ink">' + entry.action + '</td>' +
          '<td class="px-5 py-3 text-brand-muted">' + (entry.resource_type || '—') + '</td>' +
          '<td class="px-5 py-3 text-brand-muted font-mono text-xs">' + (entry.tenant_id || '—') + '</td>';
        rowsMount.appendChild(tr);
      });
    }

    function loadLogs(params) {
      var query = new URLSearchParams();
      Object.keys(params || {}).forEach(function (key) {
        if (params[key]) query.set(key, params[key]);
      });
      var qs = query.toString();
      return admin.apiFetch('/admin/audit-logs' + (qs ? '?' + qs : '')).then(function (data) {
        renderEntries(data.entries);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    var form = document.querySelector('[data-filter-form]');
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var formData = new FormData(form);
      loadLogs({
        action: formData.get('action'),
        tenant_id: formData.get('tenant_id'),
        date_from: formData.get('date_from'),
        date_to: formData.get('date_to'),
      });
    });

    loadLogs({});
  });
})();
