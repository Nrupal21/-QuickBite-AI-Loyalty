/* QuickBite AI + Loyalty — Super Admin tenants page: health KPI strip,
 * tenant table, suspend/reactivate, and the inline (not modal — matches
 * settings.html's invite-panel convention) subscription-override form.
 */
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

    function loadMetrics() {
      admin.apiFetch('/admin/health-metrics').then(function (data) {
        Object.keys(data).forEach(function (key) {
          var el = document.querySelector('[data-metric="' + key + '"]');
          if (el) el.textContent = data[key];
        });
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    var rowsMount = document.querySelector('[data-tenant-rows]');
    var template = document.getElementById('tenant-row-template');
    var allTenants = [];

    function renderRow(tenant) {
      var rowFrag = template.content.cloneNode(true);
      var mainRow = rowFrag.querySelector('tr:first-child');
      var overrideRow = rowFrag.querySelector('[data-override-row]');

      mainRow.querySelector('[data-cell="name"]').textContent = tenant.name;
      mainRow.querySelector('[data-cell="subdomain"]').textContent = tenant.subdomain;

      var badge = mainRow.querySelector('[data-cell="status-badge"]');
      badge.textContent = tenant.is_active ? 'Active' : 'Suspended';
      badge.className = 'text-xs font-bold px-2.5 py-1 rounded-full ' +
        (tenant.is_active ? 'bg-brand-success/10 text-brand-success' : 'bg-brand-danger/10 text-brand-danger');

      var toggleBtn = mainRow.querySelector('[data-action="toggle-status"]');
      toggleBtn.textContent = tenant.is_active ? 'Suspend' : 'Reactivate';
      toggleBtn.addEventListener('click', function () {
        var nextState = !tenant.is_active;
        var verb = nextState ? 'reactivate' : 'suspend';
        if (!window.confirm('Are you sure you want to ' + verb + ' ' + tenant.name + '?')) return;
        admin.apiFetch('/admin/tenants/' + tenant.tenant_id + '/status', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_active: nextState }),
        }).then(function () {
          tenant.is_active = nextState;
          loadTenants();
          loadMetrics();
        }).catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        });
      });

      var overrideToggle = mainRow.querySelector('[data-action="toggle-override"]');
      overrideToggle.addEventListener('click', function () {
        overrideRow.hidden = !overrideRow.hidden;
      });

      var overrideForm = overrideRow.querySelector('[data-override-form]');
      overrideForm.addEventListener('submit', function (event) {
        event.preventDefault();
        var formData = new FormData(overrideForm);
        var body = { reason: formData.get('reason') };
        var status = formData.get('status');
        if (status) body.status = status;
        admin.apiFetch('/admin/tenants/' + tenant.tenant_id + '/subscription', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }).then(function () {
          overrideRow.hidden = true;
          overrideForm.reset();
        }).catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        });
      });

      return rowFrag;
    }

    function renderTenants(list) {
      rowsMount.innerHTML = '';
      list.forEach(function (tenant) {
        rowsMount.appendChild(renderRow(tenant));
      });
    }

    function loadTenants() {
      return admin.apiFetch('/admin/tenants').then(function (data) {
        allTenants = data.tenants;
        renderTenants(allTenants);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    var searchInput = document.querySelector('[data-tenant-search]');
    if (searchInput) {
      searchInput.addEventListener('input', function () {
        var term = searchInput.value.trim().toLowerCase();
        var filtered = allTenants.filter(function (t) {
          return t.name.toLowerCase().indexOf(term) !== -1 ||
            t.subdomain.toLowerCase().indexOf(term) !== -1;
        });
        renderTenants(filtered);
      });
    }

    loadMetrics();
    loadTenants();
  });
})();
