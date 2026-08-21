/* QuickBite — /login.
 *
 * The common sign-in page for everyone: diners, owners, and staff. Three
 * routes to the same account, in the order the product recommends them.
 *
 *   identify -> method -> otp      -> (register-name if new) -> in
 *                      -> password                          -> in
 *   oauth (any step)                -> (handled on /register if new) -> in
 *
 * Any route can land on `mfa` or `mfa-enroll` instead of signing in: Doc 3
 * makes MFA mandatory for Owner, Manager and Super Admin, and the server
 * answers with a challenge rather than a token pair for those roles no matter
 * which credential was used. That is why the branch below keys on the
 * response's `status`, never on which form was submitted.
 */
(function () {
  'use strict';

  var A = window.QuickBiteAuth;
  var alerts = A.alerts();
  var flow = A.steps({
    rail: {
      identify: 0,
      method: 1,
      password: 1,
      otp: 1,
      'register-name': 1,
      mfa: 1,
      'mfa-enroll': 1,
    },
  });

  var state = {
    identifier: null,
    identifierType: null,
    registrationToken: null,
    mfaSessionToken: null,
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

  /* Every credential path funnels through here. `status` is the contract:
   * absent means a token pair, anything else is a gate. */
  function handleAuthResponse(data) {
    if (data.status === 'mfa_required') {
      state.mfaSessionToken = data.mfa_session_token;
      flow.show('mfa');
      return;
    }
    if (data.status === 'mfa_enrollment_required') {
      state.mfaSessionToken = data.mfa_session_token;
      startEnrollment();
      return;
    }
    if (data.status === 'registration_required') {
      state.registrationToken = data.registration_token;
      state.identifier = data.identifier || state.identifier;
      state.identifierType = data.identifier_type || state.identifierType;
      echo();
      flow.show('register-name');
      return;
    }
    signedIn(data);
  }

  function signedIn(tokens) {
    A.session.write(tokens);
    window.location.assign(A.destinationFor(tokens));
  }

  /* ------------------------------------------------------- 1. identify */

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

    /* No lookup call here. Asking the server "does this account exist" before
     * any credential is offered is exactly the enumeration oracle the OTP
     * endpoints are built to avoid, so the choice of method is offered
     * unconditionally and the answer comes after a code is entered. */
    flow.show('method');
  });

  /* --------------------------------------------------- 2. pick a method */

  document.querySelector('[data-method-otp]').addEventListener('click', function () {
    sendCode(this, function () {
      flow.show('otp');
      resend.start();
    });
  });

  document.querySelector('[data-method-password]').addEventListener('click', function () {
    /* A phone number is not a password identifier: users.phone_hash is not a
     * login column for the password route (see UserLogin's docstring), so
     * offering the password form here would guarantee a confusing failure. */
    if (state.identifierType === 'phone') {
      alerts.show(
        'Passwords work with an email address or username. Use a one-time code for a phone number.'
      );
      return;
    }
    flow.show('password');
  });

  function sendCode(button, onSent) {
    alerts.hide();
    return A.busy(button, function () {
      return A.api
        .post('/auth/otp/request', { identifier: state.identifier })
        .then(onSent)
        .catch(function (err) {
          alerts.show(err.message);
        });
    });
  }

  /* -------------------------------------------------------- 3a. password */

  var passwordForm = document.querySelector('[data-password-form]');
  passwordForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    var password = passwordForm.querySelector('[data-field-password]').value;
    if (!password) {
      alerts.show('Enter your password.');
      return;
    }

    A.busy(passwordForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/login', { identifier: state.identifier, password: password })
        .then(handleAuthResponse)
        .catch(function (err) {
          /* The account exists but signs in another way. Route them to the
           * method that works instead of repeating "wrong password" at
           * someone who never had one. */
          if (err.code === 'PASSWORD_NOT_SET') {
            document.querySelector('[data-password-unavailable]').hidden = false;
            flow.show('method');
            return;
          }
          alerts.show(err.message);
        });
    });
  });

  document.querySelector('[data-forgot]').addEventListener('click', function () {
    alerts.show(
      'Password recovery is on its way. For now, sign in with a one-time code — go back and choose "Send me a one-time code".'
    );
  });

  /* ------------------------------------------------------------ 3b. OTP */

  var otpForm = document.querySelector('[data-otp-form]');
  var otpStatus = otpForm.querySelector('[data-otp-status]');
  var otp = A.otpEntry(otpForm.querySelector('[data-otp-entry]'), function () {
    submitCode();
  });

  var resend = A.resendTimer(
    otpForm.querySelector('[data-resend]'),
    otpStatus,
    function (done) {
      sendCode(null, done);
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
        .then(handleAuthResponse)
        .catch(function (err) {
          otp.shake();
          otp.clear();
          alerts.show(err.message);
        });
    });
  }

  /* -------------------------------------------- 4. finish a new sign-up */

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

  /* --------------------------------------------------------------- MFA */

  var mfaForm = document.querySelector('[data-mfa-form]');
  var mfaCode = A.otpEntry(mfaForm.querySelector('[data-otp-entry]'), function () {
    mfaForm.requestSubmit();
  });

  mfaForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    A.busy(mfaForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/mfa/verify', {
          mfa_session_token: state.mfaSessionToken,
          totp_code: mfaCode.value(),
        })
        .then(signedIn)
        .catch(function (err) {
          mfaCode.shake();
          mfaCode.clear();
          alerts.show(err.message);
        });
    });
  });

  function startEnrollment() {
    flow.show('mfa-enroll');
    A.api
      .post('/auth/mfa/enroll', { mfa_session_token: state.mfaSessionToken })
      .then(function (data) {
        document.querySelector('[data-mfa-secret]').textContent = data.secret;
      })
      .catch(function (err) {
        alerts.show(err.message);
      });
  }

  var mfaConfirmForm = document.querySelector('[data-mfa-confirm-form]');
  var mfaConfirmCode = A.otpEntry(
    mfaConfirmForm.querySelector('[data-otp-entry]'),
    function () {
      mfaConfirmForm.requestSubmit();
    }
  );

  mfaConfirmForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    A.busy(mfaConfirmForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/mfa/confirm', {
          mfa_session_token: state.mfaSessionToken,
          totp_code: mfaConfirmCode.value(),
        })
        .then(signedIn)
        .catch(function (err) {
          mfaConfirmCode.shake();
          mfaConfirmCode.clear();
          alerts.show(err.message);
        });
    });
  });

  /* -------------------------------------------------------------- OAuth */

  A.wireOAuthButtons({
    onStart: alerts.hide,
    onToken: function (idToken) {
      return A.api
        .post('/auth/oauth', { id_token: idToken })
        .then(function (data) {
          if (data.status === 'registration_required') {
            /* A brand-new social identity needs a display name, and that form
             * lives on /register. Hand the token over rather than duplicating
             * the step here. */
            try {
              sessionStorage.setItem(
                'quickbite_oauth_pending',
                JSON.stringify({
                  registration_token: data.registration_token,
                  email: data.email,
                  suggested_name: data.suggested_name,
                })
              );
            } catch (err) {
              alerts.show('Enable storage for this site to finish signing up.');
              return;
            }
            window.location.assign('/register?oauth=1');
            return;
          }
          handleAuthResponse(data);
        })
        .catch(function (err) {
          alerts.show(err.message);
        });
    },
    onError: function (err) {
      alerts.show(err.message);
    },
  });

  /* ------------------------------------------------------- back buttons */

  document.querySelectorAll('[data-back]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      alerts.hide();
      flow.show(btn.getAttribute('data-back'));
    });
  });

  /* Already signed in? Nothing here applies — go where they were headed. */
  var existing = A.session.read();
  if (existing && existing.access_token) {
    window.location.replace(A.destinationFor(existing));
  }
})();
