/* QuickBite AI + Loyalty — Super Admin tenant management: health KPI strip,
 * search/filter/sort tenant list, and a slide-in detail panel per tenant
 * (subscription override, suspend/activate, sync GMB, staff sessions with
 * per-user force-logout). Replaces the old flat-table page wholesale.
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

    function clearError() {
      var box = document.getElementById('admin-error');
      if (!box) return;
      box.textContent = '';
      box.classList.add('hidden');
    }

    function formatDate(iso) {
      if (!iso) return '—';
      return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    }

    function formatRelative(iso) {
      if (!iso) return 'Never';
      var diffMs = Date.now() - new Date(iso).getTime();
      var days = Math.floor(diffMs / 86400000);
      if (days <= 0) return 'Today';
      if (days === 1) return 'Yesterday';
      if (days < 30) return days + ' days ago';
      var months = Math.floor(days / 30);
      if (months < 12) return months + (months === 1 ? ' month ago' : ' months ago');
      var years = Math.floor(months / 12);
      return years + (years === 1 ? ' year ago' : ' years ago');
    }

    // ---------- health KPI strip (unchanged from the old page) ----------

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

    // ---------- tenant list: search / filter / sort ----------

    var rowsMount = document.querySelector('[data-tenant-rows]');
    var emptyState = document.querySelector('[data-tenant-empty]');
    var rowTemplate = document.getElementById('tenant-row-template');
    var allTenants = [];

    var SUBSCRIPTION_BADGE = {
      none: 'bg-brand-muted/10 text-brand-muted',
      trialing: 'bg-brand-accent-orange/10 text-brand-accent-orange',
      active: 'bg-brand-success/10 text-brand-success',
      past_due: 'bg-brand-warning/10 text-brand-warning',
      canceled: 'bg-brand-danger/10 text-brand-danger',
      paused: 'bg-brand-muted/10 text-brand-muted',
    };

    function planBadgeText(tenant) {
      if (!tenant.plan_name) return 'No plan';
      return tenant.plan_name + ' · ' + tenant.subscription_status.replace(/_/g, ' ');
    }

    function renderRow(tenant) {
      var frag = rowTemplate.content.cloneNode(true);
      var row = frag.querySelector('[data-tenant-row]');

      row.querySelector('[data-cell="name"]').textContent = tenant.name;
      row.querySelector('[data-cell="subdomain"]').textContent = tenant.subdomain;

      var badge = row.querySelector('[data-cell="plan-badge"]');
      badge.textContent = planBadgeText(tenant);
      badge.className = 'text-xs font-bold px-2.5 py-1 rounded-full ' +
        (SUBSCRIPTION_BADGE[tenant.subscription_status] || SUBSCRIPTION_BADGE.none);

      row.querySelector('[data-cell="branch-count"]').textContent = tenant.branch_count;
      row.querySelector('[data-cell="staff-count"]').textContent = tenant.staff_count;
      row.querySelector('[data-cell="signup-date"]').textContent = formatDate(tenant.created_at);
      row.querySelector('[data-cell="last-active"]').textContent = formatRelative(tenant.last_active_at);

      row.addEventListener('click', function () {
        openPanel(tenant, row);
      });
      row.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
        event.preventDefault();
        openPanel(tenant, row);
      });

      return frag;
    }

    function renderTenants(list) {
      rowsMount.innerHTML = '';
      list.forEach(function (tenant) {
        rowsMount.appendChild(renderRow(tenant));
      });
      emptyState.classList.toggle('hidden', list.length > 0);
    }

    var searchInput = document.querySelector('[data-tenant-search]');
    var statusFilter = document.querySelector('[data-tenant-status-filter]');
    var planFilter = document.querySelector('[data-tenant-plan-filter]');
    var sortSelect = document.querySelector('[data-tenant-sort]');

    function applyFiltersAndSort() {
      var term = searchInput.value.trim().toLowerCase();
      var status = statusFilter.value;
      var plan = planFilter.value;
      var sort = sortSelect.value;

      var filtered = allTenants.filter(function (t) {
        if (term && t.name.toLowerCase().indexOf(term) === -1 && t.subdomain.toLowerCase().indexOf(term) === -1) return false;
        if (status === 'active' && !t.is_active) return false;
        if (status === 'suspended' && t.is_active) return false;
        if (plan === 'none' && t.plan_name) return false;
        if (plan && plan !== 'none' && t.plan_name !== plan) return false;
        return true;
      });

      filtered.sort(function (a, b) {
        if (sort === 'signup_asc') return new Date(a.created_at) - new Date(b.created_at);
        if (sort === 'active_desc') return new Date(b.last_active_at || 0) - new Date(a.last_active_at || 0);
        if (sort === 'active_asc') return new Date(a.last_active_at || 0) - new Date(b.last_active_at || 0);
        return new Date(b.created_at) - new Date(a.created_at); // signup_desc, default
      });

      renderTenants(filtered);
    }

    [searchInput, statusFilter, planFilter, sortSelect].forEach(function (el) {
      if (el) el.addEventListener(searchInput === el ? 'input' : 'change', applyFiltersAndSort);
    });

    function populatePlanFilterOptions() {
      // Remove all dynamically-added options (keep the 2 static ones: "All plans" and "No plan")
      while (planFilter.options.length > 2) {
        planFilter.remove(2);
      }
      // Now append fresh plan names from current tenant list
      var planNames = Array.from(new Set(allTenants.map(function (t) { return t.plan_name; }).filter(Boolean)));
      planNames.forEach(function (name) {
        var opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        planFilter.appendChild(opt);
      });
    }

    function loadTenants() {
      return admin.apiFetch('/admin/tenants').then(function (data) {
        allTenants = data.tenants;
        populatePlanFilterOptions();
        applyFiltersAndSort();
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    // ---------- detail panel ----------

    var panel = document.querySelector('[data-tenant-panel]');
    var panelScrim = document.querySelector('[data-panel-scrim]');
    var currentTenant = null;
    var lastTriggerElement = null;

    function closePanel() {
      panel.classList.add('hidden');
      panelScrim.classList.add('hidden');
      currentTenant = null;
      if (lastTriggerElement) {
        lastTriggerElement.focus();
        lastTriggerElement = null;
      }
    }

    function openPanel(tenant, triggerElement) {
      currentTenant = tenant;
      lastTriggerElement = triggerElement || null;
      panel.querySelector('[data-panel-name]').textContent = tenant.name;
      panel.querySelector('[data-panel-subdomain]').textContent = tenant.subdomain;
      panel.querySelector('[data-panel-signup]').textContent = 'Signed up ' + formatDate(tenant.created_at);
      panel.querySelector('[data-panel-subscription]').textContent = planBadgeText(tenant);

      var statusBtn = panel.querySelector('[data-panel-action="toggle-status"]');
      statusBtn.textContent = tenant.is_active ? 'Suspend tenant' : 'Reactivate tenant';

      loadStaffSessions(tenant.tenant_id);
      panel.classList.remove('hidden');
      panelScrim.classList.remove('hidden');
      panel.querySelector('[data-panel-close]').focus();
    }

    panel.querySelector('[data-panel-close]').addEventListener('click', closePanel);
    panelScrim.addEventListener('click', closePanel);
    panel.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closePanel();
    });

    panel.querySelector('[data-panel-action="toggle-status"]').addEventListener('click', function () {
      if (!currentTenant) return;
      var nextState = !currentTenant.is_active;
      var verb = nextState ? 'reactivate' : 'suspend';
      if (!window.confirm('Are you sure you want to ' + verb + ' ' + currentTenant.name + '?')) return;

      clearError();
      admin.apiFetch('/admin/tenants/' + currentTenant.tenant_id + '/status', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: nextState }),
      }).then(function () {
        closePanel();
        loadTenants();
        loadMetrics();
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    panel.querySelector('[data-panel-action="sync-gmb"]').addEventListener('click', function () {
      if (!currentTenant) return;
      clearError();
      admin.apiFetch('/admin/tenants/' + currentTenant.tenant_id + '/sync-gmb', { method: 'POST' })
        .catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        });
    });

    var overrideForm = panel.querySelector('[data-override-form]');
    overrideForm.addEventListener('submit', function (event) {
      event.preventDefault();
      if (!currentTenant) return;
      var formData = new FormData(overrideForm);
      var body = { reason: formData.get('reason') };
      var status = formData.get('status');
      var planId = formData.get('plan_id');
      if (status) body.status = status;
      if (planId) body.plan_id = planId;

      clearError();
      admin.apiFetch('/admin/tenants/' + currentTenant.tenant_id + '/subscription', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(function () {
        overrideForm.reset();
        loadTenants();
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    // ---------- staff sessions (per-tenant) ----------

    var staffRowsMount = panel.querySelector('[data-panel-staff-rows]');
    var staffEmpty = panel.querySelector('[data-panel-staff-empty]');

    function loadStaffSessions(tenantId) {
      staffRowsMount.innerHTML = '';
      staffEmpty.classList.add('hidden');
      admin.apiFetch('/admin/sessions?tenant_id=' + tenantId).then(function (data) {
        // Backend list_sessions applies no expires_at filter (that's a
        // reasonable server-side follow-up, out of scope here) — a session
        // that expired weeks ago but was never explicitly revoked would
        // otherwise still show as "active" with a working-looking Force
        // logout button.
        var active = data.sessions.filter(function (s) {
          return !s.revoked && new Date(s.expires_at) > new Date();
        });
        if (!active.length) {
          staffEmpty.classList.remove('hidden');
          return;
        }
        active.forEach(function (session) {
          var row = document.createElement('div');
          row.className = 'qb-admin-staff-row';
          var info = document.createElement('span');
          info.className = 'text-sm text-brand-muted';
          info.textContent = 'User ' + session.user_id.slice(0, 8) + '… · expires ' + formatDate(session.expires_at);
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'qb-program-action';
          btn.textContent = 'Force logout';
          btn.addEventListener('click', function () {
            if (!window.confirm('Force logout this user?')) return;
            clearError();
            admin.apiFetch('/admin/users/' + session.user_id + '/force-logout', { method: 'POST' })
              .then(function () {
                loadStaffSessions(tenantId);
              })
              .catch(function (error) {
                if (error.message !== 'unauthorized') showError(error.message);
              });
          });
          row.appendChild(info);
          row.appendChild(btn);
          staffRowsMount.appendChild(row);
        });
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    // ---------- plan options for the override form ----------

    function loadPlanOptions() {
      admin.apiFetch('/billing/plans').then(function (data) {
        var select = panel.querySelector('[data-panel-plan-select]');
        data.forEach(function (plan) {
          var opt = document.createElement('option');
          opt.value = plan.id;
          opt.textContent = plan.display_name;
          select.appendChild(opt);
        });
      }).catch(function () {
        // Non-fatal: the override form still works without plan_id (status-only change).
      });
    }

    loadMetrics();
    loadTenants();
    loadPlanOptions();
  });
})();
