/* QuickBite AI + Loyalty — Super Admin monitors: security flags, per-tenant
 * API usage lookup, active sessions with inline force-logout. Chart is a
 * hand-rolled SVG bar strip — no charting dependency, matching the
 * Sentiment Trend convention in DESIGN.md.
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

    function loadSecurityFlags() {
      admin.apiFetch('/admin/security-flags').then(function (data) {
        var lockedList = document.querySelector('[data-locked-list]');
        lockedList.innerHTML = data.locked_accounts.length
          ? data.locked_accounts.map(function (row) {
              return '<li>' + row.user_id + ' — ' + row.failed_login_count + ' failed attempts</li>';
            }).join('')
          : '<li>No locked accounts.</li>';

        var fraudList = document.querySelector('[data-fraud-list]');
        fraudList.innerHTML = data.fraud_flags.length
          ? data.fraud_flags.map(function (row) {
              return '<li>' + new Date(row.scanned_at).toLocaleDateString() + ' — tenant ' + (row.tenant_id || '—') + '</li>';
            }).join('')
          : '<li>No fraud flags this week.</li>';

        var clusterList = document.querySelector('[data-cluster-list]');
        clusterList.innerHTML = data.force_logout_clusters.length
          ? data.force_logout_clusters.map(function (row) {
              return '<li>Tenant ' + (row.tenant_id || '—') + ' — ' + row.count + ' force-logouts</li>';
            }).join('')
          : '<li>No clusters this week.</li>';
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    function renderUsageChart(days) {
      var mount = document.getElementById('usage-chart');
      var max = Math.max.apply(null, days.map(function (d) { return d.request_count; }).concat([1]));
      var barWidth = 100 / days.length;
      var bars = days.map(function (d, i) {
        var heightPct = (d.request_count / max) * 100;
        return '<rect x="' + (i * barWidth) + '%" y="' + (100 - heightPct) + '%" width="' + (barWidth - 2) + '%" height="' + heightPct + '%" fill="#1A56DB" rx="2"></rect>';
      }).join('');
      mount.innerHTML = '<svg viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:160px;">' + bars + '</svg>';
    }

    var usageForm = document.querySelector('[data-usage-form]');
    usageForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var tenantId = new FormData(usageForm).get('tenant_id');
      admin.apiFetch('/admin/api-usage?tenant_id=' + encodeURIComponent(tenantId)).then(function (data) {
        renderUsageChart(data.days);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    var sessionRows = document.querySelector('[data-session-rows]');

    function buildCell(className, text) {
      var td = document.createElement('td');
      td.className = className;
      td.textContent = text;
      return td;
    }

    function renderSessions(sessions) {
      sessionRows.innerHTML = '';
      sessions.forEach(function (s) {
        var tr = document.createElement('tr');
        tr.className = 'border-b border-outline-variant/10';

        tr.appendChild(buildCell('px-5 py-3 font-mono text-xs', s.user_id));
        tr.appendChild(buildCell('px-5 py-3 font-mono text-xs', s.tenant_id || '—'));
        tr.appendChild(buildCell('px-5 py-3 text-brand-muted text-xs', s.user_agent));
        tr.appendChild(
          buildCell('px-5 py-3 text-brand-muted text-xs', new Date(s.expires_at).toLocaleString())
        );

        var actionCell = document.createElement('td');
        actionCell.className = 'px-5 py-3';
        if (s.revoked) {
          var revokedSpan = document.createElement('span');
          revokedSpan.className = 'text-xs text-brand-muted';
          revokedSpan.textContent = 'Revoked';
          actionCell.appendChild(revokedSpan);
        } else {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'qb-program-action';
          btn.dataset.forceLogout = s.user_id;
          btn.textContent = 'Force logout';
          actionCell.appendChild(btn);
        }
        tr.appendChild(actionCell);

        sessionRows.appendChild(tr);
      });
    }

    function loadSessions(userId) {
      var qs = userId ? '?user_id=' + encodeURIComponent(userId) : '';
      admin.apiFetch('/admin/sessions' + qs).then(function (data) {
        renderSessions(data.sessions);
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    }

    document.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-force-logout]');
      if (!btn) return;
      var userId = btn.dataset.forceLogout;
      if (!window.confirm('Force logout this user from every device?')) return;
      admin.apiFetch('/admin/users/' + userId + '/force-logout', { method: 'POST' }).then(function () {
        loadSessions(document.querySelector('[data-session-user-filter]').value.trim());
      }).catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
    });

    var userFilter = document.querySelector('[data-session-user-filter]');
    var debounceHandle;
    userFilter.addEventListener('input', function () {
      window.clearTimeout(debounceHandle);
      debounceHandle = window.setTimeout(function () {
        loadSessions(userFilter.value.trim());
      }, 300);
    });

    loadSecurityFlags();
    loadSessions('');
  });
})();
