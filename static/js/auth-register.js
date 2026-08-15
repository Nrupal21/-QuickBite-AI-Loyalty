/* QuickBite AI + Loyalty — restaurant sign-up screen.
 *
 * Single POST to /auth/register. The endpoint never creates a Tenant/User
 * row itself (see auth_service.py's docstring) — it stashes a pending
 * registration in Redis and emails a verification link that lands on
 * /verify-email (pages.py), which is what actually creates the account.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';

  var alertBox = document.querySelector('[data-auth-alert]');
  var steps = {};
  document.querySelectorAll('[data-step]').forEach(function (el) {
    steps[el.getAttribute('data-step')] = el;
  });

  function showStep(name) {
    Object.keys(steps).forEach(function (key) {
      steps[key].hidden = key !== name;
    });
    hideAlert();
    var target = steps[name];
    if (target) {
      target.classList.remove('animate__animated', 'animate__fadeIn');
      void target.offsetWidth; // eslint-disable-line no-unused-expressions
      target.classList.add('animate__animated', 'animate__fadeIn', 'animate__faster');
      var firstField = target.querySelector('input:not([type="hidden"])');
      if (firstField) firstField.focus();
    }
  }

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

  function apiPost(path, body) {
    return fetch(API_BASE + path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!response.ok) {
            // FastAPI wraps HTTPException's `detail=` under a top-level
            // "detail" key — the body is {"detail": {"error": {...}}}, not
            // {"error": {...}} at the root.
            var err = (data && data.detail && data.detail.error) || {};
            var message = err.message || 'Something went wrong. Please try again.';
            if (err.suggestions && err.suggestions.length) {
              message += ' ' + err.suggestions.join(' ');
            }
            if (response.status === 429) {
              var retryAfter = response.headers.get('Retry-After') || err.retry_after_seconds;
              if (retryAfter) message += ' Try again in ' + retryAfter + 's.';
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

  var registerForm = document.querySelector('[data-register-form]');
  if (registerForm) {
    registerForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var restaurantName = registerForm.querySelector('[data-field-restaurant-name]').value.trim();
      var name = registerForm.querySelector('[data-field-name]').value.trim();
      var email = registerForm.querySelector('[data-field-email]').value.trim();
      var password = registerForm.querySelector('[data-field-password]').value;

      if (!restaurantName || !name || !email || !password) {
        showAlert('Fill in every field to continue.');
        return;
      }
      if (password.length < 8) {
        showAlert('Password must be at least 8 characters.');
        return;
      }

      withBusy(registerForm.querySelector('[data-submit-btn]'), function () {
        return apiPost('/auth/register', {
          email: email,
          password: password,
          name: name,
          restaurant_name: restaurantName,
        })
          .then(function () {
            var messageEl = document.querySelector('[data-sent-message]');
            if (messageEl) {
              messageEl.textContent =
                "We've sent a verification link to " + email + '. Click it to activate ' +
                restaurantName + "'s QuickBite account.";
            }
            showStep('sent');
          })
          .catch(function (error) {
            showAlert(error.message);
          });
      });
    });
  }
})();
