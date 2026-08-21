/* QuickBite AI + Loyalty — Google Profile Link page.
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 * Lists every branch (GET /api/v1/branches) with its Google Business
 * Profile connection state, and drives connect (top-level navigation to
 * Google's OAuth consent — GET /api/v1/gmb/connect?branch_id=) / disconnect
 * (POST /api/v1/gmb/disconnect). The OAuth callback lands back on this exact
 * page with ?gmb=connected|failed|expired, which the toast below reads once
 * and then strips from the URL.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';
  // Only these roles can reach GET /gmb/connect and POST /gmb/disconnect
  // server-side (require_role(RoleLevel.OWNER), which admits Super Admin) —
  // Manager can view this page (GET /branches is Manager+) but the action
  // buttons would just 403, so they never render for that role.
  var CONNECT_ROLES = { SUPER_ADMIN: true, OWNER: true };

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
    var box = document.getElementById('gmb-error');
    if (!box) return;
    box.textContent = message;
    box.classList.remove('hidden');
  }

  function apiFetch(path, options) {
    return fetch(API_BASE + path, Object.assign({}, options, {
      headers: Object.assign(
        { Authorization: 'Bearer ' + session.access_token },
        (options && options.headers) || {}
      ),
    })).then(function (response) {
      if (response.status === 401) {
        sessionStorage.removeItem(SESSION_KEY);
        window.location.replace('/login');
        throw new Error('unauthorized');
      }
      return response.json().then(function (data) {
        if (!response.ok) {
          var err = (data && data.detail && data.detail.error) || {};
          throw new Error(err.message || 'Something went wrong.');
        }
        return data;
      });
    });
  }

  // ---------- profile chip (same as dashboard.js) ----------

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

  var canConnect = !!(session.role && CONNECT_ROLES[session.role]);

  // ---------- toast (reads ?gmb= once, then strips it) ----------

  function relativeTime(iso) {
    var then = new Date(iso).getTime();
    var minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (minutes < 1) return 'just now';
    if (minutes < 60) return minutes + 'm ago';
    var hours = Math.round(minutes / 60);
    if (hours < 24) return hours + 'h ago';
    return Math.round(hours / 24) + 'd ago';
  }

  function showToast(kind, message) {
    var toast = document.createElement('div');
    toast.className = 'qb-glass qb-toast qb-toast--' + kind + ' animate__animated animate__fadeInUp';
    toast.innerHTML =
      '<span class="material-symbols-outlined" aria-hidden="true">' +
      (kind === 'success' ? 'check_circle' : 'error') +
      '</span><span>' + message + '</span>';
    document.body.appendChild(toast);
    window.setTimeout(function () {
      toast.classList.remove('animate__fadeInUp');
      toast.classList.add('animate__fadeOutDown');
      window.setTimeout(function () {
        toast.remove();
      }, 400);
    }, 4000);
  }

  (function handleCallbackOutcome() {
    var params = new URLSearchParams(window.location.search);
    var outcome = params.get('gmb');
    if (!outcome) return;
    if (outcome === 'connected') showToast('success', 'Google Business Profile connected.');
    else if (outcome === 'expired') showToast('error', 'That connection link expired — try again.');
    else showToast('error', "Couldn't connect your Google Business Profile.");
    params.delete('gmb');
    var rest = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (rest ? '?' + rest : ''));
  })();

  // ---------- branch list ----------

  function branchRowHtml(branch) {
    var statusHtml = branch.gmb_connected
      ? '<p class="qb-gmb-status qb-gmb-status--connected"><span class="qb-gmb-dot" aria-hidden="true"></span>Connected' +
        (branch.gmb_last_synced_at ? ' · synced ' + relativeTime(branch.gmb_last_synced_at) : '') +
        '</p>'
      : '<p class="qb-gmb-status"><span class="qb-gmb-dot" aria-hidden="true"></span>Not connected</p>';

    var actionHtml = '';
    if (canConnect) {
      actionHtml = branch.gmb_connected
        ? '<button type="button" class="qb-gmb-action qb-gmb-action--disconnect" data-gmb-disconnect data-branch-id="' +
          branch.id + '">Disconnect</button>'
        : '<button type="button" class="qb-gmb-action qb-gmb-action--connect" data-gmb-connect data-branch-id="' +
          branch.id + '">Connect</button>';
    }

    return (
      '<div class="qb-gmb-row" data-branch-row="' + branch.id + '">' +
      '<div><p class="qb-gmb-branch-name">' + branch.name + '</p>' + statusHtml + '</div>' +
      actionHtml +
      '</div>'
    );
  }

  function renderBranches(branches) {
    var mount = document.querySelector('[data-branch-rows]');
    if (!mount) return;
    if (!branches.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">storefront</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No branches yet</p>' +
        '<p class="text-xs text-brand-muted max-w-[260px]">Branches you add will show up here, ready to connect to Google.</p>' +
        '</div>';
      return;
    }
    mount.innerHTML = branches.map(branchRowHtml).join('');
  }

  function loadBranches() {
    return apiFetch('/branches')
      .then(renderBranches)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  loadBranches();

  // ---------- connect / disconnect ----------

  document.addEventListener('click', function (event) {
    var connectBtn = event.target.closest('[data-gmb-connect]');
    if (connectBtn) {
      connectBtn.disabled = true;
      connectBtn.textContent = 'Connecting…';
      apiFetch('/gmb/connect?branch_id=' + encodeURIComponent(connectBtn.dataset.branchId))
        .then(function (data) {
          // Real top-level navigation to Google's consent screen — not an
          // XHR redirect, since the app itself never sees Google's own
          // login/consent UI, only the callback it lands back on.
          window.location.href = data.authorize_url;
        })
        .catch(function (error) {
          connectBtn.disabled = false;
          connectBtn.textContent = 'Connect';
          if (error.message !== 'unauthorized') showError(error.message);
        });
      return;
    }

    var disconnectBtn = event.target.closest('[data-gmb-disconnect]');
    if (disconnectBtn) {
      if (disconnectBtn.dataset.confirming !== 'true') {
        disconnectBtn.dataset.confirming = 'true';
        disconnectBtn.textContent = 'Confirm disconnect?';
        window.setTimeout(function () {
          if (disconnectBtn.dataset.confirming === 'true') {
            disconnectBtn.dataset.confirming = 'false';
            disconnectBtn.textContent = 'Disconnect';
          }
        }, 4000);
        return;
      }
      disconnectBtn.disabled = true;
      disconnectBtn.textContent = 'Disconnecting…';
      apiFetch('/gmb/disconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch_id: disconnectBtn.dataset.branchId }),
      })
        .then(function () {
          showToast('success', 'Google Business Profile disconnected.');
          loadBranches();
        })
        .catch(function (error) {
          disconnectBtn.disabled = false;
          disconnectBtn.dataset.confirming = 'false';
          disconnectBtn.textContent = 'Disconnect';
          if (error.message !== 'unauthorized') showError(error.message);
        });
    }
  });
})();
