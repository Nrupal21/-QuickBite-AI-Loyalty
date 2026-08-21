/* QuickBite — the machinery every auth page shares.
 *
 * /login, /register and /onboarding are one flow split across three URLs, so
 * the pieces that make the stage work — the API client, the step machine, the
 * top step bar, the OTP boxes, session storage — live here once. Each page's
 * own file then contains only its sequence, which is the part that actually
 * differs.
 *
 * Exposed as `window.QuickBiteAuth`, a plain object rather than a module, to
 * match how the rest of static/js/ ships: no bundler, no import map, and
 * scripts loaded with a bare <script src> in template order.
 */
(function (global) {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';
  var RESEND_COOLDOWN_SECONDS = 60;

  /* ------------------------------------------------------------ session */

  /* sessionStorage, not localStorage: an access token that survives closing
   * the tab is a token still sitting on a shared machine tomorrow morning.
   * The dashboard, profile and navbar all read this same key. */
  var session = {
    read: function () {
      try {
        var raw = sessionStorage.getItem(SESSION_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (err) {
        return null;
      }
    },
    write: function (tokens) {
      try {
        sessionStorage.setItem(
          SESSION_KEY,
          JSON.stringify({
            access_token: tokens.access_token,
            refresh_token: tokens.refresh_token,
            role: tokens.role,
            tenant_id: tokens.tenant_id || null,
            expires_at: Date.now() + (tokens.expires_in || 0) * 1000,
          })
        );
      } catch (err) {
        /* Private mode with storage disabled. The tokens are still in memory
         * for this page, so the current journey finishes; the next page will
         * ask them to sign in again. Better than a hard failure here. */
      }
    },
    clear: function () {
      try {
        sessionStorage.removeItem(SESSION_KEY);
      } catch (err) {
        /* nothing to clear */
      }
    },
  };

  /* ---------------------------------------------------------- API client */

  function request(method, path, body, opts) {
    opts = opts || {};
    var headers = { 'Content-Type': 'application/json' };
    if (opts.auth) {
      var current = session.read();
      if (current && current.access_token) {
        headers.Authorization = 'Bearer ' + current.access_token;
      }
    }

    return fetch(API_BASE + path, {
      method: method,
      credentials: 'same-origin',
      headers: headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (response.ok) return data;

          /* FastAPI wraps HTTPException's `detail=` under a top-level
           * "detail" key, so the body is {"detail": {"error": {...}}} and not
           * {"error": {...}} at the root. Pydantic's own 422s are a list of
           * field errors under the same key, with no "error" object at all. */
          var detail = data && data.detail;
          var err = (detail && detail.error) || {};
          var message = err.message;

          if (!message && Array.isArray(detail) && detail.length) {
            /* A 422 the client should have caught. Show the first field's
             * message rather than a generic apology — "value is not a valid
             * email address" tells them what to fix. */
            message = detail[0].msg || '';
            message = message.replace(/^Value error, /, '');
          }
          if (!message) message = 'Something went wrong. Please try again.';

          if (response.status === 429) {
            var retry = response.headers.get('Retry-After') || err.retry_after_seconds;
            if (retry) message += ' Try again in ' + retry + 's.';
          }

          var error = new Error(message);
          error.status = response.status;
          error.code = err.code;
          error.payload = err;
          throw error;
        });
    });
  }

  var api = {
    post: function (path, body, opts) {
      return request('POST', path, body, opts);
    },
    get: function (path, opts) {
      return request('GET', path, undefined, opts);
    },
  };

  /* ------------------------------------------------------------- alerts */

  function alerts(root) {
    var box = (root || document).querySelector('[data-auth-alert]');
    var text = box && box.querySelector('[data-auth-alert-text]');
    return {
      show: function (message) {
        if (!box) return;
        if (text) text.textContent = message;
        box.hidden = false;
      },
      hide: function () {
        if (!box) return;
        box.hidden = true;
        if (text) text.textContent = '';
      },
    };
  }

  /* -------------------------------------------------------- step machine */

  /* One <section data-step> visible at a time, with the top step bar
   * restated to match. `railFor` maps a step name onto a bar-segment index;
   * a step with no entry leaves the bar where it was, which is what a
   * sub-step like "choose a method" should do. */
  function steps(options) {
    options = options || {};
    var railFor = options.rail || {};
    var sections = {};
    var order = [];

    document.querySelectorAll('[data-step]').forEach(function (el) {
      var name = el.getAttribute('data-step');
      sections[name] = el;
      order.push(name);
    });

    var railItems = Array.prototype.slice.call(
      document.querySelectorAll('[data-rail-step]')
    );
    var captionEl = document.querySelector('[data-stepper-caption]');

    function paintRail(index) {
      railItems.forEach(function (item, i) {
        var state = i < index ? 'done' : i === index ? 'current' : 'upcoming';
        item.setAttribute('data-state', state);
        if (state === 'current') {
          item.setAttribute('aria-current', 'step');
        } else {
          item.removeAttribute('aria-current');
        }
      });

      /* Mirrors the "Step N of total — label" format the macro renders
       * server-side, so a transition never visibly changes the wording, only
       * the numbers. Below 640px this caption is the only place a step's
       * name still shows — the per-segment titles are hidden there. */
      if (captionEl && railItems[index]) {
        var title = railItems[index].querySelector('.auth-stepper-title');
        var label = title ? title.textContent.trim() : '';
        captionEl.textContent =
          'Step ' + (index + 1) + ' of ' + railItems.length + (label ? ' — ' + label : '');
      }
    }

    return {
      show: function (name) {
        order.forEach(function (key) {
          var el = sections[key];
          if (!el) return;
          el.hidden = key !== name;
          el.removeAttribute('data-entering');
        });

        var target = sections[name];
        if (!target) return;

        /* Re-trigger the entrance. Removing the attribute above and setting it
         * on the next frame is what restarts the animation — a class toggled
         * within the same frame never replays. */
        global.requestAnimationFrame(function () {
          target.setAttribute('data-entering', '');
        });

        if (Object.prototype.hasOwnProperty.call(railFor, name)) {
          paintRail(railFor[name]);
        }

        /* Focus the first thing that can be typed in, so a keyboard user is
         * not left at the top of the document after every step. */
        var first = target.querySelector(
          'input:not([type="hidden"]):not([readonly]), button[data-submit]'
        );
        if (first) {
          try {
            first.focus({ preventScroll: true });
          } catch (err) {
            first.focus();
          }
        }
      },
      section: function (name) {
        return sections[name];
      },
      rail: paintRail,
    };
  }

  /* -------------------------------------------------------- busy buttons */

  /* Keeps the button's box while a request is in flight — swapping the label
   * for "Please wait…" resizes it, and the plate visibly jumps under the
   * cursor at the exact moment someone is watching it. */
  function busy(button, work) {
    if (!button) return work();
    button.setAttribute('data-busy', '');
    button.disabled = true;
    return work().finally(function () {
      button.removeAttribute('data-busy');
      button.disabled = false;
    });
  }

  /* --------------------------------------------------------- OTP entries */

  /* Six boxes behaving like one field: typing advances, backspace retreats,
   * a pasted or SMS-autofilled code distributes across all six, and a
   * complete code submits without anyone reaching for the button. */
  function otpEntry(root, onComplete) {
    if (!root) return null;
    var digits = Array.prototype.slice.call(root.querySelectorAll('[data-otp-digit]'));

    function value() {
      return digits
        .map(function (d) {
          return d.value;
        })
        .join('');
    }

    function paint() {
      digits.forEach(function (d) {
        if (d.value) {
          d.setAttribute('data-filled', '');
        } else {
          d.removeAttribute('data-filled');
        }
      });
    }

    function fill(code) {
      var chars = String(code).replace(/\D/g, '').slice(0, digits.length).split('');
      digits.forEach(function (d, i) {
        d.value = chars[i] || '';
      });
      paint();
      var next = Math.min(chars.length, digits.length - 1);
      digits[next].focus();
      if (chars.length === digits.length && onComplete) onComplete(value());
    }

    digits.forEach(function (input, index) {
      input.addEventListener('input', function () {
        /* A phone keyboard or an autofill can deliver the whole code into one
         * box; treat anything longer than a character as a paste. */
        if (input.value.length > 1) {
          fill(input.value);
          return;
        }
        input.value = input.value.replace(/\D/g, '');
        paint();
        if (input.value && index < digits.length - 1) digits[index + 1].focus();
        if (value().length === digits.length && onComplete) onComplete(value());
      });

      input.addEventListener('keydown', function (event) {
        if (event.key === 'Backspace' && !input.value && index > 0) {
          digits[index - 1].focus();
          digits[index - 1].value = '';
          paint();
          event.preventDefault();
        }
        if (event.key === 'ArrowLeft' && index > 0) {
          digits[index - 1].focus();
          event.preventDefault();
        }
        if (event.key === 'ArrowRight' && index < digits.length - 1) {
          digits[index + 1].focus();
          event.preventDefault();
        }
      });

      input.addEventListener('paste', function (event) {
        var text = (event.clipboardData || global.clipboardData).getData('text');
        if (!text) return;
        event.preventDefault();
        fill(text);
      });
    });

    return {
      value: value,
      clear: function () {
        digits.forEach(function (d) {
          d.value = '';
        });
        paint();
        digits[0].focus();
      },
      focus: function () {
        digits[0].focus();
      },
      /* The one place shake is used on this surface: a wrong code is the only
       * error where the field itself is what was wrong. */
      shake: function () {
        root.setAttribute('data-error', '');
        global.setTimeout(function () {
          root.removeAttribute('data-error');
        }, 520);
      },
    };
  }

  /* ------------------------------------------------------ resend cooldown */

  function resendTimer(button, statusEl, onResend) {
    if (!button) return null;
    var remaining = 0;
    var handle = null;

    function tick() {
      remaining -= 1;
      if (remaining <= 0) {
        global.clearInterval(handle);
        handle = null;
        button.disabled = false;
        button.textContent = 'Resend code';
        return;
      }
      button.textContent = 'Resend in ' + remaining + 's';
    }

    function start() {
      remaining = RESEND_COOLDOWN_SECONDS;
      button.disabled = true;
      button.textContent = 'Resend in ' + remaining + 's';
      if (handle) global.clearInterval(handle);
      handle = global.setInterval(tick, 1000);
    }

    button.addEventListener('click', function () {
      if (button.disabled) return;
      onResend(function () {
        start();
        if (statusEl) statusEl.textContent = 'New code sent.';
      });
    });

    return { start: start };
  }

  /* ---------------------------------------------------- password reveal */

  function wirePasswordToggles(root) {
    (root || document).querySelectorAll('[data-toggle-password]').forEach(function (btn) {
      var show = btn.querySelector('[data-icon-show]');
      var hide = btn.querySelector('[data-icon-hide]');
      btn.addEventListener('click', function () {
        var input = btn.parentElement && btn.parentElement.querySelector('input');
        if (!input) return;
        var reveal = input.type === 'password';
        input.type = reveal ? 'text' : 'password';
        btn.setAttribute('aria-label', reveal ? 'Hide password' : 'Show password');
        btn.setAttribute('aria-pressed', reveal ? 'true' : 'false');
        if (show) show.hidden = reveal;
        if (hide) hide.hidden = !reveal;
      });
    });
  }

  /* ------------------------------------------------------ identifier help */

  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var PHONE_RE = /^\+[1-9]\d{7,14}$/;

  /* Mirrors identity_service.classify_identifier so the form can name the
   * problem before a round trip. The server still classifies independently —
   * this is a courtesy, never the authority. */
  function classify(identifier) {
    var value = String(identifier || '').trim();
    if (EMAIL_RE.test(value)) return 'email';
    if (PHONE_RE.test(value)) return 'phone';
    return null;
  }

  function identifierError(identifier) {
    var value = String(identifier || '').trim();
    if (!value) return 'Enter your email address or phone number.';
    if (value.indexOf('@') !== -1 && !EMAIL_RE.test(value)) {
      return "That email address doesn't look right — check for a typo.";
    }
    if (/^[0-9+\s()-]+$/.test(value) && !PHONE_RE.test(value)) {
      return 'Phone numbers need the country code and no spaces, like +919876543210.';
    }
    if (!classify(value)) {
      return 'Enter an email address, or a phone number starting with +.';
    }
    return null;
  }

  /* ----------------------------------------------------------- OAuth ---- */

  /* Firebase's compat SDK is loaded from a <script> in the template only when
   * the deployment has web config; everything here degrades to "unavailable"
   * rather than throwing when it is absent. */
  function oauth() {
    var config = global.QUICKBITE_FIREBASE || null;
    var available = !!(config && global.firebase && global.firebase.initializeApp);

    if (available && !global.firebase.apps.length) {
      global.firebase.initializeApp(config);
    }

    var PROVIDERS = {
      google: 'GoogleAuthProvider',
      apple: 'OAuthProvider',
      github: 'GithubAuthProvider',
    };

    return {
      available: available,
      /* Resolves with a Firebase ID token, or rejects with a message the
       * caller can show verbatim. */
      signIn: function (name) {
        if (!available) {
          return Promise.reject(new Error('Social sign-in is not configured here.'));
        }
        var provider;
        if (name === 'apple') {
          provider = new global.firebase.auth.OAuthProvider('apple.com');
          provider.addScope('email');
          provider.addScope('name');
        } else {
          var ctor = global.firebase.auth[PROVIDERS[name]];
          if (!ctor) return Promise.reject(new Error('Unknown sign-in provider.'));
          provider = new ctor();
        }
        return global.firebase
          .auth()
          .signInWithPopup(provider)
          .then(function (result) {
            return result.user.getIdToken();
          })
          .catch(function (err) {
            if (err && err.code === 'auth/popup-closed-by-user') {
              var cancelled = new Error('Sign-in was cancelled.');
              cancelled.cancelled = true;
              throw cancelled;
            }
            throw new Error(
              (err && err.message) || 'That sign-in could not be completed.'
            );
          });
      },
    };
  }

  function wireOAuthButtons(opts) {
    var provider = oauth();
    var buttons = Array.prototype.slice.call(document.querySelectorAll('[data-oauth]'));
    if (!buttons.length) return provider;

    if (!provider.available) {
      buttons.forEach(function (btn) {
        btn.disabled = true;
      });
      document.querySelectorAll('[data-oauth-unavailable]').forEach(function (el) {
        el.hidden = false;
      });
      return provider;
    }

    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        opts.onStart();
        busy(btn, function () {
          return provider
            .signIn(btn.getAttribute('data-oauth'))
            .then(opts.onToken)
            .catch(function (err) {
              if (!err.cancelled) opts.onError(err);
            });
        });
      });
    });

    return provider;
  }

  /* ------------------------------------------------------ where to land */

  /* One rule, three pages: an owner or staff member lands on the dashboard, a
   * standard user on their profile, and anyone arriving with ?next= goes back
   * where they came from — but only to a path on this origin, never to an
   * absolute URL a link could have supplied. */
  function destinationFor(tokens) {
    var next = new URLSearchParams(global.location.search).get('next');
    if (next && next.charAt(0) === '/' && next.charAt(1) !== '/') return next;
    if (tokens && tokens.tenant_id) return '/dashboard';
    return '/profile';
  }

  global.QuickBiteAuth = {
    API_BASE: API_BASE,
    SESSION_KEY: SESSION_KEY,
    api: api,
    session: session,
    alerts: alerts,
    steps: steps,
    busy: busy,
    otpEntry: otpEntry,
    resendTimer: resendTimer,
    wirePasswordToggles: wirePasswordToggles,
    classify: classify,
    identifierError: identifierError,
    oauth: oauth,
    wireOAuthButtons: wireOAuthButtons,
    destinationFor: destinationFor,
  };
})(window);
