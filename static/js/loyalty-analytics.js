/* QuickBite AI + Loyalty — loyalty analytics + fraud log (DASH-02, STITCH-09).
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';

  function readSession() {
    try {
      var raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  var session = readSession();
  if (!session || !session.access_token) {
    window.location.replace('/login');
    return;
  }

  function showError(message) {
    var box = document.getElementById('analytics-error');
    if (!box) return;
    box.textContent = message;
    box.classList.remove('hidden');
  }

  function apiGet(path) {
    return fetch(API_BASE + path, {
      headers: { Authorization: 'Bearer ' + session.access_token },
    }).then(function (response) {
      if (response.status === 401) {
        sessionStorage.removeItem(SESSION_KEY);
        window.location.replace('/login');
        throw new Error('unauthorized');
      }
      return response.json().then(function (data) {
        if (!response.ok) {
          // FastAPI wraps HTTPException's `detail=` under a top-level
          // "detail" key — the body is {"detail": {"error": {...}}}, not
          // {"error": {...}} at the root.
          var err = (data && data.detail && data.detail.error) || {};
          throw new Error(err.message || 'Something went wrong loading analytics.');
        }
        return data;
      });
    });
  }

  // ---------- profile chip (same as dashboard.js) ----------

  // Two [data-user-initials] nodes exist per page now (the sidebar/mobile
  // trigger chip and the account sheet) — both get the same initial.
  var initialsEls = document.querySelectorAll('[data-user-initials]');
  var roleLabelEl = document.querySelector('[data-user-role-label]');
  var roleBadgeEl = document.querySelector('[data-user-role-badge]');
  if (session.role) {
    var label = String(session.role).replace(/_/g, ' ');
    if (roleLabelEl) roleLabelEl.textContent = label.charAt(0) + label.slice(1).toLowerCase();
    if (roleBadgeEl) roleBadgeEl.textContent = label;
    initialsEls.forEach(function (el) {
      el.textContent = label.charAt(0);
    });
  }

  // ---------- rendering ----------

  function renderMetrics(data) {
    var stampsEl = document.querySelector('[data-metric="total_stamps_month"]');
    var customersEl = document.querySelector('[data-metric="active_customers"]');
    var rateEl = document.querySelector('[data-metric="redemption_rate"]');
    if (stampsEl) stampsEl.textContent = data.total_stamps_month;
    if (customersEl) customersEl.textContent = data.active_loyalty_customers;
    if (rateEl) rateEl.textContent = Math.round(data.redemption_rate * 100) + '%';
  }

  function renderHeatmap(heatmap) {
    var el = document.getElementById('scan-heatmap');
    if (!el) return;
    el.innerHTML = '';

    // Aggregated by hour across whatever branches the response covers (all,
    // or one — filtered server-side by ?branch_id already).
    var byHour = new Array(24).fill(0);
    heatmap.forEach(function (point) {
      byHour[point.hour] += point.scan_count;
    });
    var max = Math.max.apply(null, byHour.concat([1]));

    byHour.forEach(function (count) {
      var intensity = count === 0 ? 0 : Math.max(0.15, count / max);
      var cell = document.createElement('div');
      cell.className = 'heat-cell';
      cell.title = count + ' scans';
      cell.style.backgroundColor =
        count === 0 ? 'var(--tw-color-surface-container, #ededf8)' : 'rgba(26, 86, 219, ' + intensity + ')';
      el.appendChild(cell);
    });
  }

  function renderTopCustomers(topCustomers) {
    var body = document.querySelector('[data-top-customers-body]');
    if (!body) return;
    if (!topCustomers.length) {
      body.innerHTML = '<tr><td class="px-6 py-4 text-brand-muted" colspan="2">No customers yet.</td></tr>';
      return;
    }
    body.innerHTML = topCustomers
      .map(function (customer) {
        return (
          '<tr><td class="px-6 py-3 font-mono">' +
          customer.phone_masked +
          '</td><td class="px-6 py-3 text-right font-semibold">' +
          customer.total_stamps +
          '</td></tr>'
        );
      })
      .join('');
  }

  function renderFraudLog(fraudLog) {
    var body = document.querySelector('[data-fraud-log-body]');
    if (!body) return;
    if (!fraudLog.length) {
      body.innerHTML = '<tr><td class="px-6 py-4 text-brand-muted" colspan="3">No fraud alerts.</td></tr>';
      return;
    }
    body.innerHTML = fraudLog
      .map(function (entry) {
        var when = new Date(entry.scanned_at).toLocaleString();
        return (
          '<tr><td class="px-6 py-3">' +
          when +
          '</td><td class="px-6 py-3 text-right">' +
          Math.round(entry.distance_from_branch_m) +
          'm</td><td class="px-6 py-3 text-brand-danger">' +
          entry.reason +
          '</td></tr>'
        );
      })
      .join('');
  }

  var branchFilter = document.getElementById('branch-filter');
  var branchOptionsPopulated = false;

  function populateBranchFilter(branchComparison) {
    if (!branchFilter || branchOptionsPopulated) return;
    branchComparison.forEach(function (point) {
      var option = document.createElement('option');
      option.value = point.branch_id;
      option.textContent = point.branch_name;
      branchFilter.appendChild(option);
    });
    branchOptionsPopulated = true;
  }

  function loadAnalytics() {
    var query = branchFilter && branchFilter.value ? '?branch_id=' + encodeURIComponent(branchFilter.value) : '';
    apiGet('/loyalty/analytics' + query)
      .then(function (data) {
        renderMetrics(data);
        renderHeatmap(data.heatmap);
        renderTopCustomers(data.top_customers);
        renderFraudLog(data.fraud_log);
        populateBranchFilter(data.branch_comparison);
      })
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  if (branchFilter) {
    branchFilter.addEventListener('change', loadAnalytics);
  }

  loadAnalytics();
})();
