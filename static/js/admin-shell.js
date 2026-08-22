/* QuickBite AI + Loyalty — Super Admin shell: session gate, account chip,
 * mobile tab bar wiring, and the shared apiFetch() every admin-*.js page
 * script uses. Modeled directly on dashboard-shell.js (same session key,
 * same account-sheet DOM contract) — the one difference is the redirect
 * target on failure: a non-Super-Admin session bounces to /dashboard
 * instead of /login, since they may well have a valid staff session, just
 * not this one's role.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';

  function readSession() {
    try {
      var raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (storageError) {
      return null;
    }
  }

  var session = readSession();
  if (!session || !session.access_token) {
    window.location.replace('/login');
    return;
  }
  if (session.role !== 'SUPER_ADMIN') {
    window.location.replace('/dashboard');
    return;
  }

  window.QuickBiteAdmin = {
    session: session,
    apiFetch: function (path, options) {
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
    },
  };

  // ---------- account chip (mirrors dashboard-shell.js exactly) ----------

  var initialsEls = document.querySelectorAll('[data-user-initials]');
  var roleLabelEl = document.querySelector('[data-account-sheet-role]');
  var label = 'Super Admin';
  initialsEls.forEach(function (el) { el.textContent = 'SA'; });
  if (roleLabelEl) roleLabelEl.textContent = label;

  var signOutBtn = document.querySelector('[data-account-signout]');
  if (signOutBtn) {
    signOutBtn.addEventListener('click', function () {
      sessionStorage.removeItem(SESSION_KEY);
      window.location.replace('/login');
    });
  }

  // ---------- mobile more/account sheet (same trigger contract as dashboard-shell.js) ----------

  var sheet = document.getElementById('more-sheet');
  var scrim = document.querySelector('[data-account-scrim]');
  var triggers = Array.prototype.slice.call(document.querySelectorAll('[data-account-trigger]'));

  function closeSheet() {
    if (sheet) sheet.hidden = true;
    if (scrim) scrim.hidden = true;
    triggers.forEach(function (t) { t.setAttribute('aria-expanded', 'false'); });
  }

  function openSheet(trigger) {
    if (!sheet) return;
    sheet.hidden = false;
    if (window.innerWidth < 900 && scrim) scrim.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
  }

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function () {
      var isOpen = trigger.getAttribute('aria-expanded') === 'true';
      if (isOpen) { closeSheet(); } else { openSheet(trigger); }
    });
  });
  if (scrim) scrim.addEventListener('click', closeSheet);
})();
