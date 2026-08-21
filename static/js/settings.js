/* QuickBite AI + Loyalty — Settings (Team) page.
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 * GET /api/v1/auth/me establishes who's viewing (user_id, role_level) so the
 * "strictly more senior" deactivate rule (team_service.deactivate) can be
 * mirrored client-side for which rows even show a Deactivate button — the
 * server re-checks it regardless, this is only about not offering a control
 * that would 403.
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';
  // require_role(RoleLevel.OWNER) admits Super Admin too ("at least this
  // senior") — both invite and deactivate are gated at this rank server-side.
  var MANAGE_TEAM_ROLES = { SUPER_ADMIN: true, OWNER: true };
  var INVITABLE_ROLES = ['MANAGER', 'STAFF'];

  function readSession() {
    try {
      var raw = sessionStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  var session = readSession();
  if (!session || !session.access_token) {
    window.location.replace('/login');
    return;
  }

  function showError(message) {
    var box = document.getElementById('settings-error');
    if (!box) return;
    box.textContent = message;
    box.classList.remove('hidden');
  }

  function apiFetch(path, options) {
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
  }

  // ---------- profile chip (same as dashboard.js) ----------

  var initialsEls = document.querySelectorAll('[data-user-initials]');
  var roleLabelEl = document.querySelector('[data-user-role-label]');
  var roleBadgeEl = document.querySelector('[data-user-role-badge]');
  if (session.role) {
    var chipLabel = String(session.role).replace(/_/g, ' ');
    if (roleLabelEl) roleLabelEl.textContent = chipLabel.charAt(0) + chipLabel.slice(1).toLowerCase();
    if (roleBadgeEl) roleBadgeEl.textContent = chipLabel;
    initialsEls.forEach(function (el) {
      el.textContent = chipLabel.charAt(0);
    });
  }

  function roleLabel(role) {
    var label = String(role).replace(/_/g, ' ');
    return label.charAt(0) + label.slice(1).toLowerCase();
  }

  function showToast(kind, message) {
    var toast = document.createElement('div');
    toast.className = 'qb-glass qb-toast qb-toast--' + kind + ' animate__animated animate__fadeInUp';
    toast.innerHTML =
      '<span class="material-symbols-outlined" aria-hidden="true">' +
      (kind === 'success' ? 'check_circle' : 'error') +
      '</span><span>' + message + '</span>';
    document.body.appendChild(toast);
    window.setTimeout(function () {
      toast.classList.remove('animate__fadeInUp');
      toast.classList.add('animate__fadeOutDown');
      window.setTimeout(function () {
        toast.remove();
      }, 400);
    }, 4000);
  }

  var me = null; // { user_id, role, role_level } once /auth/me resolves
  var canManageTeam = false;

  function memberRowHtml(member) {
    var flags = '';
    if (member.mfa_enabled) {
      flags += '<span class="qb-team-flag"><span class="material-symbols-outlined" aria-hidden="true">verified_user</span>MFA</span>';
    }
    if (!member.email_verified) {
      flags += '<span class="qb-team-flag"><span class="material-symbols-outlined" aria-hidden="true">mail</span>Unverified</span>';
    }
    if (!member.is_active) {
      flags += '<span class="qb-team-flag"><span class="material-symbols-outlined" aria-hidden="true">block</span>Deactivated</span>';
    }

    var canDeactivateThis =
      canManageTeam &&
      member.is_active &&
      me &&
      member.user_id !== me.user_id &&
      member.role_level > me.role_level;

    var actionHtml = canDeactivateThis
      ? '<button type="button" class="qb-gmb-action qb-gmb-action--disconnect" data-team-deactivate data-user-id="' +
        member.user_id + '">Remove</button>'
      : '';

    return (
      '<div class="qb-team-row' + (member.is_active ? '' : ' qb-team-row--inactive') + '">' +
      '<div class="qb-team-avatar" aria-hidden="true">' + member.email.charAt(0).toUpperCase() + '</div>' +
      '<div class="qb-team-identity">' +
      '<p class="qb-team-email" title="' + member.email + '">' + member.email + '</p>' +
      '<div class="qb-team-meta"><span class="qb-role-badge">' + roleLabel(member.role) + '</span>' + flags + '</div>' +
      '</div>' +
      actionHtml +
      '</div>'
    );
  }

  function renderMembers(members) {
    var mount = document.querySelector('[data-team-rows]');
    if (!mount) return;
    if (!members.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">group</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No team members yet</p>' +
        '</div>';
      return;
    }
    mount.innerHTML = members.map(memberRowHtml).join('');
  }

  function loadTeam() {
    return apiFetch('/team')
      .then(function (data) {
        renderMembers(data.members);
      })
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  function applyRoleGates() {
    canManageTeam = !!(session.role && MANAGE_TEAM_ROLES[session.role]);
    var invitePanel = document.querySelector('[data-invite-panel]');
    if (invitePanel) invitePanel.hidden = !canManageTeam;
  }

  applyRoleGates();

  apiFetch('/auth/me')
    .then(function (data) {
      me = data;
      // /auth/me is the source of truth for the seniority gate — re-render
      // once it resolves in case it disagrees with the sessionStorage role
      // (e.g. a demotion since the token was minted).
      canManageTeam = !!(me.role && MANAGE_TEAM_ROLES[me.role]);
      var invitePanel = document.querySelector('[data-invite-panel]');
      if (invitePanel) invitePanel.hidden = !canManageTeam;
      return loadTeam();
    })
    .catch(function (error) {
      if (error.message !== 'unauthorized') {
        // /auth/me failing shouldn't block the read-only list — fall back
        // to the sessionStorage role for the invite-panel gate and load the
        // list with deactivate buttons simply not offered (me is null).
        loadTeam();
      }
    });

  // ---------- invite ----------

  var inviteForm = document.querySelector('[data-invite-form]');
  if (inviteForm) {
    inviteForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var emailInput = inviteForm.querySelector('[name="email"]');
      var roleSelect = inviteForm.querySelector('[name="role"]');
      var submitBtn = inviteForm.querySelector('[type="submit"]');
      var email = emailInput.value.trim();
      var role = roleSelect.value;
      if (!email || INVITABLE_ROLES.indexOf(role) === -1) return;

      submitBtn.disabled = true;
      submitBtn.textContent = 'Sending…';
      apiFetch('/team/invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, role: role }),
      })
        .then(function (data) {
          showToast('success', 'Invite sent to ' + data.email + '.');
          emailInput.value = '';
        })
        .catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        })
        .then(function () {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Send invite';
        });
    });
  }

  // ---------- deactivate ----------

  document.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-team-deactivate]');
    if (!btn) return;

    if (btn.dataset.confirming !== 'true') {
      btn.dataset.confirming = 'true';
      btn.textContent = 'Confirm remove?';
      window.setTimeout(function () {
        if (btn.dataset.confirming === 'true') {
          btn.dataset.confirming = 'false';
          btn.textContent = 'Remove';
        }
      }, 4000);
      return;
    }

    btn.disabled = true;
    btn.textContent = 'Removing…';
    apiFetch('/team/' + encodeURIComponent(btn.dataset.userId), { method: 'DELETE' })
      .then(function () {
        showToast('success', 'Team member removed.');
        loadTeam();
      })
      .catch(function (error) {
        btn.disabled = false;
        btn.dataset.confirming = 'false';
        btn.textContent = 'Remove';
        if (error.message !== 'unauthorized') showError(error.message);
      });
  });
})();
