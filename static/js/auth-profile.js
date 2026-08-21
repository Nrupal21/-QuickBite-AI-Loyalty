/* QuickBite AI + Loyalty — /profile ("My profile").
 *
 * One URL, two identities. app/core/principal.py keeps User (owner/staff) and
 * Customer (loyalty diner) strictly separate, with no bridge between them, so
 * this page picks exactly one of them and renders it:
 *
 *   owner / staff  — bearer token in sessionStorage  -> GET /auth/me
 *   diner          — HttpOnly cookie set at OTP login -> GET /api/v1/customers/me
 *   neither        — signed-out card
 *
 * The bearer session is tried first: it is the more privileged of the two and
 * a diner never has one, so there is no case where checking it first shows the
 * wrong surface.
 *
 * Nothing on this page is rendered server-side — the shell carries no account
 * data at all, and every value below arrives from one of those two authorised
 * responses.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';

  var alertBox = document.querySelector('[data-auth-alert]');

  var steps = {};
  document.querySelectorAll('[data-step]').forEach(function (el) {
    steps[el.getAttribute('data-step')] = el;
  });

  function showStep(name) {
    Object.keys(steps).forEach(function (key) {
      steps[key].hidden = key !== name;
    });
    if (steps[name]) settle(steps[name]);
  }

  /* The one authored moment — cards settle up in sequence as the data lands.
     CSS owns the animation and disables it under prefers-reduced-motion; this
     only assigns the per-card delay. */
  function settle(root) {
    var cards = root.querySelectorAll('.profile-card');
    for (var i = 0; i < cards.length; i += 1) {
      cards[i].style.setProperty('--settle-delay', Math.min(i, 8) * 45 + 'ms');
      cards[i].classList.add('profile-settle');
    }
  }

  function showAlert(message) {
    if (!alertBox) return;
    alertBox.textContent = message;
    alertBox.hidden = false;
  }

  function setText(selector, value) {
    var el = document.querySelector(selector);
    if (el) el.textContent = value;
  }

  /* A detail row with nothing to show is removed, not left blank: an empty
     "Username —" row reads as a broken field rather than an absent one. */
  function setField(rowSelector, valueSelector, value) {
    var row = document.querySelector(rowSelector);
    var valueEl = document.querySelector(valueSelector);
    if (!row || !valueEl) return;
    if (value === null || value === undefined || value === '') {
      row.hidden = true;
      return;
    }
    valueEl.textContent = value;
    row.hidden = false;
  }

  function setInitial(el, source) {
    if (!el) return;
    var text = String(source || '').trim();
    el.textContent = text ? text.charAt(0).toUpperCase() : '?';
  }

  function formatDate(iso) {
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
    } catch (parseError) {
      return iso;
    }
  }

  function readSession() {
    try {
      var raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (storageError) {
      return null;
    }
  }

  function clearSession() {
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch (storageError) {
      // Private browsing / storage disabled — nothing to clear.
    }
  }

  // ------------------------------------------------ owner / staff account

  function apiGetOwner(path, accessToken) {
    return fetch(API_BASE + path, {
      headers: { Authorization: 'Bearer ' + accessToken },
    }).then(function (response) {
      if (response.status === 401) {
        clearSession();
        window.location.replace('/login');
        throw new Error('unauthorized');
      }
      return response.json().then(function (data) {
        if (!response.ok) {
          var err = (data && data.detail && data.detail.error) || {};
          throw new Error(err.message || 'Something went wrong loading your account.');
        }
        return data;
      });
    });
  }

  function roleLabel(role) {
    var text = String(role || '').replace(/_/g, ' ');
    return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
  }

  function renderOwnerProfile(me) {
    var identifier = me.username || me.email || me.phone || 'Your account';

    setInitial(document.querySelector('[data-profile-avatar]'), identifier);
    setText('[data-profile-identifier]', identifier);
    setText('[data-profile-role-badge]', roleLabel(me.role));
    setText('[data-profile-email]', me.email || 'Not set');

    var verifiedOk = document.querySelector('[data-profile-verified-ok]');
    var verifiedWarn = document.querySelector('[data-profile-verified-warn]');
    // No email means nothing to verify — a phone-OTP account gets neither flag.
    if (verifiedOk) verifiedOk.hidden = !me.email || !me.email_verified;
    if (verifiedWarn) verifiedWarn.hidden = !me.email || !!me.email_verified;

    setField('[data-profile-username-row]', '[data-profile-username]', me.username);

    var mfaText = 'Off';
    if (me.mfa_enabled) {
      mfaText = 'On';
    } else if (me.mfa_required) {
      mfaText = 'Required, not set up';
    }
    setText('[data-profile-mfa]', mfaText);

    var noRestaurant = document.querySelector('[data-profile-no-restaurant]');
    var hasRestaurant = document.querySelector('[data-profile-has-restaurant]');
    if (hasRestaurant) hasRestaurant.hidden = !me.tenant_id;
    if (noRestaurant) noRestaurant.hidden = !!me.tenant_id;

    showStep('owner');
  }

  var logoutBtn = document.querySelector('[data-logout-btn]');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', function () {
      var session = readSession();
      if (!session) return;
      logoutBtn.disabled = true;
      fetch(API_BASE + '/auth/logout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + session.access_token,
        },
        body: JSON.stringify({ refresh_token: session.refresh_token }),
      })
        .catch(function () {
          // Best-effort — the local session is cleared either way below.
        })
        .then(function () {
          clearSession();
          window.location.href = '/login';
        });
    });
  }

  // ---------------------------------------------------- diner / loyalty

  function apiGetCustomer(path) {
    return fetch(API_BASE + path, { credentials: 'same-origin' }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!response.ok) {
            var error = new Error('customer session unavailable');
            error.status = response.status;
            throw error;
          }
          return data;
        });
    });
  }

  function ratingStars(rating) {
    var fragment = document.createDocumentFragment();

    // The stars are decorative; the rating itself is stated once for a
    // screen reader instead of being read out as five separate glyphs.
    var label = document.createElement('span');
    label.className = 'visually-hidden';
    label.textContent = rating + ' out of 5 stars';
    fragment.appendChild(label);

    for (var i = 1; i <= 5; i += 1) {
      var star = document.createElement('span');
      star.className = 'profile-star' + (i <= rating ? ' is-filled' : '');
      star.setAttribute('aria-hidden', 'true');
      star.innerHTML =
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">' +
        '<path d="m12 2.6 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9l6.1-.9L12 2.6Z"/></svg>';
      fragment.appendChild(star);
    }
    return fragment;
  }

  function renderCustomerLocations(locations) {
    var list = document.querySelector('[data-customer-locations-list]');
    var empty = document.querySelector('[data-customer-locations-empty]');
    var template = document.getElementById('customer-location-template');
    if (!list || !template) return;
    list.innerHTML = '';

    if (!locations.length) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    locations.forEach(function (location) {
      // The pin icon lives in the <template>, so this loop only fills data.
      var node = template.content.cloneNode(true);
      node.querySelector('.profile-location-name').textContent = location.branch_name;

      var addressEl = node.querySelector('.profile-location-address');
      if (location.branch_address) {
        addressEl.textContent = location.branch_address;
      } else {
        addressEl.remove();
      }

      node.querySelector('.profile-location-count-value').textContent = location.stamp_count;
      node.querySelector('.profile-location-last-visit').textContent =
        'Last visited ' + formatDate(location.last_scanned_at);
      list.appendChild(node);
    });
  }

  function renderCustomerReviews(reviews) {
    var list = document.querySelector('[data-customer-reviews-list]');
    var empty = document.querySelector('[data-customer-reviews-empty]');
    var template = document.getElementById('customer-review-template');
    if (!list || !template) return;
    list.innerHTML = '';

    if (!reviews.length) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    reviews.forEach(function (review) {
      var node = template.content.cloneNode(true);
      node.querySelector('.profile-review-branch').textContent = review.branch_name;
      node.querySelector('.profile-review-rating').appendChild(ratingStars(review.rating));
      node.querySelector('.profile-review-excerpt').textContent = review.draft_excerpt;
      node.querySelector('.profile-review-date').textContent = formatDate(review.created_at);

      var tagsEl = node.querySelector('.profile-review-tags');
      (review.tags || []).forEach(function (tag) {
        var chip = document.createElement('span');
        chip.className = 'profile-tag';
        chip.textContent = tag;
        tagsEl.appendChild(chip);
      });

      list.appendChild(node);
    });
  }

  function renderCustomerProfile(profile) {
    var displayName = profile.name || profile.username || profile.email || '';

    setInitial(document.querySelector('[data-customer-avatar]'), displayName);
    setText('[data-customer-name]', displayName || 'Welcome back');

    setText('[data-customer-stat-total-stamps]', profile.total_stamps_alltime);
    setText('[data-customer-stat-current-progress]', profile.current_reward_count);

    var locations = profile.stamps_by_location || [];
    setText('[data-customer-stat-location-count]', locations.length);

    setField('[data-customer-email-row]', '[data-customer-email]', profile.email);
    setField('[data-customer-username-row]', '[data-customer-username]', profile.username);
    setText('[data-customer-member-since]', formatDate(profile.member_since));

    // "Favourite" and "last visit" are derived here rather than asked of the
    // API — both are already implied by the stamp rows the response carries.
    var favorite = locations.reduce(function (best, location) {
      return !best || location.stamp_count > best.stamp_count ? location : best;
    }, null);
    setField('[data-customer-favorite-row]', '[data-customer-favorite]', favorite && favorite.branch_name);

    var lastVisit = locations.reduce(function (latest, location) {
      var seen = new Date(location.last_scanned_at);
      return !latest || seen > latest ? seen : latest;
    }, null);
    setField(
      '[data-customer-last-visit-row]',
      '[data-customer-last-visit]',
      lastVisit && formatDate(lastVisit.toISOString())
    );

    renderCustomerLocations(locations);
    renderCustomerReviews(profile.recent_review_drafts || []);

    showStep('customer');
  }

  // ------------------------------------------------------- entry point

  showStep('loading');

  var session = readSession();
  if (session && session.access_token) {
    apiGetOwner('/auth/me', session.access_token)
      .then(renderOwnerProfile)
      .catch(function (error) {
        // 'unauthorized' already redirected to /login — anything else leaves
        // the owner surface up with the failure stated on it.
        if (error.message !== 'unauthorized') {
          showStep('owner');
          showAlert(error.message);
        }
      });
  } else {
    apiGetCustomer('/customers/me')
      .then(renderCustomerProfile)
      .catch(function () {
        showStep('signed-out');
      });
  }
})();
