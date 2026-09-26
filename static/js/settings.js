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
    // POST /branches is require_role(OWNER) too — same rank, same gate.
    var branchPanel = document.querySelector('[data-branch-create-panel]');
    if (branchPanel) branchPanel.hidden = !canManageTeam;
  }

  applyRoleGates();

  apiFetch('/auth/me')
    .then(function (data) {
      me = data;
      // /auth/me is the source of truth for the seniority gate — re-apply
      // both role gates against it in case it disagrees with the
      // sessionStorage role (e.g. a demotion since the token was minted).
      canManageTeam = !!(me.role && MANAGE_TEAM_ROLES[me.role]);
      var invitePanel = document.querySelector('[data-invite-panel]');
      if (invitePanel) invitePanel.hidden = !canManageTeam;
      var branchPanel = document.querySelector('[data-branch-create-panel]');
      if (branchPanel) branchPanel.hidden = !canManageTeam;
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

  loadBranches();

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

  // ---------- branches ----------

  function geofenceEditPanelHtml(branch) {
    var mapsUrl = 'https://www.google.com/maps?q=' + branch.gps_lat + ',' + branch.gps_lng;
    return (
      '<div class="qb-program-edit-panel" data-edit-panel hidden>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Latitude</label>' +
      '<input class="qb-invite-input" data-field="gps_lat" type="number" step="any" value="' + branch.gps_lat + '" /></div>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Longitude</label>' +
      '<input class="qb-invite-input" data-field="gps_lng" type="number" step="any" value="' + branch.gps_lng + '" /></div>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Geofence radius (metres)</label>' +
      '<input class="qb-invite-input" data-field="geofence_radius_m" type="number" min="10" max="1000" value="' + branch.geofence_radius_m + '" /></div>' +
      '<div class="qb-program-edit-actions">' +
      '<button type="button" class="qb-program-action" data-use-location-edit>' +
      '<span class="material-symbols-outlined align-middle text-[1rem]" aria-hidden="true">my_location</span> Use my current location</button>' +
      '<a href="' + mapsUrl + '" target="_blank" rel="noopener" class="qb-program-action">' +
      '<span class="material-symbols-outlined align-middle text-[1rem]" aria-hidden="true">map</span> View current pin</a>' +
      '</div>' +
      '<div class="qb-program-edit-actions">' +
      '<button type="button" class="qb-invite-submit" data-save-geofence data-branch-id="' + branch.id + '">Save geofence</button>' +
      '<button type="button" class="qb-program-action" data-cancel-geofence>Cancel</button>' +
      '</div></div>'
    );
  }

  function branchRowHtml(branch) {
    var editBtn = canManageTeam
      ? '<button type="button" class="qb-program-action" data-branch-edit data-branch-id="' + branch.id + '">Edit geofence</button>'
      : '';
    return (
      '<div class="qb-program-row" data-branch-row="' + branch.id + '">' +
      '<div class="qb-program-main">' +
      '<div class="qb-team-avatar" aria-hidden="true"><span class="material-symbols-outlined" style="font-size:1.125rem" aria-hidden="true">storefront</span></div>' +
      '<div class="qb-team-identity">' +
      '<p class="qb-team-email">' + branch.name + '</p>' +
      '<div class="qb-team-meta"><span class="qb-role-badge">' + (branch.is_active ? 'Active' : 'Inactive') + '</span>' +
      '<span class="qb-team-flag"><span class="material-symbols-outlined" aria-hidden="true">radar</span>' + branch.geofence_radius_m + 'm geofence</span>' +
      (branch.gmb_connected ? '<span class="qb-team-flag"><span class="material-symbols-outlined" aria-hidden="true">link</span>Google connected</span>' : '') +
      '</div></div>' +
      '<div class="qb-program-actions">' + editBtn +
      '<button type="button" class="qb-program-action" data-branch-fraud data-branch-id="' + branch.id + '">Fraud attempts</button>' +
      '</div></div>' +
      (canManageTeam ? geofenceEditPanelHtml(branch) : '') +
      '<div class="qb-program-edit-panel" data-fraud-panel hidden></div>' +
      '</div>'
    );
  }

  function renderBranches(branches) {
    var mount = document.querySelector('[data-branch-rows]');
    if (!mount) return;
    if (!branches.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">storefront</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No branches yet</p>' +
        '<p class="text-xs text-brand-muted max-w-[260px]">' +
        (canManageTeam ? 'Add your first branch above to start printing QR codes.' : 'Ask an Owner to add one.') +
        '</p></div>';
      return;
    }
    mount.innerHTML = branches.map(branchRowHtml).join('');
  }

  function loadBranches() {
    return apiFetch('/branches')
      .then(renderBranches)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  function fraudAttemptsHtml(attempts) {
    if (!attempts.length) {
      return '<p class="text-xs text-brand-muted" style="grid-column:1/-1">No out-of-geofence scan attempts for this branch.</p>';
    }
    return (
      '<ul class="qb-fraud-list" style="grid-column:1/-1">' +
      attempts
        .map(function (attempt) {
          var when = new Date(attempt.scanned_at).toLocaleString();
          return (
            '<li><span class="material-symbols-outlined" aria-hidden="true">warning</span>' +
            '<span>' + Math.round(attempt.distance_m) + 'm away (radius is ' + attempt.geofence_radius_m + 'm) — ' +
            (attempt.is_registered_customer ? 'registered customer' : 'guest') + ' — ' + when + '</span></li>'
          );
        })
        .join('') +
      '</ul>'
    );
  }

  document.addEventListener('click', function (event) {
    var editBtn = event.target.closest('[data-branch-edit]');
    if (editBtn) {
      var row = editBtn.closest('[data-branch-row]');
      var panel = row && row.querySelector('[data-edit-panel]');
      if (panel) panel.hidden = !panel.hidden;
      return;
    }

    var cancelBtn = event.target.closest('[data-cancel-geofence]');
    if (cancelBtn) {
      var panelToClose = cancelBtn.closest('[data-edit-panel]');
      if (panelToClose) panelToClose.hidden = true;
      return;
    }

    var locateBtn = event.target.closest('[data-use-location-edit]');
    if (locateBtn) {
      var panelEl = locateBtn.closest('[data-edit-panel]');
      if (!panelEl || !('geolocation' in navigator)) {
        showError("Your browser can't share your location — enter the coordinates directly.");
        return;
      }
      locateBtn.disabled = true;
      navigator.geolocation.getCurrentPosition(
        function (position) {
          panelEl.querySelector('[data-field="gps_lat"]').value = position.coords.latitude;
          panelEl.querySelector('[data-field="gps_lng"]').value = position.coords.longitude;
          locateBtn.disabled = false;
        },
        function () {
          showError('Location access was blocked — enter the coordinates directly.');
          locateBtn.disabled = false;
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
      );
      return;
    }

    var saveBtn = event.target.closest('[data-save-geofence]');
    if (saveBtn) {
      var savePanelEl = saveBtn.closest('[data-edit-panel]');
      var branchId = saveBtn.dataset.branchId;
      var payload = {
        gps_lat: Number(savePanelEl.querySelector('[data-field="gps_lat"]').value),
        gps_lng: Number(savePanelEl.querySelector('[data-field="gps_lng"]').value),
        geofence_radius_m: Number(savePanelEl.querySelector('[data-field="geofence_radius_m"]').value),
      };
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      apiFetch('/branches/' + encodeURIComponent(branchId) + '/geofence', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function (branch) {
          showToast('success', branch.name + '’s geofence was updated.');
          return loadBranches();
        })
        .catch(function (error) {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Save geofence';
          if (error.message !== 'unauthorized') showError(error.message);
        });
      return;
    }

    var fraudBtn = event.target.closest('[data-branch-fraud]');
    if (fraudBtn) {
      var fraudRow = fraudBtn.closest('[data-branch-row]');
      var fraudPanel = fraudRow && fraudRow.querySelector('[data-fraud-panel]');
      if (!fraudPanel) return;
      if (!fraudPanel.hidden) {
        fraudPanel.hidden = true;
        return;
      }
      fraudPanel.hidden = false;
      fraudPanel.innerHTML = '<p class="text-xs text-brand-muted" style="grid-column:1/-1">Loading…</p>';
      apiFetch('/branches/' + encodeURIComponent(fraudBtn.dataset.branchId) + '/fraud-attempts')
        .then(function (attempts) {
          fraudPanel.innerHTML = fraudAttemptsHtml(attempts);
        })
        .catch(function (error) {
          if (error.message !== 'unauthorized') {
            fraudPanel.innerHTML = '<p class="text-xs text-brand-danger" style="grid-column:1/-1">' + error.message + '</p>';
          }
        });
    }
  });

  var useLocationBtn = document.querySelector('[data-use-location]');
  if (useLocationBtn) {
    useLocationBtn.addEventListener('click', function () {
      if (!('geolocation' in navigator)) {
        showError("Your browser can't share your location — enter the coordinates directly.");
        return;
      }
      useLocationBtn.disabled = true;
      var originalText = useLocationBtn.textContent;
      useLocationBtn.textContent = 'Locating…';
      navigator.geolocation.getCurrentPosition(
        function (position) {
          var latInput = document.getElementById('branch-lat');
          var lngInput = document.getElementById('branch-lng');
          if (latInput) latInput.value = position.coords.latitude;
          if (lngInput) lngInput.value = position.coords.longitude;
          useLocationBtn.disabled = false;
          useLocationBtn.textContent = originalText;
        },
        function () {
          showError('Location access was blocked — enter the coordinates directly.');
          useLocationBtn.disabled = false;
          useLocationBtn.textContent = originalText;
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
      );
    });
  }

  var branchForm = document.querySelector('[data-branch-form]');
  if (branchForm) {
    branchForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var submitBtn = branchForm.querySelector('[type="submit"]');
      var payload = {
        name: branchForm.querySelector('[name="name"]').value.trim(),
        address: branchForm.querySelector('[name="address"]').value.trim(),
        gps_lat: Number(branchForm.querySelector('[name="gps_lat"]').value),
        gps_lng: Number(branchForm.querySelector('[name="gps_lng"]').value),
        geofence_radius_m: Number(branchForm.querySelector('[name="geofence_radius_m"]').value),
      };
      if (!payload.name || !payload.address) return;

      submitBtn.disabled = true;
      submitBtn.textContent = 'Adding…';
      apiFetch('/branches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function (branch) {
          showToast('success', branch.name + ' added — its QR code is ready on the QR Codes page.');
          branchForm.reset();
          branchForm.querySelector('[name="geofence_radius_m"]').value = 100;
          return loadBranches();
        })
        .catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        })
        .then(function () {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Add branch';
        });
    });
  }
})();
