/* QuickBite AI + Loyalty — owner dashboard shell (DASH-01, STITCH-07).
 *
 * Auth: reads the sessionStorage session auth-login.js writes on sign-in
 * (`quickbite_staff_session` — see persistStaffSession() there). No session,
 * or a 401 from the API, sends the tab back to /login. Session-scoped by
 * design (auth-login.js's comment: a token pair should not outlive the tab
 * it was issued for), so a closed tab always re-authenticates.
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
    var box = document.getElementById('dashboard-error');
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
          throw new Error(err.message || 'Something went wrong loading the dashboard.');
        }
        return data;
      });
    });
  }

  // ---------- profile chip ----------

  var initialsEl = document.querySelector('[data-user-initials]');
  var roleLabelEl = document.querySelector('[data-user-role-label]');
  var roleBadgeEl = document.querySelector('[data-user-role-badge]');
  if (session.role) {
    var label = String(session.role).replace(/_/g, ' ');
    if (roleLabelEl) roleLabelEl.textContent = label.charAt(0) + label.slice(1).toLowerCase();
    if (roleBadgeEl) roleBadgeEl.textContent = label;
    if (initialsEl) initialsEl.textContent = label.charAt(0);
  }

  // ---------- stats ----------

  function renderStats(stats) {
    document.querySelectorAll('[data-stat]').forEach(function (el) {
      var key = el.getAttribute('data-stat');
      var value = stats[key];
      if (key === 'avg_rating') {
        el.textContent = value === null || value === undefined ? '—' : value.toFixed(1);
      } else {
        el.textContent = value === null || value === undefined ? '—' : String(value);
      }
    });

    var badge = document.querySelector('[data-stat-badge="pending_approvals"]');
    if (badge) badge.textContent = stats.pending_approvals + ' Pending';

    renderSentimentChart(stats.sentiment_trend || []);

    // Doc 5 DASH-01: "Staff role: read-only response (no approve buttons)".
    // Nothing in this screen renders an approve/reject control today (no
    // list-pending-reviews endpoint exists yet to populate real rows), but
    // the flag is applied at the body level so any future row template only
    // has to check one class rather than re-deriving the role from the JWT.
    document.body.classList.toggle('can-approve', !!stats.can_approve);
  }

  function renderSentimentChart(points) {
    var el = document.getElementById('sentiment-chart');
    if (!el) return;
    el.innerHTML = '';
    if (!points.length) return;

    // Lightweight CSS bar chart, not a Chart.js dependency — the AC only
    // requires the #sentiment-chart mount point to exist; this fills it with
    // real data using the same bar-chart look the generated screen already
    // uses elsewhere, without adding a charting library for one sparkline.
    var wrap = document.createElement('div');
    wrap.className = 'flex items-end justify-between gap-2 h-full px-2';
    points.forEach(function (point) {
      var value = point.avg_sentiment;
      var heightPct = value === null ? 4 : Math.max(4, Math.round(((value + 1) / 2) * 100));
      var bar = document.createElement('div');
      bar.className = 'flex-1 flex flex-col items-center gap-1';
      bar.innerHTML =
        '<div class="w-full rounded-t-sm ' +
        (value === null ? 'bg-outline-variant/40' : 'bg-brand-primary/70') +
        '" style="height:' +
        heightPct +
        '%"></div>' +
        '<span class="text-[10px] text-brand-muted">' +
        point.date.slice(5) +
        '</span>';
      wrap.appendChild(bar);
    });
    el.appendChild(wrap);
  }

  function loadStats() {
    apiGet('/dashboard/stats').then(renderStats).catch(function (error) {
      if (error.message !== 'unauthorized') showError(error.message);
    });
  }

  loadStats();

  // ---------- live updates ----------

  var badge = document.getElementById('new-events-badge');
  var wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  var ws;
  try {
    ws = new WebSocket(
      wsProtocol + '//' + window.location.host + API_BASE + '/dashboard/stream?token=' +
        encodeURIComponent(session.access_token)
    );
  } catch (e) {
    ws = null;
  }

  if (ws) {
    ws.addEventListener('message', function () {
      if (badge) badge.classList.remove('hidden');
      loadStats();
    });
    // No reconnect loop: a dropped socket just means the live-nudge stops —
    // GET /dashboard/stats on the next page load/manual refresh is still
    // correct, per broadcast.py's "always a best-effort nudge" contract.
    ws.addEventListener('error', function () {
      if (ws) ws.close();
    });
  }
})();
