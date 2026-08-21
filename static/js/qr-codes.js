/* QuickBite AI + Loyalty — QR Codes page.
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 * The QR image itself is authenticated (GET /branches/{id}/qr-code.png), so
 * it can't sit in a plain <img src="..."> — every image and download is
 * fetched with the Bearer token and turned into a blob: object URL instead.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';
  // require_role(RoleLevel.OWNER) gates regenerate — Manager can view/
  // download (GET is Manager+) but never rotates a token.
  var REGENERATE_ROLES = { SUPER_ADMIN: true, OWNER: true };

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
    var box = document.getElementById('qr-error');
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
      return response;
    });
  }

  function apiFetchJson(path, options) {
    return apiFetch(path, options).then(function (response) {
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
    var chipLabel = String(session.role).replace(/_/g, ' ');
    if (roleLabelEl) roleLabelEl.textContent = chipLabel.charAt(0) + chipLabel.slice(1).toLowerCase();
    if (roleBadgeEl) roleBadgeEl.textContent = chipLabel;
    initialsEls.forEach(function (el) {
      el.textContent = chipLabel.charAt(0);
    });
  }

  var canRegenerate = !!(session.role && REGENERATE_ROLES[session.role]);

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

  // ---------- cards ----------

  function cardHtml(branch) {
    var actionsHtml = canRegenerate
      ? '<button type="button" class="qb-program-action" data-qr-regenerate data-branch-id="' + branch.id + '">Regenerate</button>'
      : '';

    return (
      '<div class="qb-glass qb-qr-card' + (branch.is_active ? '' : ' qb-qr-card--inactive') + '" data-qr-card="' + branch.id + '">' +
      '<p class="qb-program-name text-center">' + branch.name + '</p>' +
      '<div class="qb-qr-image-frame" data-qr-frame>' +
      '<span class="qb-skel" style="width: 4rem; height: 4rem;"></span>' +
      '</div>' +
      '<div class="qb-qr-token">' +
      '<span class="qb-qr-token-value" data-qr-token title="' + branch.qr_code_token + '">' + branch.qr_code_token + '</span>' +
      '<button type="button" class="qb-qr-copy" data-qr-copy data-token="' + branch.qr_code_token + '" aria-label="Copy code">' +
      '<span class="material-symbols-outlined" aria-hidden="true">content_copy</span></button>' +
      '</div>' +
      '<div class="qb-qr-actions">' +
      '<button type="button" class="qb-qr-download" data-qr-download data-branch-id="' + branch.id + '" data-branch-name="' + branch.name + '">' +
      '<span class="material-symbols-outlined" aria-hidden="true">download</span>Download</button>' +
      actionsHtml +
      '</div>' +
      '</div>'
    );
  }

  // blob: object URLs the page created — revoked and re-created on every
  // reload/regenerate so a stale image is never held onto past its token.
  var objectUrls = {};

  function loadQrImage(branchId) {
    var card = document.querySelector('[data-qr-card="' + branchId + '"]');
    if (!card) return;
    var frame = card.querySelector('[data-qr-frame]');
    apiFetch('/branches/' + encodeURIComponent(branchId) + '/qr-code.png')
      .then(function (response) {
        return response.blob();
      })
      .then(function (blob) {
        if (objectUrls[branchId]) URL.revokeObjectURL(objectUrls[branchId]);
        var url = URL.createObjectURL(blob);
        objectUrls[branchId] = url;
        if (frame) frame.innerHTML = '<img src="' + url + '" alt="QR code" />';
      })
      .catch(function () {
        if (frame) frame.innerHTML = '<span class="material-symbols-outlined text-brand-danger" aria-hidden="true">error</span>';
      });
  }

  function renderBranches(branches) {
    var mount = document.querySelector('[data-qr-grid]');
    if (!mount) return;
    if (!branches.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">qr_code_2</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No branches yet</p>' +
        '</div>';
      return;
    }
    mount.innerHTML = branches.map(cardHtml).join('');
    branches.forEach(function (branch) {
      loadQrImage(branch.id);
    });
  }

  function loadBranches() {
    return apiFetchJson('/branches')
      .then(renderBranches)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  loadBranches();

  // ---------- copy / download / regenerate ----------

  document.addEventListener('click', function (event) {
    var copyBtn = event.target.closest('[data-qr-copy]');
    if (copyBtn) {
      var token = copyBtn.dataset.token;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(token).then(function () {
          showToast('success', 'Code copied.');
        });
      }
      return;
    }

    var downloadBtn = event.target.closest('[data-qr-download]');
    if (downloadBtn) {
      var branchId = downloadBtn.dataset.branchId;
      var url = objectUrls[branchId];
      if (!url) return;
      var link = document.createElement('a');
      link.href = url;
      link.download = 'quickbite-qr-' + downloadBtn.dataset.branchName.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.png';
      document.body.appendChild(link);
      link.click();
      link.remove();
      return;
    }

    var regenBtn = event.target.closest('[data-qr-regenerate]');
    if (regenBtn) {
      if (regenBtn.dataset.confirming !== 'true') {
        regenBtn.dataset.confirming = 'true';
        regenBtn.textContent = 'Old code stops working — confirm?';
        window.setTimeout(function () {
          if (regenBtn.dataset.confirming === 'true') {
            regenBtn.dataset.confirming = 'false';
            regenBtn.textContent = 'Regenerate';
          }
        }, 5000);
        return;
      }
      regenBtn.disabled = true;
      regenBtn.textContent = 'Regenerating…';
      var id = regenBtn.dataset.branchId;
      apiFetchJson('/branches/' + encodeURIComponent(id) + '/qr-code/regenerate', { method: 'POST' })
        .then(function (branch) {
          showToast('success', 'New QR code generated — the old one no longer works.');
          var card = document.querySelector('[data-qr-card="' + branch.id + '"]');
          if (card) {
            var tokenEl = card.querySelector('[data-qr-token]');
            if (tokenEl) {
              tokenEl.textContent = branch.qr_code_token;
              tokenEl.title = branch.qr_code_token;
            }
            var copyEl = card.querySelector('[data-qr-copy]');
            if (copyEl) copyEl.dataset.token = branch.qr_code_token;
          }
          loadQrImage(branch.id);
        })
        .catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        })
        .then(function () {
          regenBtn.disabled = false;
          regenBtn.dataset.confirming = 'false';
          regenBtn.textContent = 'Regenerate';
        });
    }
  });
})();
