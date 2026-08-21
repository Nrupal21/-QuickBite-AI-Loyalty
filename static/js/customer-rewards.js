/* QuickBite AI + Loyalty — "My Rewards" customer profile page.
 *
 * Auth: the customer session lives in an HttpOnly cookie set by OTP login
 * (auth-login.js's otp-verify step) — nothing to read or store client-side.
 * `credentials: 'same-origin'` is what makes the browser attach it.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';

  var steps = {};
  document.querySelectorAll('[data-step]').forEach(function (el) {
    steps[el.getAttribute('data-step')] = el;
  });

  function showStep(name) {
    Object.keys(steps).forEach(function (key) {
      steps[key].hidden = key !== name;
    });
  }

  function apiGet(path) {
    return fetch(API_BASE + path, { credentials: 'same-origin' }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!response.ok) {
            var err = (data && data.detail && data.detail.error) || {};
            var error = new Error(err.message || "We couldn't load your rewards just now.");
            error.status = response.status;
            throw error;
          }
          return data;
        });
    });
  }

  function ratingStars(rating) {
    var wrap = document.createElement('span');
    wrap.className = 'visually-hidden';
    wrap.textContent = rating + ' out of 5 stars';
    var stars = document.createElement('span');
    stars.setAttribute('aria-hidden', 'true');
    for (var i = 1; i <= 5; i += 1) {
      var star = document.createElement('span');
      star.className = 'rewards-star' + (i <= rating ? ' is-filled' : '');
      star.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="' +
        (i <= rating ? 'currentColor' : 'none') +
        '" stroke="currentColor" stroke-width="1.5"><path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2Z"/></svg>';
      stars.appendChild(star);
    }
    var container = document.createElement('span');
    container.appendChild(wrap);
    container.appendChild(stars);
    return container;
  }

  function formatDate(iso) {
    try {
      return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch (e) {
      return iso;
    }
  }

  function renderLocations(locations) {
    var list = document.querySelector('[data-locations-list]');
    var empty = document.querySelector('[data-locations-empty]');
    var template = document.getElementById('rewards-location-template');
    if (!list || !template) return;
    list.innerHTML = '';

    if (!locations.length) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    locations.forEach(function (location) {
      var node = template.content.cloneNode(true);
      var icon = node.querySelector('.rewards-location-icon');
      icon.innerHTML =
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>';
      node.querySelector('.rewards-location-name').textContent = location.branch_name;
      var addressEl = node.querySelector('.rewards-location-address');
      if (location.branch_address) {
        addressEl.textContent = location.branch_address;
      } else {
        addressEl.remove();
      }
      node.querySelector('.rewards-location-count-value').textContent = location.stamp_count;
      node.querySelector('.rewards-location-last-visit').textContent =
        'Last visited ' + formatDate(location.last_scanned_at);
      list.appendChild(node);
    });
  }

  function renderReviews(reviews) {
    var list = document.querySelector('[data-reviews-list]');
    var empty = document.querySelector('[data-reviews-empty]');
    var template = document.getElementById('rewards-review-template');
    if (!list || !template) return;
    list.innerHTML = '';

    if (!reviews.length) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    reviews.forEach(function (review) {
      var node = template.content.cloneNode(true);
      node.querySelector('.rewards-review-branch').textContent = review.branch_name;
      node.querySelector('.rewards-review-rating').appendChild(ratingStars(review.rating));
      node.querySelector('.rewards-review-excerpt').textContent = review.draft_excerpt;
      node.querySelector('.rewards-review-date').textContent = formatDate(review.created_at);
      var tagsEl = node.querySelector('.rewards-review-tags');
      (review.tags || []).forEach(function (tag) {
        var chip = document.createElement('span');
        chip.className = 'rewards-tag-chip';
        chip.textContent = tag;
        tagsEl.appendChild(chip);
      });
      list.appendChild(node);
    });
  }

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

  function renderProfile(profile) {
    var avatar = document.querySelector('[data-profile-avatar]');
    if (avatar) {
      var initialSource = profile.name || profile.username || profile.email || '?';
      avatar.textContent = initialSource.charAt(0).toUpperCase();
    }

    var nameEl = document.querySelector('[data-profile-name]');
    if (nameEl) nameEl.textContent = profile.name || profile.username || 'Welcome back';

    var totalStampsEl = document.querySelector('[data-stat-total-stamps]');
    if (totalStampsEl) totalStampsEl.textContent = profile.total_stamps_alltime;

    var currentProgressEl = document.querySelector('[data-stat-current-progress]');
    if (currentProgressEl) currentProgressEl.textContent = profile.current_reward_count;

    var locations = profile.stamps_by_location || [];
    var locationCountEl = document.querySelector('[data-stat-location-count]');
    if (locationCountEl) locationCountEl.textContent = locations.length;

    setField('[data-profile-email-row]', '[data-profile-email]', profile.email);
    setField('[data-profile-username-row]', '[data-profile-username]', profile.username);

    var favorite = locations.reduce(function (best, location) {
      return !best || location.stamp_count > best.stamp_count ? location : best;
    }, null);
    setField('[data-profile-favorite-row]', '[data-profile-favorite]', favorite && favorite.branch_name);

    var lastVisit = locations.reduce(function (latest, location) {
      var seen = new Date(location.last_scanned_at);
      return !latest || seen > latest ? seen : latest;
    }, null);
    setField(
      '[data-profile-last-visit-row]',
      '[data-profile-last-visit]',
      lastVisit && formatDate(lastVisit.toISOString())
    );

    renderLocations(locations);
    renderReviews(profile.recent_review_drafts);

    showStep('profile');
  }

  function load() {
    showStep('loading');
    apiGet('/customers/me')
      .then(renderProfile)
      .catch(function (error) {
        if (error.status === 401 || error.status === 403) {
          showStep('signed-out');
          return;
        }
        var messageEl = document.querySelector('[data-error-message]');
        if (messageEl) messageEl.textContent = error.message;
        showStep('error');
      });
  }

  var retryBtn = document.querySelector('[data-retry-btn]');
  if (retryBtn) {
    retryBtn.addEventListener('click', load);
  }

  load();
})();
