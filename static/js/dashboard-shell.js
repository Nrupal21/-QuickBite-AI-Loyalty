/* QuickBite AI + Loyalty — dashboard app-shell chrome, shared by every
 * dashboard/*.html screen (dashboard/index.html, dashboard/loyalty_analytics.html).
 *
 * This is deliberately its own file, not folded into each page's own script
 * (dashboard.js / loyalty-analytics.js): the mobile tab bar and account
 * sheet are byte-for-byte identical chrome across every dashboard screen,
 * unlike the stats-fetching logic those files own, which differs per page.
 *
 * Replaces partials/nav.html + nav.js + scan.js on dashboard screens — the
 * public marketing nav (guest "Sign in / Get started" pill, the customer
 * receipt-scan dock) does not belong on an authenticated staff/owner
 * surface, which already has its own sidebar/tab-bar navigation and its own
 * account identity, read from the same 'quickbite_staff_session' each
 * page's own script already redirects on if missing.
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

  // ---------- more/account sheet ----------
  // One panel, two anchors, two bodies in one DOM node: a popover off the
  // sidebar chip on desktop (account identity + sign out only — the sidebar
  // already lists every page), a bottom sheet off the mobile tab bar's More
  // tab (the same account block, plus every non-primary nav destination,
  // shown via a min-[900px]:hidden block in the markup so desktop never
  // sees it). Position is set entirely by dashboard.css's breakpoint.

  var sheet = document.getElementById('more-sheet');
  var scrim = document.querySelector('[data-account-scrim]');
  var triggers = Array.prototype.slice.call(document.querySelectorAll('[data-account-trigger]'));

  // Above 900px the sheet is a popover anchored to whichever trigger opened
  // it (the sidebar chip — its exact height/padding isn't worth mirroring
  // as a second, drift-prone CSS constant), positioned from the trigger's
  // real bounding box. Below 900px it's a bottom sheet and dashboard.css's
  // media query owns its position entirely — inline styles are cleared.
  function positionFor(trigger) {
    if (!sheet) return;
    var isDesktop = window.matchMedia('(min-width: 900px)').matches;
    if (!isDesktop) {
      sheet.style.left = '';
      sheet.style.bottom = '';
      sheet.style.top = '';
      return;
    }
    var rect = trigger.getBoundingClientRect();
    var gap = 8;
    sheet.style.left = Math.round(rect.left) + 'px';
    sheet.style.bottom = Math.round(window.innerHeight - rect.top + gap) + 'px';
    sheet.style.top = 'auto';
  }

  function openSheet(trigger) {
    if (!sheet) return;
    positionFor(trigger);
    sheet.hidden = false;
    if (scrim) scrim.hidden = false;
    triggers.forEach(function (t) {
      t.setAttribute('aria-expanded', 'true');
    });
    var signoutButton = sheet.querySelector('[data-account-signout]');
    if (signoutButton) signoutButton.focus();
  }

  function closeSheet() {
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    if (scrim) scrim.hidden = true;
    triggers.forEach(function (trigger) {
      trigger.setAttribute('aria-expanded', 'false');
    });
  }

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function () {
      if (sheet && sheet.hidden) openSheet(trigger);
      else closeSheet();
    });
  });

  if (scrim) scrim.addEventListener('click', closeSheet);

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeSheet();
  });

  // Desktop has no scrim (a full-page dim over a small popover is
  // overkill), so an outside click is what closes it there.
  document.addEventListener('click', function (event) {
    if (!sheet || sheet.hidden) return;
    var target = event.target;
    if (sheet.contains(target)) return;
    if (triggers.some(function (trigger) { return trigger.contains(target); })) return;
    closeSheet();
  });

  if (session && session.role) {
    var label = String(session.role).replace(/_/g, ' ');
    var roleEl = document.querySelector('[data-account-sheet-role]');
    if (roleEl) roleEl.textContent = label.charAt(0) + label.slice(1).toLowerCase();
  }

  // ---------- sign out ----------
  var signoutButton = document.querySelector('[data-account-signout]');
  if (signoutButton) {
    signoutButton.addEventListener('click', function () {
      var current = readSession();
      if (!current) {
        window.location.href = '/login';
        return;
      }
      signoutButton.disabled = true;
      fetch(API_BASE + '/auth/logout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + current.access_token,
        },
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      })
        .catch(function () {
          // Best-effort — the local session is cleared either way below.
        })
        .then(function () {
          try {
            sessionStorage.removeItem(SESSION_KEY);
          } catch (storageError) {
            // Private browsing / storage disabled — nothing to clear.
          }
          window.location.href = '/login';
        });
    });
  }
})();
