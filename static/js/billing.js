/* QuickBite AI + Loyalty — Billing page.
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 * GET /api/v1/billing/subscription and GET /api/v1/billing/plans are both
 * already-shipped endpoints (no new backend for this page). Checkout hands
 * off to Razorpay's own hosted subscription page (`short_url` from
 * POST /billing/checkout) rather than embedding Razorpay's Checkout.js
 * widget — this app has no payment-verification callback endpoint for the
 * embedded flow, and the hosted page is what create_checkout_order already
 * returns a URL for.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';

  var STATUS_LABEL = {
    trialing: 'Trial', active: 'Active', past_due: 'Past due',
    canceled: 'Canceled', paused: 'Paused', none: 'No plan',
  };

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
    var box = document.getElementById('billing-error');
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

  // ---------- current subscription ----------

  function formatDate(iso) {
    return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  var currentPlanName = null;
  var currentSub = null;

  function setSubField(el, text) {
    if (!el) return;
    el.classList.remove('qb-skel');
    el.removeAttribute('data-sub-loading');
    el.textContent = text;
  }

  function renderSubscription(sub) {
    currentPlanName = sub.plan_name;
    currentSub = sub;

    var statusWrap = document.querySelector('[data-sub-status]');
    if (statusWrap) {
      statusWrap.textContent = STATUS_LABEL[sub.status] || sub.status;
      statusWrap.className = 'qb-sub-status qb-sub-status--' + sub.status;
    }

    setSubField(document.querySelector('[data-sub-plan-name]'), sub.plan_name || 'No active plan');

    var metaEl = document.querySelector('[data-sub-meta]');
    if (sub.status === 'trialing' && sub.trial_ends_at) {
      setSubField(metaEl, 'Trial ends ' + formatDate(sub.trial_ends_at));
    } else if (sub.current_period_end) {
      setSubField(metaEl, (sub.cancel_at_period_end ? 'Ends ' : 'Renews ') + formatDate(sub.current_period_end));
    } else {
      setSubField(metaEl, 'Choose a plan below to get started.');
    }

    var cancelNoticeEl = document.querySelector('[data-sub-cancel-notice]');
    if (cancelNoticeEl) cancelNoticeEl.hidden = !sub.cancel_at_period_end;

    renderCancelAction(sub);
  }

  function loadSubscription() {
    return apiFetch('/billing/subscription')
      .then(renderSubscription)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  // ---------- cancel / reactivate ----------
  // Only Owner reaches this page's mutating actions at all (require_role
  // on both routes); no client-side role gate needed here the way
  // google-profile.js/settings.js gate Manager, since this whole page's
  // only mutating actions (checkout, cancel, reactivate) are all
  // Owner-only and Manager/Staff simply get a 403 if they somehow reach
  // this page and click — matching this page's existing convention of
  // not hiding the plan grid from non-Owner roles either.

  function renderCancelAction(sub) {
    var mount = document.querySelector('[data-cancel-action]');
    if (!mount) return;

    var hasActiveOrPastDue = sub.status === 'active' || sub.status === 'past_due';

    if (hasActiveOrPastDue && !sub.cancel_at_period_end) {
      mount.innerHTML = '<button type="button" class="qb-plan-cta" data-cancel-subscription>Cancel subscription</button>';
    } else if (sub.cancel_at_period_end) {
      mount.innerHTML = '<button type="button" class="qb-plan-cta" data-reactivate-subscription>Keep my plan</button>';
    } else {
      mount.innerHTML = '';
    }
  }

  document.addEventListener('click', function (event) {
    var cancelBtn = event.target.closest('[data-cancel-subscription]');
    if (cancelBtn) {
      var endDate = currentSub && currentSub.current_period_end
        ? formatDate(currentSub.current_period_end)
        : 'the end of your current period';
      if (!window.confirm('Cancel your subscription? You\'ll keep access until ' + endDate + '.')) return;

      cancelBtn.disabled = true;
      cancelBtn.textContent = 'Cancelling…';
      apiFetch('/billing/cancel', { method: 'POST' })
        .then(renderSubscription)
        .catch(function (error) {
          cancelBtn.disabled = false;
          cancelBtn.textContent = 'Cancel subscription';
          if (error.message !== 'unauthorized') showError(error.message);
        });
      return;
    }

    var reactivateBtn = event.target.closest('[data-reactivate-subscription]');
    if (reactivateBtn) {
      reactivateBtn.disabled = true;
      reactivateBtn.textContent = 'Restoring…';
      apiFetch('/billing/reactivate', { method: 'POST' })
        .then(renderSubscription)
        .catch(function (error) {
          reactivateBtn.disabled = false;
          reactivateBtn.textContent = 'Keep my plan';
          if (error.message !== 'unauthorized') showError(error.message);
        });
    }
  });

  // ---------- plans ----------

  function formatInr(paise) {
    if (paise === 0) return 'Free';
    return '₹' + Math.round(paise / 100).toLocaleString('en-IN');
  }

  function humanizeKey(key) {
    var words = key.replace(/_/g, ' ').split(' ');
    return words.map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1); }).join(' ');
  }

  function featureLines(limits) {
    return Object.keys(limits)
      .map(function (key) {
        var value = limits[key];
        var text = typeof value === 'boolean' ? humanizeKey(key) : humanizeKey(key) + ': ' + value;
        return '<li class="qb-plan-feature"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>' + text + '</li>';
      })
      .join('');
  }

  function planCardHtml(plan) {
    var isCurrent = plan.display_name === currentPlanName;
    return (
      '<div class="qb-glass qb-plan-card' + (isCurrent ? ' qb-plan-card--current' : '') + '">' +
      (isCurrent ? '<span class="qb-plan-current-badge">Current plan</span>' : '') +
      '<p class="qb-plan-name">' + plan.display_name + '</p>' +
      '<p class="qb-plan-price">' + formatInr(plan.price_monthly_inr) + (plan.price_monthly_inr > 0 ? ' <small>/ month</small>' : '') + '</p>' +
      (plan.trial_days > 0 ? '<p class="qb-plan-trial">' + plan.trial_days + '-day free trial</p>' : '') +
      '<ul class="qb-plan-features">' + featureLines(plan.feature_limits || {}) + '</ul>' +
      (isCurrent
        ? ''
        : '<button type="button" class="qb-plan-cta" data-choose-plan data-plan-id="' + plan.id + '">Choose plan</button>') +
      '</div>'
    );
  }

  function renderPlans(plans) {
    var mount = document.querySelector('[data-plan-grid]');
    if (!mount) return;
    if (!plans.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">credit_card</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No plans available right now</p>' +
        '</div>';
      return;
    }
    mount.innerHTML = plans.map(planCardHtml).join('');
  }

  function loadPlans() {
    return apiFetch('/billing/plans')
      .then(renderPlans)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  // Plans render relative to the current plan's name, so wait for the
  // subscription lookup before drawing the grid.
  loadSubscription().then(loadPlans);

  // ---------- checkout ----------

  document.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-choose-plan]');
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = 'Starting checkout…';
    apiFetch('/billing/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: btn.dataset.planId }),
    })
      .then(function (data) {
        if (data.short_url) {
          window.location.href = data.short_url;
          return;
        }
        showError("Checkout isn't available for this plan yet — please contact support.");
        btn.disabled = false;
        btn.textContent = 'Choose plan';
      })
      .catch(function (error) {
        btn.disabled = false;
        btn.textContent = 'Choose plan';
        if (error.message !== 'unauthorized') showError(error.message);
      });
  });
})();
