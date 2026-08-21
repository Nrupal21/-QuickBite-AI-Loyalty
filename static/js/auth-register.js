/* QuickBite — /register.
 *
 *   identify -> otp -> register-name -> complete
 *   password-signup  -> sent            (verification link, no session yet)
 *   oauth            -> oauth-name      -> complete
 *
 * Registration never creates a restaurant. Every route here produces the same
 * thing: a standard account with role USER and no tenant. Turning that into a
 * business is "Join Us" at /onboarding, reached later from the account menu
 * or dock — not offered on this page, so a fresh sign-up isn't asked to
 * start a second multi-step form before it's had a chance to acknowledge the
 * first one just succeeded.
 *
 * The password route is the odd one out and is meant to be. It cannot sign
 * anyone in, because the address has not been proven yet: it posts to
 * /auth/register, which stashes the signup in Redis and emails a link. The
 * code and social routes both prove the identifier first, so they can create
 * the account and hand back a session immediately.
 */
(function () {
  'use strict';

  var A = window.QuickBiteAuth;
  var alerts = A.alerts();
  var flow = A.steps({
    rail: {
      identify: 0,
      'password-signup': 0,
      otp: 1,
      'register-name': 2,
      'oauth-name': 2,
      sent: 1,
      complete: 3,
    },
  });

  var state = {
    identifier: null,
    identifierType: null,
    registrationToken: null,
    oauthToken: null,
    // Whichever contact the account ends up confirmed on — the identifier
    // for the code route, the provider's verified email for the social
    // route (absent for a provider, Apple among them, that hands back no
    // email at all). Only used to word the `complete` step's message.
    contact: null,
  };

  A.wirePasswordToggles();

  function echo() {
    document.querySelectorAll('[data-echo-identifier]').forEach(function (el) {
      el.textContent = state.identifier || '';
    });
    document.querySelectorAll('[data-echo-identifier-type]').forEach(function (el) {
      el.textContent = state.identifierType === 'phone' ? 'phone number' : 'email';
    });
  }

  function signedIn(tokens) {
    A.session.write(tokens);
    alerts.hide();

    /* state.contact carries the social route's verified email (absent for a
     * provider that hands one back); state.identifier carries the code
     * route's contact end to end. Whichever is set names the confirmation
     * that was just sent. */
    var contact = state.contact || state.identifier;
    var messageEl = document.querySelector('[data-complete-message]');
    if (messageEl) {
      messageEl.textContent = contact
        ? 'Your QuickBite account is ready. We sent a confirmation to ' + contact + '.'
        : 'Your QuickBite account is ready.';
    }
    /* A confirmation step, not an immediate redirect into "Join Us" — a
     * fresh sign-up just finished a form; it has not asked to start another
     * one. */
    flow.show('complete');
  }

  /* -------------------------------------------------- 1. pick a contact */

  var identifyForm = document.querySelector('[data-identify-form]');
  identifyForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    var input = identifyForm.querySelector('[data-field-identifier]');
    var value = input.value.trim();
    var problem = A.identifierError(value);
    if (problem) {
      input.setAttribute('aria-invalid', 'true');
      alerts.show(problem);
      input.focus();
      return;
    }
    input.removeAttribute('aria-invalid');

    state.identifier = value;
    state.identifierType = A.classify(value);
    echo();

    A.busy(identifyForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/otp/request', { identifier: value })
        .then(function () {
          flow.show('otp');
          resend.start();
        })
        .catch(function (err) {
          alerts.show(err.message);
        });
    });
  });

  /* ------------------------------------------------------ 2. the code */

  var otpForm = document.querySelector('[data-otp-form]');
  var otpStatus = otpForm.querySelector('[data-otp-status]');
  var otp = A.otpEntry(otpForm.querySelector('[data-otp-entry]'), function () {
    submitCode();
  });

  var resend = A.resendTimer(
    otpForm.querySelector('[data-resend]'),
    otpStatus,
    function (done) {
      A.api
        .post('/auth/otp/request', { identifier: state.identifier })
        .then(done)
        .catch(function (err) {
          alerts.show(err.message);
        });
    }
  );

  otpForm.addEventListener('submit', function (event) {
    event.preventDefault();
    submitCode();
  });

  function submitCode() {
    var code = otp.value();
    if (code.length !== 6) {
      alerts.show('Enter all six digits.');
      return;
    }
    alerts.hide();

    A.busy(otpForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/otp/verify', { identifier: state.identifier, otp_code: code })
        .then(function (data) {
          if (data.status === 'registration_required') {
            state.registrationToken = data.registration_token;
            state.identifierType = data.identifier_type || state.identifierType;
            echo();
            flow.show('register-name');
            return;
          }
          /* The contact already had an account. The code just proved they own
           * it, so this is a successful sign-in, not an error to apologise
           * for — send them on rather than making them start again at /login. */
          if (data.status === 'mfa_required' || data.status === 'mfa_enrollment_required') {
            window.location.assign('/login');
            return;
          }
          A.session.write(data);
          window.location.assign(A.destinationFor(data));
        })
        .catch(function (err) {
          otp.shake();
          otp.clear();
          alerts.show(err.message);
        });
    });
  }

  /* ------------------------------------------------- 3. name the account */

  var completeForm = document.querySelector('[data-complete-form]');
  completeForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    var name = completeForm.querySelector('[data-field-name]').value.trim();
    var password = completeForm.querySelector('[data-field-password]').value;

    if (!name) {
      alerts.show('Tell us what to call you.');
      return;
    }
    if (password && password.length < 8) {
      alerts.show('A password needs at least 8 characters — or leave it blank.');
      return;
    }

    A.busy(completeForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/otp/register', {
          registration_token: state.registrationToken,
          name: name,
          password: password || null,
        })
        .then(signedIn)
        .catch(function (err) {
          alerts.show(err.message);
        });
    });
  });

  /* --------------------------------------------- 4. the password route */

  document
    .querySelector('[data-show-password-signup]')
    .addEventListener('click', function (event) {
      event.preventDefault();
      alerts.hide();
      flow.show('password-signup');
    });

  var passwordSignupForm = document.querySelector('[data-password-signup-form]');
  passwordSignupForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    var name = passwordSignupForm.querySelector('[data-field-name]').value.trim();
    var email = passwordSignupForm.querySelector('[data-field-email]').value.trim();
    var password = passwordSignupForm.querySelector('[data-field-password]').value;

    if (!name || !email || !password) {
      alerts.show('Fill in every field to continue.');
      return;
    }
    if (A.classify(email) !== 'email') {
      alerts.show("That email address doesn't look right — check for a typo.");
      return;
    }
    if (password.length < 8) {
      alerts.show('Your password needs at least 8 characters.');
      return;
    }

    A.busy(passwordSignupForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/register', { name: name, email: email, password: password })
        .then(function () {
          document.querySelector('[data-sent-message]').textContent =
            'We sent a verification link to ' +
            email +
            '. Click it and your account is ready.';
          flow.show('sent');
        })
        .catch(function (err) {
          alerts.show(err.message);
        });
    });
  });

  /* --------------------------------------------------- 5. social sign-up */

  var oauthCompleteForm = document.querySelector('[data-oauth-complete-form]');

  function startOAuthCompletion(pending) {
    state.registrationToken = pending.registration_token;
    state.contact = pending.email || null;
    var nameField = oauthCompleteForm.querySelector('[data-field-name]');
    nameField.value = pending.suggested_name || '';
    document.querySelector('[data-oauth-email-line]').textContent = pending.email
      ? 'Signed in as ' + pending.email + '. This is the last step.'
      : 'Your provider is verified. This is the last step.';
    flow.show('oauth-name');
  }

  oauthCompleteForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    var name = oauthCompleteForm.querySelector('[data-field-name]').value.trim();
    if (!name) {
      alerts.show('Tell us what to call you.');
      return;
    }

    A.busy(oauthCompleteForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/oauth/register', {
          registration_token: state.registrationToken,
          name: name,
        })
        .then(signedIn)
        .catch(function (err) {
          alerts.show(err.message);
        });
    });
  });

  A.wireOAuthButtons({
    onStart: alerts.hide,
    onToken: function (idToken) {
      return A.api
        .post('/auth/oauth', { id_token: idToken })
        .then(function (data) {
          if (data.status === 'registration_required') {
            startOAuthCompletion(data);
            return;
          }
          if (data.status === 'mfa_required' || data.status === 'mfa_enrollment_required') {
            window.location.assign('/login');
            return;
          }
          A.session.write(data);
          window.location.assign(A.destinationFor(data));
        })
        .catch(function (err) {
          alerts.show(err.message);
        });
    },
    onError: function (err) {
      alerts.show(err.message);
    },
  });

  /* /login hands off a half-finished social sign-up through sessionStorage
   * rather than the URL: a registration token in a query string ends up in
   * browser history, in a shared screenshot, and in any referrer header the
   * next navigation sends. */
  (function resumeOAuthHandoff() {
    var raw;
    try {
      raw = sessionStorage.getItem('quickbite_oauth_pending');
      sessionStorage.removeItem('quickbite_oauth_pending');
    } catch (err) {
      raw = null;
    }
    if (!raw) return;
    try {
      startOAuthCompletion(JSON.parse(raw));
    } catch (err) {
      /* Corrupt handoff — fall through to the normal first step. */
    }
  })();

  /* ------------------------------------------------------- back buttons */

  document.querySelectorAll('[data-back]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      alerts.hide();
      flow.show(btn.getAttribute('data-back'));
    });
  });
})();
