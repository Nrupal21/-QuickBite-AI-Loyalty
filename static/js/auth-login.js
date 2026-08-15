/* QuickBite AI + Loyalty — unified identify-first login screen.
 *
 * Flow: /auth/identify classifies the identifier as staff (password, with
 * optional MFA challenge/enrollment) or customer (OTP), then each branch
 * drives its own request/verify pair. See app/api/v1/routers/auth.py and
 * app/api/v1/routers/customer_auth.py for the exact response contracts this
 * mirrors.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  var card = document.querySelector('[data-auth-card]');
  var alertBox = document.querySelector('[data-auth-alert]');
  var kicker = document.querySelector('[data-step-kicker]');
  var titleEl = document.querySelector('[data-step-title]');
  var subEl = document.querySelector('[data-step-sub]');
  var backBtn = document.querySelector('[data-back-btn]');
  var tenantField = document.querySelector('[data-field-tenant-id]');
  var identifierField = document.querySelector('[data-field-identifier]');

  var steps = {};
  document.querySelectorAll('[data-step]').forEach(function (el) {
    steps[el.getAttribute('data-step')] = el;
  });

  var STEP_COPY = {
    'find-tenant': {
      kicker: 'Sign in',
      title: 'Find your restaurant',
      sub: "Enter the QuickBite link your restaurant gave you to continue.",
      back: false,
    },
    identify: {
      kicker: 'Sign in',
      title: 'Log in to QuickBite',
      sub: 'Enter your email, phone, or username to continue.',
      back: false,
    },
    'staff-password': {
      kicker: 'Owner / Staff',
      title: 'Enter your password',
      sub: '',
      back: true,
    },
    'mfa-verify': {
      kicker: 'Two-factor authentication',
      title: 'Verify your identity',
      sub: '',
      back: false,
    },
    'mfa-enroll': {
      kicker: 'Two-factor authentication',
      title: 'Set up your authenticator',
      sub: '',
      back: false,
    },
    'otp-verify': {
      kicker: 'Customer login',
      title: 'Enter your code',
      sub: '',
      back: true,
    },
    'new-customer': {
      kicker: 'Customer login',
      title: 'No loyalty profile yet',
      sub: '',
      back: false,
    },
    'not-found': {
      kicker: 'Sign in',
      title: "We couldn't find that account",
      sub: '',
      back: false,
    },
    success: {
      kicker: 'Signed in',
      title: 'Welcome back',
      sub: '',
      back: false,
    },
  };

  var state = {
    tenantId: (tenantField && tenantField.value.trim()) || '',
    identifier: '',
    password: '',
    accountType: null,
    mfaSessionToken: null,
    resendCooldownUntil: 0,
  };

  // ---------- step transitions ----------

  function staggerReveal(container) {
    // Mirrors the reference component's framer-motion staggerChildren: each
    // direct child fades and slides up in sequence rather than all at once.
    // Guarded on window.anime so a blocked/slow CDN degrades to the
    // Animate.css fadeIn already applied to the step container, not breakage.
    if (!window.anime || !container) return;
    var children = container.children;
    if (!children.length) return;
    anime.set(children, { opacity: 0, translateY: 16 });
    anime({
      targets: children,
      opacity: [0, 1],
      translateY: [16, 0],
      duration: 320,
      delay: anime.stagger(60),
      easing: 'easeOutQuad',
    });
  }

  function showStep(name) {
    Object.keys(steps).forEach(function (key) {
      steps[key].hidden = key !== name;
    });
    var copy = STEP_COPY[name] || {};
    if (kicker) kicker.textContent = copy.kicker || '';
    if (titleEl) titleEl.textContent = copy.title || '';
    if (subEl) subEl.textContent = copy.sub || '';
    if (backBtn) backBtn.hidden = !copy.back;
    hideAlert();

    var target = steps[name];
    if (target) {
      target.classList.remove('animate__animated', 'animate__fadeIn');
      // force reflow so the animation replays on repeated visits to a step
      void target.offsetWidth; // eslint-disable-line no-unused-expressions
      target.classList.add('animate__animated', 'animate__fadeIn', 'animate__faster');
      var firstField = target.querySelector('input:not([type="hidden"])');
      if (firstField) firstField.focus();
      staggerReveal(target);
    }
  }

  function resetToIdentify() {
    state.identifier = '';
    state.password = '';
    state.accountType = null;
    state.mfaSessionToken = null;
    if (identifierField) identifierField.value = '';
    showStep('identify');
  }

  if (backBtn) {
    backBtn.addEventListener('click', resetToIdentify);
  }

  // ---------- alert + shake ----------

  function showAlert(message) {
    if (!alertBox) return;
    alertBox.textContent = message;
    alertBox.hidden = false;
    alertBox.classList.remove('animate__animated', 'animate__shakeX');
    void alertBox.offsetWidth; // eslint-disable-line no-unused-expressions
    alertBox.classList.add('animate__animated', 'animate__shakeX');
  }

  function hideAlert() {
    if (!alertBox) return;
    alertBox.hidden = true;
    alertBox.textContent = '';
  }

  // ---------- API helper ----------

  function apiPost(path, body) {
    return fetch(API_BASE + path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (response) {
      return response.json().catch(function () {
        return {};
      }).then(function (data) {
        if (!response.ok) {
          // FastAPI wraps HTTPException's `detail=` under a top-level
          // "detail" key — the body is {"detail": {"error": {...}}}, not
          // {"error": {...}} at the root.
          var err = (data && data.detail && data.detail.error) || {};
          var message = err.message || 'Something went wrong. Please try again.';
          if (response.status === 429) {
            var retryAfter = response.headers.get('Retry-After') || err.retry_after_seconds;
            if (retryAfter) {
              message += ' Try again in ' + retryAfter + 's.';
            }
          }
          var error = new Error(message);
          error.status = response.status;
          error.code = err.code;
          throw error;
        }
        return data;
      });
    });
  }

  function withBusy(button, fn) {
    if (!button) return fn();
    var label = button.querySelector('[data-btn-label]');
    var originalText = label ? label.textContent : null;
    button.disabled = true;
    if (label) label.textContent = 'Please wait…';
    return fn().finally(function () {
      button.disabled = false;
      if (label && originalText !== null) label.textContent = originalText;
    });
  }

  // ---------- OTP digit boxes ----------

  function buildOtpBoxes(container) {
    if (!container || container.childElementCount > 0) return;
    for (var i = 0; i < 6; i += 1) {
      var input = document.createElement('input');
      input.type = 'text';
      input.inputMode = 'numeric';
      input.autocomplete = i === 0 ? 'one-time-code' : 'off';
      input.maxLength = 1;
      input.className = 'otp-box';
      input.setAttribute('aria-label', 'Digit ' + (i + 1));
      container.appendChild(input);
    }
    container.addEventListener('input', function (event) {
      var target = event.target;
      if (!target.classList.contains('otp-box')) return;
      target.value = target.value.replace(/\D/g, '').slice(-1);
      target.classList.toggle('is-filled', target.value.length > 0);
      if (target.value && target.nextElementSibling) {
        target.nextElementSibling.focus();
      }
    });
    container.addEventListener('keydown', function (event) {
      var target = event.target;
      if (!target.classList.contains('otp-box')) return;
      if (event.key === 'Backspace' && !target.value && target.previousElementSibling) {
        target.previousElementSibling.focus();
      }
    });
    container.addEventListener('paste', function (event) {
      var text = (event.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '');
      if (!text) return;
      event.preventDefault();
      var boxes = Array.prototype.slice.call(container.querySelectorAll('.otp-box'));
      boxes.forEach(function (box, index) {
        box.value = text[index] || '';
        box.classList.toggle('is-filled', box.value.length > 0);
      });
      var lastIndex = Math.min(text.length, boxes.length) - 1;
      if (lastIndex >= 0) boxes[lastIndex].focus();
    });
  }

  function otpValue(container) {
    if (!container) return '';
    return Array.prototype.map
      .call(container.querySelectorAll('.otp-box'), function (box) {
        return box.value;
      })
      .join('');
  }

  function clearOtp(container) {
    if (!container) return;
    container.querySelectorAll('.otp-box').forEach(function (box) {
      box.value = '';
      box.classList.remove('is-filled');
    });
    var first = container.querySelector('.otp-box');
    if (first) first.focus();
  }

  document.querySelectorAll('[data-otp-group]').forEach(buildOtpBoxes);

  // ---------- password show/hide toggle ----------

  document.querySelectorAll('[data-toggle-password]').forEach(function (btn) {
    var iconShow = btn.querySelector('[data-icon-show]');
    var iconHide = btn.querySelector('[data-icon-hide]');
    btn.addEventListener('click', function () {
      var input = btn.parentElement ? btn.parentElement.querySelector('input') : null;
      if (!input) return;
      var toText = input.type === 'password';
      input.type = toText ? 'text' : 'password';
      btn.setAttribute('aria-label', toText ? 'Hide password' : 'Show password');
      btn.setAttribute('aria-pressed', toText ? 'true' : 'false');
      if (iconShow) iconShow.hidden = toText;
      if (iconHide) iconHide.hidden = !toText;
    });
  });

  // ---------- success / customer new-user copy ----------

  function goToSuccess(message, continueHref) {
    var messageEl = document.querySelector('[data-success-message]');
    var continueLink = document.querySelector('[data-continue-link]');
    if (messageEl) messageEl.textContent = message;
    if (continueLink) continueLink.href = continueHref || '/';
    showStep('success');
  }

  // ---------- step: find restaurant (pre-TENANT-01: no subdomain routing
  // yet, so a bare /login has to ask which restaurant before /auth/identify
  // has a tenant_id to check against) ----------

  function resolveTenantBySubdomain(subdomain) {
    return fetch(API_BASE + '/auth/tenant?subdomain=' + encodeURIComponent(subdomain), {
      credentials: 'same-origin',
    })
      .then(function (response) {
        return response.json().catch(function () {
          return {};
        });
      })
      .then(function (data) {
        if (!data.found) {
          showAlert("We couldn't find a restaurant with that link. Check with your restaurant and try again.");
          showStep('find-tenant');
          return;
        }
        state.tenantId = data.tenant_id;
        if (tenantField) tenantField.value = data.tenant_id;
        showStep('identify');
      })
      .catch(function () {
        showAlert('Something went wrong. Please try again.');
        showStep('find-tenant');
      });
  }

  var tenantForm = document.querySelector('[data-tenant-form]');
  if (tenantForm) {
    tenantForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var subdomain = tenantForm.querySelector('[data-field-subdomain]').value.trim();
      if (!subdomain) {
        showAlert("Enter your restaurant's QuickBite link.");
        return;
      }
      withBusy(tenantForm.querySelector('[data-submit-btn]'), function () {
        return resolveTenantBySubdomain(subdomain);
      });
    });
  }

  // ---------- step: identify ----------

  var identifyForm = document.querySelector('[data-identify-form]');
  if (identifyForm) {
    identifyForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var identifier = identifierField.value.trim();
      if (!identifier) {
        showAlert('Enter your email, phone, or username.');
        return;
      }
      if (!UUID_RE.test(state.tenantId)) {
        showAlert(
          "This login link is missing your restaurant's tenant ID. Use the link your restaurant gave you."
        );
        return;
      }
      withBusy(identifyForm.querySelector('[data-submit-btn]'), function () {
        return apiPost('/auth/identify', { identifier: identifier, tenant_id: state.tenantId })
          .then(function (data) {
            state.identifier = identifier;
            state.accountType = data.account_type;
            if (!data.found) {
              showStep('not-found');
              return;
            }
            if (data.account_type === 'staff') {
              var chip = document.querySelector('[data-identity-chip]');
              if (chip) chip.textContent = identifier;
              showStep('staff-password');
              return;
            }
            return requestCustomerOtp();
          })
          .catch(function (error) {
            showAlert(error.message);
          });
      });
    });
  }

  // ---------- step: staff password ----------

  var passwordForm = steps['staff-password'];
  if (passwordForm) {
    passwordForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var password = passwordForm.querySelector('[data-field-password]').value;
      if (!password) {
        showAlert('Enter your password.');
        return;
      }
      withBusy(passwordForm.querySelector('[data-submit-btn]'), function () {
        return apiPost('/auth/login', { identifier: state.identifier, password: password })
          .then(handleAuthOutcome)
          .catch(function (error) {
            showAlert(error.message);
          });
      });
    });
  }

  function handleAuthOutcome(data) {
    if (data.status === 'mfa_required') {
      state.mfaSessionToken = data.mfa_session_token;
      var group = document.querySelector('[data-otp-group="mfa-verify"]');
      clearOtp(group);
      showStep('mfa-verify');
      return;
    }
    if (data.status === 'mfa_enrollment_required') {
      state.mfaSessionToken = data.mfa_session_token;
      return startMfaEnrollment(data.role);
    }
    // TokenResponse — plain owner/staff sign-in, no MFA involved.
    persistStaffSession(data);
    goToSuccess('Signed in as ' + state.identifier + '.', '/dashboard');
  }

  function persistStaffSession(tokenResponse) {
    // Session-scoped on purpose: an access/refresh pair should not outlive
    // the browser tab it was issued for.
    try {
      sessionStorage.setItem(
        'quickbite_staff_session',
        JSON.stringify({
          access_token: tokenResponse.access_token,
          refresh_token: tokenResponse.refresh_token,
          expires_in: tokenResponse.expires_in,
          role: tokenResponse.role,
          tenant_id: tokenResponse.tenant_id,
        })
      );
    } catch (storageError) {
      // Private browsing / storage disabled — session still works for this
      // page load, it just won't survive a refresh.
    }
  }

  // ---------- step: mfa verify (already enrolled) ----------

  var mfaVerifyForm = steps['mfa-verify'];
  if (mfaVerifyForm) {
    mfaVerifyForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var group = document.querySelector('[data-otp-group="mfa-verify"]');
      var code = otpValue(group);
      if (code.length !== 6) {
        showAlert('Enter the 6-digit code.');
        return;
      }
      withBusy(mfaVerifyForm.querySelector('[data-submit-btn]'), function () {
        return apiPost('/auth/mfa/verify', {
          mfa_session_token: state.mfaSessionToken,
          totp_code: code,
        })
          .then(function (data) {
            persistStaffSession(data);
            goToSuccess('Signed in as ' + state.identifier + '.', '/dashboard');
          })
          .catch(function (error) {
            clearOtp(group);
            showAlert(error.message);
          });
      });
    });
  }

  // ---------- step: mfa enrollment ----------

  function startMfaEnrollment(role) {
    var roleNote = document.querySelector('[data-mfa-role-note]');
    if (roleNote) {
      roleNote.textContent = role
        ? 'Your ' + role + ' role requires two-factor authentication.'
        : 'Your role requires two-factor authentication.';
    }
    return apiPost('/auth/mfa/enroll', { mfa_session_token: state.mfaSessionToken })
      .then(function (data) {
        var secretBox = document.querySelector('[data-mfa-secret]');
        if (secretBox) secretBox.textContent = data.secret;
        var group = document.querySelector('[data-otp-group="mfa-enroll"]');
        clearOtp(group);
        showStep('mfa-enroll');
      })
      .catch(function (error) {
        showAlert(error.message);
      });
  }

  var mfaEnrollForm = steps['mfa-enroll'];
  if (mfaEnrollForm) {
    mfaEnrollForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var group = document.querySelector('[data-otp-group="mfa-enroll"]');
      var code = otpValue(group);
      if (code.length !== 6) {
        showAlert('Enter the 6-digit code from your authenticator app.');
        return;
      }
      withBusy(mfaEnrollForm.querySelector('[data-submit-btn]'), function () {
        return apiPost('/auth/mfa/confirm', {
          mfa_session_token: state.mfaSessionToken,
          totp_code: code,
        })
          .then(function (data) {
            persistStaffSession(data);
            goToSuccess('Two-factor authentication is on. Signed in as ' + state.identifier + '.', '/dashboard');
          })
          .catch(function (error) {
            clearOtp(group);
            showAlert(error.message);
          });
      });
    });
  }

  var copySecretBtn = document.querySelector('[data-copy-secret]');
  if (copySecretBtn) {
    copySecretBtn.addEventListener('click', function () {
      var secretBox = document.querySelector('[data-mfa-secret]');
      var text = secretBox ? secretBox.textContent : '';
      if (!text || !navigator.clipboard) return;
      navigator.clipboard.writeText(text).then(function () {
        var original = copySecretBtn.textContent;
        copySecretBtn.textContent = 'Copied';
        setTimeout(function () {
          copySecretBtn.textContent = original;
        }, 1500);
      });
    });
  }

  // ---------- step: customer OTP ----------

  function requestCustomerOtp() {
    return apiPost('/auth/customer/otp-request', {
      identifier: state.identifier,
      tenant_id: state.tenantId,
    }).then(function (data) {
      if (data.status === 'new_user') {
        showStep('new-customer');
        return;
      }
      var noteEl = document.querySelector('[data-otp-sent-note]');
      if (noteEl) {
        noteEl.textContent = 'We sent a 6-digit code to ' + maskIdentifier(state.identifier) + '.';
      }
      var group = document.querySelector('[data-otp-group="otp-verify"]');
      clearOtp(group);
      showStep('otp-verify');
      startResendCooldown();
    });
  }

  function maskIdentifier(identifier) {
    if (identifier.indexOf('@') !== -1) {
      var parts = identifier.split('@');
      var name = parts[0];
      var visible = name.slice(0, Math.min(2, name.length));
      return visible + '***@' + parts[1];
    }
    if (identifier.length > 4) {
      return identifier.slice(0, -4).replace(/./g, '*') + identifier.slice(-4);
    }
    return identifier;
  }

  var otpVerifyForm = steps['otp-verify'];
  if (otpVerifyForm) {
    otpVerifyForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var group = document.querySelector('[data-otp-group="otp-verify"]');
      var code = otpValue(group);
      if (code.length !== 6) {
        showAlert('Enter the 6-digit code.');
        return;
      }
      withBusy(otpVerifyForm.querySelector('[data-submit-btn]'), function () {
        return apiPost('/auth/customer/otp-verify', {
          identifier: state.identifier,
          tenant_id: state.tenantId,
          otp_code: code,
        })
          .then(function () {
            // Customer JWT is set as an HttpOnly cookie by the server —
            // nothing to persist client-side.
            goToSuccess("You're in. Your stamp card is ready.", '/');
          })
          .catch(function (error) {
            clearOtp(group);
            showAlert(error.message);
          });
      });
    });
  }

  var resendBtn = document.querySelector('[data-resend-btn]');

  function startResendCooldown() {
    if (!resendBtn) return;
    state.resendCooldownUntil = Date.now() + 30000;
    var original = resendBtn.textContent;
    resendBtn.disabled = true;
    var tick = function () {
      var remaining = Math.ceil((state.resendCooldownUntil - Date.now()) / 1000);
      if (remaining <= 0) {
        resendBtn.disabled = false;
        resendBtn.textContent = original;
        return;
      }
      resendBtn.textContent = 'Resend code (' + remaining + 's)';
      setTimeout(tick, 1000);
    };
    tick();
  }

  if (resendBtn) {
    resendBtn.addEventListener('click', function () {
      if (resendBtn.disabled) return;
      withBusy(null, function () {
        return requestCustomerOtp().catch(function (error) {
          showAlert(error.message);
        });
      });
    });
  }

  // ---------- step: social sign-in (Google / Apple / Microsoft / GitHub / Twitter) ----------
  //
  // Firebase's client SDK abstracts all five providers into one ID token, so
  // there is exactly one backend call (/auth/customer/oauth) regardless of
  // which button fires — see customer_oauth_service for the server side.

  var FIREBASE_CONFIG = window.__FIREBASE_CONFIG__ || null;
  if (FIREBASE_CONFIG && window.firebase) {
    firebase.initializeApp(FIREBASE_CONFIG);
  }

  function firebaseProviderFor(name) {
    switch (name) {
      case 'google':
        return new firebase.auth.GoogleAuthProvider();
      case 'apple':
        return new firebase.auth.OAuthProvider('apple.com');
      case 'microsoft':
        return new firebase.auth.OAuthProvider('microsoft.com');
      case 'github':
        return new firebase.auth.GithubAuthProvider();
      case 'twitter':
        return new firebase.auth.TwitterAuthProvider();
      default:
        return null;
    }
  }

  document.querySelectorAll('[data-oauth-provider]').forEach(function (button) {
    button.addEventListener('click', function () {
      if (!FIREBASE_CONFIG || !window.firebase) {
        showAlert('Social sign-in is not available right now.');
        return;
      }
      if (!UUID_RE.test(state.tenantId)) {
        showAlert(
          "This login link is missing your restaurant's tenant ID. Use the link your restaurant gave you."
        );
        return;
      }
      var provider = firebaseProviderFor(button.getAttribute('data-oauth-provider'));
      if (!provider) return;

      withBusy(button, function () {
        return firebase
          .auth()
          .signInWithPopup(provider)
          .then(function (result) {
            return result.user.getIdToken();
          })
          .then(function (idToken) {
            return apiPost('/auth/customer/oauth', { id_token: idToken, tenant_id: state.tenantId });
          })
          .then(function (data) {
            if (data.status === 'new_user') {
              // No registration-form UI exists yet (same gap the OTP
              // new_user branch hits) — same placeholder step either way.
              showStep('new-customer');
              return;
            }
            goToSuccess("You're in. Your stamp card is ready.", '/');
          })
          .catch(function (error) {
            // Firebase popup errors (e.g. auth/popup-closed-by-user) carry a
            // .message too, so this reads fine whether the failure was ours
            // or Firebase's.
            showAlert(error.message || 'Sign-in failed. Please try again.');
          });
      });
    });
  });

  if (UUID_RE.test(state.tenantId)) {
    showStep('identify');
  } else {
    var urlSubdomain = new URLSearchParams(window.location.search).get('subdomain');
    if (urlSubdomain) {
      showStep('find-tenant');
      resolveTenantBySubdomain(urlSubdomain);
    } else {
      showStep('find-tenant');
    }
  }
})();
