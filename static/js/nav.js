/* QuickBite AI + Loyalty — the shared navigation.
 *
 * Drives three things for partials/nav.html, on every page:
 *
 *   1. AUTH STATE — the bar ships signed-out and is swapped from the
 *      sessionStorage session the auth pages write ('quickbite_staff_session').
 *      Guest is the common case and must never flash the wrong bar, so the
 *      swap only ever goes guest -> signed-in, never the other way.
 *   2. THE ACCOUNT MENU — one menu grammar, two anchors (under the top bar's
 *      avatar, above the dock's profile button). Both are filled from the same
 *      GET /auth/me.
 *   3. THE DOCK'S MAGNIFICATION — icons grow with the pointer's proximity,
 *      macOS-style, as a spring-damped scale per item.
 *
 * A signed-in Customer (loyalty diner) is deliberately not reflected here:
 * Customer and User are separate identities (app/core/principal.py) and the
 * diner's session is an HttpOnly cookie this script cannot read by design.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  // --------------------------------------------------------------- session

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

  /* An expired access token is not a session. Leaving the signed-in bar up
   * would send someone to a dashboard that bounces them straight back to
   * /login — worse than showing "Sign in" here. */
  function activeSession() {
    var session = readSession();
    if (!session || !session.access_token) return null;
    if (session.expires_at && Date.now() >= session.expires_at) {
      clearSession();
      return null;
    }
    return session;
  }

  // ------------------------------------------------------------ auth state

  var guestGroup = document.querySelector('[data-nav-guest]');
  var authedGroup = document.querySelector('[data-nav-authed]');
  var dockSignIn = document.querySelector('[data-dock-signin]');
  var dockProfile = document.querySelector('[data-dock-profile]');
  var dockAdaptive = document.querySelector('[data-dock-adaptive]');

  var ADAPTIVE = {
    dashboard: {
      href: '/dashboard',
      name: 'Dashboard',
      icon:
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>',
    },
    joinus: {
      href: '/onboarding',
      name: 'Join us',
      icon:
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 10v9.5h16V10"/><path d="M3 6.5 4.6 3.5h14.8L21 6.5a2.75 2.75 0 0 1-5 1.6 2.75 2.75 0 0 1-4 0 2.75 2.75 0 0 1-4 0 2.75 2.75 0 0 1-5-1.6Z"/></svg>',
    },
  };

  function setAdaptiveDockItem(which) {
    if (!dockAdaptive) return;
    var spec = ADAPTIVE[which];
    var iconEl = dockAdaptive.querySelector('[data-dock-adaptive-icon]');
    var nameEl = dockAdaptive.querySelector('[data-dock-adaptive-name]');
    var labelEl = dockAdaptive.querySelector('.sitedock-label');
    dockAdaptive.setAttribute('href', spec.href);
    if (iconEl) iconEl.innerHTML = spec.icon;
    if (nameEl) nameEl.textContent = spec.name;
    if (labelEl) labelEl.textContent = spec.name;
  }

  function applySignedIn(session) {
    if (guestGroup) guestGroup.hidden = true;
    if (authedGroup) authedGroup.hidden = false;
    if (dockSignIn) dockSignIn.hidden = true;
    if (dockProfile) dockProfile.hidden = false;

    var hasTenant = !!session.tenant_id;
    var dashboardBtn = document.querySelector('[data-nav-dashboard]');
    var joinUsBtn = document.querySelector('[data-nav-joinus]');
    if (dashboardBtn) dashboardBtn.hidden = !hasTenant;
    if (joinUsBtn) joinUsBtn.hidden = hasTenant;

    all('[data-account-dashboard]').forEach(function (el) {
      el.hidden = !hasTenant;
    });
    all('[data-account-joinus]').forEach(function (el) {
      el.hidden = hasTenant;
    });

    setAdaptiveDockItem(hasTenant ? 'dashboard' : 'joinus');
  }

  /* Name and email are not in the session — only the tokens, role and tenant
   * are — so the menu header comes from one authorised GET. It runs once per
   * page and only when a session exists; a 401 means the session is stale and
   * the bar drops back to its guest state rather than lying about it. */
  function loadAccount(session) {
    fetch(API_BASE + '/auth/me', {
      headers: { Authorization: 'Bearer ' + session.access_token },
    })
      .then(function (response) {
        if (!response.ok) throw new Error('unauthorized');
        return response.json();
      })
      .then(function (me) {
        var display = me.username || me.email || me.phone || 'Your account';
        var initial = display.charAt(0).toUpperCase();

        all('[data-account-avatar]').forEach(function (el) {
          el.textContent = initial;
        });
        all('[data-account-name]').forEach(function (el) {
          el.textContent = display;
        });
        all('[data-account-email]').forEach(function (el) {
          el.textContent = me.email && me.email !== display ? me.email : '';
        });
      })
      .catch(function () {
        clearSession();
        if (authedGroup) authedGroup.hidden = true;
        if (guestGroup) guestGroup.hidden = false;
        if (dockProfile) dockProfile.hidden = true;
        if (dockSignIn) dockSignIn.hidden = false;
        setAdaptiveDockItem('joinus');
        if (dockAdaptive) {
          // Back to the guest slot: pricing, which is where a signed-out
          // visitor on a phone actually has somewhere to go.
          dockAdaptive.setAttribute('href', '/#pricing');
          var name = dockAdaptive.querySelector('[data-dock-adaptive-name]');
          var label = dockAdaptive.querySelector('.sitedock-label');
          if (name) name.textContent = 'Pricing';
          if (label) label.textContent = 'Pricing';
        }
      });
  }

  var session = activeSession();
  if (session) {
    applySignedIn(session);
    loadAccount(session);
  }

  // -------------------------------------------------------- account menu

  var triggers = all('[data-account-trigger]');
  var openMenu = null;
  var openTrigger = null;

  function menuFor(trigger) {
    var id = trigger.getAttribute('aria-controls');
    return id ? document.getElementById(id) : null;
  }

  function closeMenu(restoreFocus) {
    if (!openMenu) return;
    openMenu.hidden = true;
    if (openTrigger) {
      openTrigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) openTrigger.focus();
    }
    openMenu = null;
    openTrigger = null;
  }

  function showMenu(trigger, fromKeyboard) {
    var menu = menuFor(trigger);
    if (!menu) return;
    closeMenu(false);
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    openMenu = menu;
    openTrigger = trigger;

    // Only a keyboard opener wants the first item focused. Doing it on a
    // mouse click would paint a focus ring nobody asked for; arrow keys still
    // reach the items from the trigger either way.
    if (fromKeyboard) {
      var first = menu.querySelector('.sitemenu-item:not([hidden])');
      if (first) first.focus();
    }
  }

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (openTrigger === trigger) {
        closeMenu(true);
      } else {
        // `detail === 0` is a click synthesised by Enter/Space, not a pointer.
        showMenu(trigger, event.detail === 0);
      }
    });
  });

  document.addEventListener('click', function (event) {
    if (!openMenu) return;
    if (openMenu.contains(event.target)) return;
    if (openTrigger && openTrigger.contains(event.target)) return;
    closeMenu(false);
  });

  document.addEventListener('keydown', function (event) {
    if (!openMenu) return;

    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu(true);
      return;
    }

    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;

    var items = all('.sitemenu-item:not([hidden])', openMenu);
    if (!items.length) return;
    event.preventDefault();
    // -1 when focus is still on the trigger, so ArrowDown lands on the first
    // item and ArrowUp on the last.
    var index = items.indexOf(document.activeElement);
    var next = event.key === 'ArrowDown' ? index + 1 : index - 1;
    if (next < 0) next = items.length - 1;
    if (next >= items.length) next = 0;
    items[next].focus();
  });

  // ------------------------------------------------------------- sign out

  all('[data-account-signout]').forEach(function (button) {
    button.addEventListener('click', function () {
      var current = readSession();
      if (!current) {
        window.location.href = '/login';
        return;
      }
      button.disabled = true;
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
          clearSession();
          window.location.href = '/login';
        });
    });
  });

  // ------------------------------------------------------ dock magnification

  /* The macOS dock's behaviour, ported: an item's scale falls off with its
   * distance from the pointer, so the row bows into an arc. Layout is never
   * touched — only `transform: scale()` — so nothing reflows while the
   * pointer moves and the icons keep their tap targets where they were. */
  var DISTANCE = 140; // px of influence either side of the pointer
  var MAX_SCALE = 1.55;
  var STIFFNESS = 0.28; // how fast a scale chases its target, per frame

  var dockPanel = document.querySelector('[data-dock-panel]');
  var dockItems = dockPanel ? all('[data-dock-item]', dockPanel) : [];

  if (dockPanel && dockItems.length && !reduceMotion) {
    var centers = [];
    var current = dockItems.map(function () {
      return 1;
    });
    var targets = dockItems.map(function () {
      return 1;
    });
    var frame = null;

    /* Centres are measured with every item at rest, because a transform does
     * not move an element's layout box but does move the box that
     * getBoundingClientRect() reports — measuring mid-magnification would
     * feed the effect back into itself. */
    function measure() {
      dockItems.forEach(function (item) {
        item.style.setProperty('--dock-scale', '1');
      });
      centers = dockItems.map(function (item) {
        var rect = item.getBoundingClientRect();
        return rect.left + rect.width / 2;
      });
    }

    function step() {
      var settled = true;
      for (var i = 0; i < dockItems.length; i += 1) {
        var delta = targets[i] - current[i];
        if (Math.abs(delta) < 0.002) {
          current[i] = targets[i];
        } else {
          current[i] += delta * STIFFNESS;
          settled = false;
        }
        dockItems[i].style.setProperty('--dock-scale', current[i].toFixed(3));
        // The dock names whatever it is magnifying. Driven from the scale
        // rather than :hover so a finger — which never hovers — gets the
        // label too.
        dockItems[i].classList.toggle('is-magnified', current[i] > 1.22);
      }
      frame = settled ? null : window.requestAnimationFrame(step);
    }

    function run() {
      if (frame === null) frame = window.requestAnimationFrame(step);
    }

    function magnify(pointerX) {
      if (!centers.length) measure();
      for (var i = 0; i < centers.length; i += 1) {
        var distance = Math.abs(pointerX - centers[i]);
        var falloff = Math.max(0, 1 - distance / DISTANCE);
        // Squared falloff: a narrower peak under the pointer and a gentler
        // shoulder, which is what makes the row read as one arc.
        targets[i] = 1 + (MAX_SCALE - 1) * falloff * falloff;
      }
      run();
    }

    function rest() {
      for (var i = 0; i < targets.length; i += 1) targets[i] = 1;
      run();
    }

    measure();

    dockPanel.addEventListener('pointermove', function (event) {
      magnify(event.clientX);
    });
    dockPanel.addEventListener('pointerleave', rest);
    dockPanel.addEventListener('pointercancel', rest);
    // A finger that lifts leaves no pointer behind to follow, so the dock
    // settles rather than freezing mid-arc.
    dockPanel.addEventListener('pointerup', function (event) {
      if (event.pointerType !== 'mouse') rest();
    });

    window.addEventListener(
      'resize',
      function () {
        centers = [];
        rest();
      },
      { passive: true }
    );
  }
})();
