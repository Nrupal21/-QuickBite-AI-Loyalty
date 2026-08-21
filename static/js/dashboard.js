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

  // ---------- stats ----------

  function renderStats(stats) {
    document.querySelectorAll('[data-stat]').forEach(function (el) {
      var key = el.getAttribute('data-stat');
      var value = stats[key];
      el.classList.remove('qb-skel');
      el.removeAttribute('data-stat-loading');
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

  // SVG area + line chart, no charting dependency — matches the project's
  // existing preference (the CSS bar chart this replaces carried the same
  // note) for keeping the #sentiment-chart mount honest with zero added
  // libraries. Smooths through the real points with simple cubic Béziers;
  // a day with no reviews (avg_sentiment: null) still gets an x position,
  // rendered as an open/empty point rather than interpolated over.
  function renderSentimentChart(points) {
    var mount = document.getElementById('sentiment-chart');
    if (!mount) return;
    mount.innerHTML = '';
    if (!points.length) {
      mount.innerHTML =
        '<div class="qb-empty h-full justify-center">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">show_chart</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No reviews in the last 7 days</p>' +
        '<p class="text-xs text-brand-muted max-w-[220px]">The trend line fills in as reviews come through.</p>' +
        '</div>';
      return;
    }

    var width = 640;
    var height = 260;
    var padX = 8;
    var padTop = 16;
    var padBottom = 28;
    var plotH = height - padTop - padBottom;
    var n = points.length;
    var stepX = n > 1 ? (width - padX * 2) / (n - 1) : 0;
    var baselineY = padTop + plotH; // 0% (most negative) of the sentiment scale
    var midY = padTop + plotH / 2; // where a "no reviews" day's marker sits

    function yFor(value) {
      // avg_sentiment is -1..1.
      var pct = (value + 1) / 2;
      return padTop + plotH - pct * plotH;
    }

    var coords = points.map(function (point, i) {
      var hasData = point.avg_sentiment !== null;
      return {
        x: padX + stepX * i,
        y: hasData ? yFor(point.avg_sentiment) : midY,
        hasData: hasData,
        point: point,
      };
    });

    function smoothSegment(coordsList, close) {
      var d = 'M ' + coordsList[0].x.toFixed(1) + ' ' + coordsList[0].y.toFixed(1);
      for (var i = 0; i < coordsList.length - 1; i++) {
        var c0 = coordsList[i];
        var c1 = coordsList[i + 1];
        var midX = (c0.x + c1.x) / 2;
        d += ' C ' + midX.toFixed(1) + ' ' + c0.y.toFixed(1) + ' ' + midX.toFixed(1) + ' ' + c1.y.toFixed(1) + ' ' + c1.x.toFixed(1) + ' ' + c1.y.toFixed(1);
      }
      if (close) {
        var last = coordsList[coordsList.length - 1];
        var first = coordsList[0];
        d += ' L ' + last.x.toFixed(1) + ' ' + baselineY.toFixed(1);
        d += ' L ' + first.x.toFixed(1) + ' ' + baselineY.toFixed(1);
        d += ' Z';
      }
      return d;
    }

    // A day with no reviews breaks the line rather than dragging it down to
    // the scale's floor — that would read as a sentiment crash, not an
    // absence of data. Each contiguous run of real points gets its own
    // line/area segment; gaps stay gaps.
    var linePaths = [];
    var areaPaths = [];
    var run = [];
    coords.forEach(function (c, i) {
      if (c.hasData) run.push(c);
      if (!c.hasData || i === coords.length - 1) {
        if (run.length > 1) {
          linePaths.push(smoothSegment(run, false));
          areaPaths.push(smoothSegment(run, true));
        }
        if (!c.hasData) run = [];
      }
    });

    var gridLines = [0, 0.5, 1]
      .map(function (t) {
        var y = padTop + plotH * t;
        return '<line class="qb-chart-gridline" x1="' + padX + '" x2="' + (width - padX) + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '"></line>';
      })
      .join('');

    var lastDataIndex = -1;
    coords.forEach(function (c, i) {
      if (c.hasData) lastDataIndex = i;
    });

    var pointsSvg = coords
      .map(function (c, i) {
        var cls = !c.hasData ? 'qb-chart-point qb-chart-point--empty' : i === lastDataIndex ? 'qb-chart-point qb-chart-point--last' : 'qb-chart-point';
        var label = c.point.date + ': ' + (!c.hasData ? 'no reviews that day' : (c.point.avg_sentiment >= 0 ? '+' : '') + c.point.avg_sentiment.toFixed(2) + ' sentiment');
        return '<circle class="' + cls + '" cx="' + c.x.toFixed(1) + '" cy="' + c.y.toFixed(1) + '" r="4"><title>' + label + '</title></circle>';
      })
      .join('');

    var labelsSvg = coords
      .map(function (c) {
        return '<text class="qb-chart-axis" x="' + c.x.toFixed(1) + '" y="' + (height - 6) + '" text-anchor="middle">' + c.point.date.slice(5) + '</text>';
      })
      .join('');

    mount.innerHTML =
      '<svg class="qb-chart-svg" viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="none" role="img" aria-label="Average review sentiment over the last 7 days">' +
      '<defs><linearGradient id="qb-area-gradient" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#1a56db" stop-opacity="0.28"></stop>' +
      '<stop offset="100%" stop-color="#1a56db" stop-opacity="0"></stop>' +
      '</linearGradient></defs>' +
      gridLines +
      areaPaths.map(function (d) { return '<path class="qb-chart-area" d="' + d + '"></path>'; }).join('') +
      linePaths.map(function (d) { return '<path class="qb-chart-line" d="' + d + '"></path>'; }).join('') +
      pointsSvg +
      labelsSvg +
      '</svg>';
  }

  function loadStats() {
    apiGet('/dashboard/stats').then(renderStats).catch(function (error) {
      if (error.message !== 'unauthorized') showError(error.message);
    });
  }

  loadStats();

  // ---------- loyalty snapshot (Manager and above) ----------
  // Mirrors GET /loyalty/analytics's own require_role(MANAGER) boundary
  // (app/api/v1/routers/loyalty.py) client-side, so a Staff session never
  // issues a fetch the API would 403 — the section simply never appears for
  // that role, same as it never would server-side.
  var LOYALTY_ROLES = { SUPER_ADMIN: true, OWNER: true, MANAGER: true };

  function renderLoyaltySnapshot(data) {
    var bento = document.querySelector('[data-loyalty-bento]');
    if (!bento) return;
    bento.hidden = false;

    var stampsEl = bento.querySelector('[data-loyalty-metric="total_stamps_month"]');
    var customersEl = bento.querySelector('[data-loyalty-metric="active_loyalty_customers"]');
    if (stampsEl) stampsEl.textContent = String(data.total_stamps_month);
    if (customersEl) customersEl.textContent = String(data.active_loyalty_customers);

    var insightEl = bento.querySelector('[data-loyalty-insight]');
    if (insightEl) {
      if (data.total_stamps_month === 0) {
        insightEl.textContent = 'No loyalty activity recorded yet this month.';
      } else {
        var branchCount = data.branch_comparison.length;
        insightEl.textContent =
          data.active_loyalty_customers +
          ' customer' + (data.active_loyalty_customers === 1 ? '' : 's') +
          ' earned stamps across ' +
          branchCount + ' branch' + (branchCount === 1 ? '' : 'es') +
          ' this month.';
      }
    }

    var gaugeFill = bento.querySelector('[data-gauge-fill]');
    var gaugeValue = bento.querySelector('[data-gauge-value]');
    var pct = Math.max(0, Math.min(1, data.redemption_rate || 0));
    if (gaugeFill) {
      var circumference = 2 * Math.PI * 50;
      gaugeFill.style.strokeDashoffset = String(circumference * (1 - pct));
    }
    if (gaugeValue) gaugeValue.textContent = Math.round(pct * 100) + '%';

    var branchList = bento.querySelector('[data-branch-list]');
    if (branchList) {
      var sorted = data.branch_comparison.slice().sort(function (a, b) {
        return b.scan_count - a.scan_count;
      });
      if (!sorted.length) {
        branchList.innerHTML = '<p class="text-sm text-brand-muted">No branch activity yet.</p>';
      } else {
        var top = sorted.slice(0, 5);
        var max = top[0].scan_count || 1;
        branchList.innerHTML = top
          .map(function (branch) {
            var fraction = Math.max(0.06, branch.scan_count / max);
            return (
              '<div class="qb-branch-row">' +
              '<span class="qb-branch-name" title="' + branch.branch_name + '">' + branch.branch_name + '</span>' +
              '<span class="qb-branch-bar-track"><span class="qb-branch-bar-fill" style="transform:scaleX(' + fraction.toFixed(3) + ')"></span></span>' +
              '<span class="qb-branch-count">' + branch.scan_count + '</span>' +
              '</div>'
            );
          })
          .join('');
      }
    }
  }

  function loadLoyaltySnapshot() {
    if (!session.role || !LOYALTY_ROLES[session.role]) return;
    apiGet('/loyalty/analytics')
      .then(renderLoyaltySnapshot)
      .catch(function () {
        // Best-effort supplementary panel — a failure here (e.g. a stale
        // client-side role check racing a demotion) just leaves the bento
        // hidden rather than surfacing in the primary dashboard-error banner.
      });
  }

  loadLoyaltySnapshot();

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
