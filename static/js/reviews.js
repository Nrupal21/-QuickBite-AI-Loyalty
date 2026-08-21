/* QuickBite AI + Loyalty — Reviews page.
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 * GET /api/v1/reviews (Manager+) lists every synced review with its AI-
 * drafted reply (if any) nested. The review itself needs no approval — it
 * posted straight to Google — only the reply does, via the existing
 * PATCH .../approve and .../reject (REVIEW-02), which are the same
 * Manager+ rank as the list itself: anyone who can load this page can act
 * on every row it shows, no extra role check needed here.
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
    var box = document.getElementById('reviews-error');
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
    var chipLabel = String(session.role).replace(/_/g, ' ');
    if (roleLabelEl) roleLabelEl.textContent = chipLabel.charAt(0) + chipLabel.slice(1).toLowerCase();
    if (roleBadgeEl) roleBadgeEl.textContent = chipLabel;
    initialsEls.forEach(function (el) {
      el.textContent = chipLabel.charAt(0);
    });
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

  // ---------- rendering ----------

  function relativeTime(iso) {
    var then = new Date(iso).getTime();
    var hours = Math.max(0, Math.round((Date.now() - then) / 3600000));
    if (hours < 1) return 'just now';
    if (hours < 24) return hours + 'h ago';
    return Math.round(hours / 24) + 'd ago';
  }

  function starsHtml(rating) {
    return '★★★★★'.slice(0, rating) + '☆☆☆☆☆'.slice(0, 5 - rating);
  }

  var STATE_LABEL = { pending: 'Needs review', approved: 'Approved', rejected: 'Regenerating', posted: 'Posted to Google' };

  function responseHtml(review) {
    var response = review.response;
    if (!response) {
      return (
        '<div class="qb-review-response">' +
        '<p class="qb-review-response-label">No reply drafted yet</p>' +
        '<p class="text-xs text-brand-muted">One drafts automatically within the hour.</p>' +
        '</div>'
      );
    }

    var isPending = response.approval_state === 'pending';
    var text = response.final_text || response.ai_draft;
    var actionsHtml = isPending
      ? '<div class="qb-review-response-actions">' +
        '<button type="button" class="qb-review-approve" data-review-approve data-response-id="' + response.id + '">Approve &amp; post</button>' +
        '<button type="button" class="qb-program-action" data-review-reject data-response-id="' + response.id + '">Regenerate</button>' +
        '</div>'
      : '';

    return (
      '<div class="qb-review-response" data-review-response="' + response.id + '">' +
      '<p class="qb-review-response-label">AI-drafted reply <span class="qb-role-badge">' + (STATE_LABEL[response.approval_state] || response.approval_state) + '</span></p>' +
      '<textarea class="qb-review-response-text" data-response-text' + (isPending ? '' : ' readonly') + '>' + text + '</textarea>' +
      actionsHtml +
      '</div>'
    );
  }

  function reviewRowHtml(review) {
    return (
      '<div class="qb-review-row" data-review-row="' + review.id + '">' +
      '<div class="qb-review-header">' +
      '<span class="qb-review-stars" aria-label="' + review.rating + ' out of 5 stars">' + starsHtml(review.rating) + '</span>' +
      '<span class="qb-review-reviewer">' + (review.reviewer_name || 'Anonymous') + '</span>' +
      '<span class="qb-review-meta">· ' + review.branch_name + ' · ' + relativeTime(review.reviewed_at) + '</span>' +
      '</div>' +
      '<p class="qb-review-body">' + (review.review_body || '<span class="text-brand-muted">No written comment.</span>') + '</p>' +
      responseHtml(review) +
      '</div>'
    );
  }

  function renderReviews(reviews) {
    var mount = document.querySelector('[data-review-rows]');
    if (!mount) return;
    if (!reviews.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">rate_review</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No reviews yet</p>' +
        '<p class="text-xs text-brand-muted max-w-[260px]">Reviews sync in from Google once a branch is connected — see the Google Profile page.</p>' +
        '</div>';
      return;
    }
    mount.innerHTML = reviews.map(reviewRowHtml).join('');
  }

  function loadReviews() {
    return apiFetch('/reviews')
      .then(renderReviews)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  loadReviews();

  // ---------- approve / reject ----------

  document.addEventListener('click', function (event) {
    var approveBtn = event.target.closest('[data-review-approve]');
    if (approveBtn) {
      var panel = approveBtn.closest('[data-review-response]');
      var textarea = panel.querySelector('[data-response-text]');
      var original = textarea.defaultValue;
      var edited = textarea.value.trim();
      var payload = edited !== original.trim() ? { final_text: edited } : {};

      approveBtn.disabled = true;
      approveBtn.textContent = 'Approving…';
      apiFetch('/reviews/' + encodeURIComponent(approveBtn.dataset.responseId) + '/approve', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function () {
          showToast('success', 'Approved — posting to Google shortly.');
          loadReviews();
        })
        .catch(function (error) {
          approveBtn.disabled = false;
          approveBtn.textContent = 'Approve & post';
          if (error.message !== 'unauthorized') showError(error.message);
        });
      return;
    }

    var rejectBtn = event.target.closest('[data-review-reject]');
    if (rejectBtn) {
      rejectBtn.disabled = true;
      rejectBtn.textContent = 'Regenerating…';
      apiFetch('/reviews/' + encodeURIComponent(rejectBtn.dataset.responseId) + '/reject', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
        .then(function () {
          showToast('success', 'New draft generated.');
          loadReviews();
        })
        .catch(function (error) {
          rejectBtn.disabled = false;
          rejectBtn.textContent = 'Regenerate';
          if (error.message !== 'unauthorized') showError(error.message);
        });
    }
  });
})();
