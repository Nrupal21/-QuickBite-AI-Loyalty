/* QuickBite — /onboarding, the "Join Us" business registration.
 *
 *   business -> verify -> category -> plan -> confirm -> done
 *                                                     -> mfa-enroll -> done
 *
 * Turns a standard account (role USER, no tenant) into an Owner with a
 * restaurant. The order is not arbitrary:
 *
 *   - The second contact is verified BEFORE anything is chosen, so a caller
 *     who abandons the flow at the pricing step has created nothing.
 *   - The category comes before the plan because the category decides which
 *     plans are offered — GET /billing/plans?category_id=. A food truck is
 *     never shown the unlimited-branch Enterprise tier.
 *   - Registration is one call at the end. Nothing is written to the database
 *     until "Register my business", so every back button above it is free.
 *
 * The MFA branch is not an error path. Doc 3 makes MFA mandatory for Owner,
 * so become_restaurant creates the tenant and promotes the role but withholds
 * the token pair until TOTP is enrolled. The business exists either way —
 * which is why that step's copy says so rather than implying a failure.
 */
(function () {
  'use strict';

  var A = window.QuickBiteAuth;
  var alerts = A.alerts();
  var flow = A.steps({
    rail: {
      loading: 1,
      business: 1,
      verify: 2,
      category: 3,
      plan: 4,
      confirm: 4,
      done: 5,
      'mfa-enroll': 5,
      blocked: 0,
    },
  });

  var state = {
    me: null,
    knownContact: null,
    knownType: null, // "email" | "phone"
    secondContact: null,
    secondType: null,
    contactToken: null,
    category: null,
    plan: null,
    mfaSessionToken: null,
  };

  /* ------------------------------------------------- who is signed in? */

  var session = A.session.read();
  if (!session || !session.access_token) {
    showBlocked(
      'Sign in first',
      'Registering a business needs an account. Sign in or create one, then come back to Join Us.'
    );
    return;
  }

  function showBlocked(title, message) {
    document.querySelector('[data-blocked-title]').textContent = title;
    document.querySelector('[data-blocked-message]').textContent = message;
    flow.show('blocked');
  }

  A.api
    .get('/auth/me', { auth: true })
    .then(function (me) {
      state.me = me;

      if (me.tenant_id) {
        showBlocked(
          'You already have a business',
          'This account is already registered as ' +
            (me.role === 'OWNER' ? 'an owner' : 'a team member') +
            '. Head to your dashboard instead.'
        );
        document.querySelector('[data-step="blocked"] .auth-btn--primary').href =
          '/dashboard';
        return;
      }

      prefillContacts(me);
      flow.show('business');
    })
    .catch(function (err) {
      if (err.status === 401) {
        A.session.clear();
        showBlocked(
          'Your session expired',
          'Sign in again and come straight back to Join Us — nothing was lost.'
        );
        return;
      }
      alerts.show(err.message);
      flow.show('business');
    });

  /* The heart of the plan's contact rule: whichever contact the account was
   * created with is shown, locked and already verified; the other one is what
   * we ask for. An account that somehow has both still asks for the phone,
   * because a phone is what the Owner alerts need most. */
  function prefillContacts(me) {
    var knownLabel = document.querySelector('[data-known-label]');
    var knownField = document.querySelector('[data-field-known-contact]');
    var secondLabel = document.querySelector('[data-second-label]');
    var secondField = document.querySelector('[data-field-second-contact]');
    var secondHint = document.querySelector('[data-second-hint]');

    if (me.email) {
      state.knownContact = me.email;
      state.knownType = 'email';
      state.secondType = 'phone';
      knownLabel.textContent = 'Your verified email';
      secondLabel.textContent = 'Add a phone number';
      secondField.type = 'tel';
      secondField.inputMode = 'tel';
      secondField.placeholder = '+919876543210';
      secondField.autocomplete = 'tel';
      secondHint.textContent =
        "We'll text a 6-digit code to confirm it. Owners get reward alerts and security warnings here.";
    } else {
      state.knownContact = me.phone;
      state.knownType = 'phone';
      state.secondType = 'email';
      knownLabel.textContent = 'Your verified phone';
      secondLabel.textContent = 'Add an email address';
      secondField.type = 'email';
      secondField.inputMode = 'email';
      secondField.placeholder = 'you@restaurant.in';
      secondField.autocomplete = 'email';
      secondHint.textContent =
        "We'll email a 6-digit code to confirm it. Receipts and monthly reports go here.";
    }

    knownField.value = state.knownContact || '';
  }

  /* ------------------------------------------------------ 1. the business */

  var businessForm = document.querySelector('[data-business-form]');
  businessForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    var nameField = businessForm.querySelector('[data-field-restaurant-name]');
    var contactField = businessForm.querySelector('[data-field-second-contact]');
    var businessName = nameField.value.trim();
    var contact = contactField.value.trim();

    if (!businessName) {
      alerts.show('Your business needs a name — the one diners will recognise.');
      nameField.focus();
      return;
    }

    var kind = A.classify(contact);
    if (kind !== state.secondType) {
      alerts.show(
        state.secondType === 'phone'
          ? 'Enter a phone number with its country code, like +919876543210.'
          : "Enter an email address — that's the contact this account is missing."
      );
      contactField.setAttribute('aria-invalid', 'true');
      contactField.focus();
      return;
    }
    contactField.removeAttribute('aria-invalid');

    state.businessName = businessName;
    state.secondContact = contact;
    document.querySelectorAll('[data-echo-contact]').forEach(function (el) {
      el.textContent = contact;
    });

    A.busy(businessForm.querySelector('[data-submit]'), function () {
      return requestContactCode().then(function () {
        flow.show('verify');
        resend.start();
      });
    });
  });

  function requestContactCode() {
    return A.api
      .post(
        '/auth/register-restaurant/contact-otp/request',
        { contact: state.secondContact },
        { auth: true }
      )
      .catch(function (err) {
        alerts.show(err.message);
        throw err;
      });
  }

  /* ---------------------------------------------------------- 2. verify */

  var verifyForm = document.querySelector('[data-verify-form]');
  var verifyStatus = verifyForm.querySelector('[data-otp-status]');
  var otp = A.otpEntry(verifyForm.querySelector('[data-otp-entry]'), function () {
    submitContactCode();
  });

  var resend = A.resendTimer(
    verifyForm.querySelector('[data-resend]'),
    verifyStatus,
    function (done) {
      requestContactCode().then(done).catch(function () {
        /* requestContactCode already surfaced the message. */
      });
    }
  );

  verifyForm.addEventListener('submit', function (event) {
    event.preventDefault();
    submitContactCode();
  });

  function submitContactCode() {
    var code = otp.value();
    if (code.length !== 6) {
      alerts.show('Enter all six digits.');
      return;
    }
    alerts.hide();

    A.busy(verifyForm.querySelector('[data-submit]'), function () {
      return A.api
        .post(
          '/auth/register-restaurant/contact-otp/verify',
          { contact: state.secondContact, otp_code: code },
          { auth: true }
        )
        .then(function (data) {
          state.contactToken = data.contact_verification_token;
          flow.show('category');
          loadCategories();
        })
        .catch(function (err) {
          otp.shake();
          otp.clear();
          alerts.show(err.message);
        });
    });
  }

  /* -------------------------------------------------------- 3. category */

  var categoryGrid = document.querySelector('[data-category-grid]');
  var categoryContinue = document.querySelector('[data-category-continue]');
  var categoriesLoaded = false;

  /* Same drawn set the server names through `icon_key`. Kept in the client
   * so the picker paints in one frame with no icon request, and so a key the
   * backend adds before an icon exists falls through to the neutral mark
   * rather than rendering an empty box. */
  var CATEGORY_ICONS = {
    plate: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/>',
    cup: '<path d="M4 8h12v6a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V8Z"/><path d="M16 9h1.5a2.5 2.5 0 0 1 0 5H16"/><path d="M7 2.5v2M11 2.5v2"/>',
    moped:
      '<circle cx="5.5" cy="17" r="3"/><circle cx="18.5" cy="17" r="3"/><path d="M8.5 17h7"/><path d="M18.5 17V9a3 3 0 0 0-3-3H14"/><path d="M5.5 14V9h5l3 5"/>',
    bag: '<path d="M5 8h14l-1.2 11.2a2 2 0 0 1-2 1.8H8.2a2 2 0 0 1-2-1.8L5 8Z"/><path d="M8.5 8V6a3.5 3.5 0 0 1 7 0v2"/>',
    croissant:
      '<path d="M3 15.5c4.5 3 13.5 3 18 0"/><path d="M4.5 15.5C4.5 10.5 7.9 6.5 12 6.5s7.5 4 7.5 9"/><path d="M9 15.2c0-3.4 1.3-6.2 3-6.2s3 2.8 3 6.2"/><path d="M3 15.5 2 19M21 15.5 22 19"/>',
    glass: '<path d="M5 4h14l-7 8-7-8Z"/><path d="M12 12v7"/><path d="M8.5 19h7"/>',
    cloche:
      '<path d="M3 17h18"/><path d="M4.5 17a7.5 7.5 0 0 1 15 0"/><path d="M12 6.5V5"/><circle cx="12" cy="4" r="1"/>',
    truck:
      '<path d="M2 7h11v10H2z"/><path d="M13 10h4l4 3.5V17h-8"/><circle cx="7" cy="18.5" r="2"/><circle cx="17" cy="18.5" r="2"/>',
  };

  function categoryIcon(key) {
    var body = CATEGORY_ICONS[key] || CATEGORY_ICONS.plate;
    return (
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      body +
      '</svg>'
    );
  }

  function loadCategories() {
    if (categoriesLoaded) return;
    categoriesLoaded = true;

    A.api
      .get('/catalog/business-categories')
      .then(function (categories) {
        categoryGrid.innerHTML = '';
        if (!categories.length) {
          categoryGrid.hidden = true;
          document.querySelector('[data-category-empty]').hidden = false;
          return;
        }
        categories.forEach(function (category) {
          var button = document.createElement('button');
          button.type = 'button';
          button.className = 'category-option';
          button.setAttribute('aria-pressed', 'false');
          button.innerHTML =
            '<span class="category-icon">' +
            categoryIcon(category.icon_key) +
            '</span><span><span class="category-name"></span>' +
            '<span class="category-tagline"></span></span>';
          button.querySelector('.category-name').textContent = category.display_name;
          button.querySelector('.category-tagline').textContent = category.tagline;

          button.addEventListener('click', function () {
            state.category = category;
            categoryGrid.querySelectorAll('.category-option').forEach(function (other) {
              other.setAttribute('aria-pressed', String(other === button));
            });
            categoryContinue.disabled = false;
          });

          categoryGrid.appendChild(button);
        });
      })
      .catch(function (err) {
        categoryGrid.innerHTML = '';
        categoryGrid.hidden = true;
        document.querySelector('[data-category-empty]').hidden = false;
        alerts.show(err.message);
      });
  }

  categoryContinue.addEventListener('click', function () {
    if (!state.category) return;
    alerts.hide();
    document.querySelectorAll('[data-echo-category]').forEach(function (el) {
      el.textContent = state.category.display_name;
    });
    flow.show('plan');
    loadPlans();
  });

  /* ------------------------------------------------------------ 4. plan */

  var planList = document.querySelector('[data-plan-list]');
  var planContinue = document.querySelector('[data-plan-continue]');
  var loadedPlansFor = null;

  function formatPrice(paise) {
    if (!paise) return 'Free';
    /* Prices are stored in paise; the pricing page shows whole rupees, since
     * no plan is priced to the paisa and "₹2,999.00" reads as noise. */
    var rupees = Math.round(paise / 100);
    return '₹' + rupees.toLocaleString('en-IN');
  }

  function limitSummary(limits) {
    if (!limits) return '';
    var parts = [];
    function count(value, singular, plural) {
      if (value === undefined || value === null) return null;
      if (value === -1) return 'Unlimited ' + plural;
      return value + ' ' + (value === 1 ? singular : plural);
    }
    [
      count(limits.branches, 'branch', 'branches'),
      count(limits.team_members, 'team member', 'team members'),
      count(limits.ai_responses_pm, 'AI reply/mo', 'AI replies/mo'),
    ].forEach(function (part) {
      if (part) parts.push(part);
    });
    return parts.join(' · ');
  }

  function loadPlans() {
    if (!state.category) return;
    if (loadedPlansFor === state.category.id) return;
    loadedPlansFor = state.category.id;

    planContinue.disabled = true;
    state.plan = null;
    planList.hidden = false;
    document.querySelector('[data-plan-empty]').hidden = true;
    planList.innerHTML =
      '<div class="auth-skeleton" aria-hidden="true"></div>' +
      '<div class="auth-skeleton" aria-hidden="true"></div>';

    A.api
      .get('/billing/plans?category_id=' + encodeURIComponent(state.category.id))
      .then(function (plans) {
        planList.innerHTML = '';
        if (!plans.length) {
          planList.hidden = true;
          document.querySelector('[data-plan-empty]').hidden = false;
          return;
        }
        plans.forEach(function (plan) {
          var button = document.createElement('button');
          button.type = 'button';
          button.className = 'plan-option';
          button.setAttribute('aria-pressed', 'false');
          button.innerHTML =
            '<span><span class="plan-name"></span>' +
            '<span class="plan-limits"></span>' +
            '<span class="plan-trial" hidden></span></span>' +
            '<span class="plan-price"><span class="plan-amount"></span>' +
            '<span class="plan-period"></span></span>';

          button.querySelector('.plan-name').textContent = plan.display_name;
          button.querySelector('.plan-limits').textContent = limitSummary(
            plan.feature_limits
          );
          button.querySelector('.plan-amount').textContent = formatPrice(
            plan.price_monthly_inr
          );
          button.querySelector('.plan-period').textContent = plan.price_monthly_inr
            ? 'per month'
            : 'forever';

          var trial = button.querySelector('.plan-trial');
          if (plan.trial_days > 0) {
            trial.textContent = plan.trial_days + '-day free trial';
            trial.hidden = false;
          } else if (plan.trial_days === -1) {
            trial.textContent = 'Custom terms';
            trial.hidden = false;
          }

          button.addEventListener('click', function () {
            state.plan = plan;
            planList.querySelectorAll('.plan-option').forEach(function (other) {
              other.setAttribute('aria-pressed', String(other === button));
            });
            planContinue.disabled = false;
          });

          planList.appendChild(button);
        });
      })
      .catch(function (err) {
        planList.innerHTML = '';
        planList.hidden = true;
        document.querySelector('[data-plan-empty]').hidden = false;
        alerts.show(err.message);
      });
  }

  planContinue.addEventListener('click', function () {
    if (!state.plan) return;
    alerts.hide();

    document.querySelectorAll('[data-echo-business]').forEach(function (el) {
      el.textContent = state.businessName;
    });
    document.querySelector('[data-summary-business]').textContent = state.businessName;
    document.querySelector('[data-summary-category]').textContent =
      state.category.display_name;
    document.querySelector('[data-summary-plan]').textContent =
      state.plan.display_name + ' · ' + formatPrice(state.plan.price_monthly_inr);
    document.querySelector('[data-summary-contacts]').textContent =
      state.knownContact + ' · ' + state.secondContact;

    flow.show('confirm');
  });

  /* --------------------------------------------------------- 5. register */

  var registerButton = document.querySelector('[data-register-submit]');
  registerButton.addEventListener('click', function () {
    alerts.hide();

    A.busy(registerButton, function () {
      return A.api
        .post(
          '/auth/register-restaurant',
          {
            restaurant_name: state.businessName,
            category_id: state.category.id,
            plan_id: state.plan.id,
            contact_verification_token: state.contactToken,
          },
          { auth: true }
        )
        .then(function (data) {
          if (data.status === 'mfa_enrollment_required') {
            /* The business is already created and the role already promoted —
             * only the session is withheld. The copy on that step says so. */
            state.mfaSessionToken = data.mfa_session_token;
            startEnrollment();
            return;
          }
          A.session.write(data);
          finish(data);
        })
        .catch(function (err) {
          if (err.code === 'CONTACT_VERIFICATION_EXPIRED') {
            /* The 15-minute window ran out while they were reading pricing.
             * Send them back to the code, not to the start. */
            state.contactToken = null;
            otp.clear();
            flow.show('verify');
            requestContactCode().then(function () {
              resend.start();
            });
            alerts.show('That verification expired. We sent a fresh code.');
            return;
          }
          alerts.show(err.message);
        });
    });
  });

  function finish(data) {
    document.querySelector('[data-done-message]').textContent =
      state.businessName +
      ' is live at ' +
      data.subdomain +
      '. We sent a confirmation to ' +
      state.knownContact +
      ' and ' +
      state.secondContact +
      '.';
    flow.show('done');
  }

  /* ------------------------------------------------------- MFA for Owner */

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
  var mfaCode = A.otpEntry(mfaConfirmForm.querySelector('[data-otp-entry]'), function () {
    mfaConfirmForm.requestSubmit();
  });

  mfaConfirmForm.addEventListener('submit', function (event) {
    event.preventDefault();
    alerts.hide();

    A.busy(mfaConfirmForm.querySelector('[data-submit]'), function () {
      return A.api
        .post('/auth/mfa/confirm', {
          mfa_session_token: state.mfaSessionToken,
          totp_code: mfaCode.value(),
        })
        .then(function (tokens) {
          A.session.write(tokens);
          document.querySelector('[data-done-message]').textContent =
            state.businessName +
            ' is live and two-factor is on. We sent a confirmation to ' +
            state.knownContact +
            ' and ' +
            state.secondContact +
            '.';
          flow.show('done');
        })
        .catch(function (err) {
          mfaCode.shake();
          mfaCode.clear();
          alerts.show(err.message);
        });
    });
  });

  /* ------------------------------------------------------- back buttons */

  document.querySelectorAll('[data-back]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      alerts.hide();
      flow.show(btn.getAttribute('data-back'));
    });
  });
})();
