/* QuickBite AI + Loyalty — Loyalty Programs page.
 *
 * Same session/auth contract as dashboard.js — see that file's header.
 * GET /api/v1/branches (BRANCH-01) feeds the create form's branch picker.
 * GET/POST/PATCH /api/v1/loyalty/reward-programs is LOYALTY-04's CRUD (no
 * delete — a program is paused via is_active, never removed, since
 * RewardRedemption rows FK onto it).
 */
(function () {
  'use strict';

  var API_BASE = '/api/v1';
  var SESSION_KEY = 'quickbite_staff_session';
  // require_role(RoleLevel.OWNER) gates create + update — Manager can list
  // (GET is Manager+) but never sees the create form or row actions.
  var MANAGE_ROLES = { SUPER_ADMIN: true, OWNER: true };
  var REWARD_TYPE_LABEL = {
    free_item: 'Free item',
    percentage_off: '% off',
    fixed_off: 'Fixed amount off',
  };

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
    var box = document.getElementById('programs-error');
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

  var canManage = !!(session.role && MANAGE_ROLES[session.role]);

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

  // ---------- create form: populate the branch picker ----------

  var createPanel = document.querySelector('[data-create-panel]');
  if (createPanel) createPanel.hidden = !canManage;

  var branchSelect = document.getElementById('program-branch');
  if (canManage && branchSelect) {
    apiFetch('/branches')
      .then(function (branches) {
        branchSelect.innerHTML = branches
          .map(function (b) {
            return '<option value="' + b.id + '">' + b.name + '</option>';
          })
          .join('');
      })
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  // ---------- list ----------

  function programMetaText(program) {
    return (
      program.branch_name + ' · ' + program.stamps_required + ' stamps → ' +
      program.reward_value + ' (' + (REWARD_TYPE_LABEL[program.reward_type] || program.reward_type) + ') · valid ' +
      program.validity_days + ' day' + (program.validity_days === 1 ? '' : 's')
    );
  }

  function editPanelHtml(program) {
    return (
      '<div class="qb-program-edit-panel" data-edit-panel hidden>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Name</label>' +
      '<input class="qb-invite-input" data-field="name" value="' + program.name + '" /></div>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Stamps required</label>' +
      '<input class="qb-invite-input" data-field="stamps_required" type="number" min="1" max="100" value="' + program.stamps_required + '" /></div>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Reward</label>' +
      '<input class="qb-invite-input" data-field="reward_value" value="' + program.reward_value + '" /></div>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Type</label>' +
      '<select class="qb-invite-select" data-field="reward_type">' +
      ['free_item', 'percentage_off', 'fixed_off'].map(function (t) {
        return '<option value="' + t + '"' + (t === program.reward_type ? ' selected' : '') + '>' + REWARD_TYPE_LABEL[t] + '</option>';
      }).join('') +
      '</select></div>' +
      '<div class="qb-invite-field"><label class="qb-invite-label">Valid for (days)</label>' +
      '<input class="qb-invite-input" data-field="validity_days" type="number" min="1" max="365" value="' + program.validity_days + '" /></div>' +
      '<div class="qb-program-edit-actions">' +
      '<button type="button" class="qb-invite-submit" data-save-edit data-program-id="' + program.id + '">Save</button>' +
      '<button type="button" class="qb-program-action" data-cancel-edit>Cancel</button>' +
      '</div></div>'
    );
  }

  function programRowHtml(program) {
    var actionsHtml = canManage
      ? '<div class="qb-program-actions">' +
        '<button type="button" class="qb-program-action" data-program-edit data-program-id="' + program.id + '">Edit</button>' +
        '<button type="button" class="qb-program-action" data-program-toggle data-program-id="' + program.id + '" data-active="' + program.is_active + '">' +
        (program.is_active ? 'Pause' : 'Resume') + '</button>' +
        '</div>'
      : '<span class="qb-role-badge">' + (program.is_active ? 'Active' : 'Paused') + '</span>';

    return (
      '<div class="qb-program-row' + (program.is_active ? '' : ' qb-program-row--paused') + '" data-program-row="' + program.id + '">' +
      '<div class="qb-program-main">' +
      '<div class="qb-program-body">' +
      '<p class="qb-program-name">' + program.name + '</p>' +
      '<p class="qb-program-meta">' + programMetaText(program) + '</p>' +
      '</div>' +
      actionsHtml +
      '</div>' +
      (canManage ? editPanelHtml(program) : '') +
      '</div>'
    );
  }

  function renderPrograms(programs) {
    var mount = document.querySelector('[data-program-rows]');
    if (!mount) return;
    if (!programs.length) {
      mount.innerHTML =
        '<div class="qb-empty">' +
        '<span class="qb-empty-icon material-symbols-outlined" aria-hidden="true">redeem</span>' +
        '<p class="text-sm font-semibold text-brand-ink">No reward programs yet</p>' +
        '<p class="text-xs text-brand-muted max-w-[260px]">' +
        (canManage ? 'Create one above to start collecting stamps.' : 'Ask an Owner to set one up.') +
        '</p></div>';
      return;
    }
    mount.innerHTML = programs.map(programRowHtml).join('');
  }

  function loadPrograms() {
    return apiFetch('/loyalty/reward-programs')
      .then(renderPrograms)
      .catch(function (error) {
        if (error.message !== 'unauthorized') showError(error.message);
      });
  }

  loadPrograms();

  // ---------- create ----------

  var createForm = document.querySelector('[data-create-form]');
  if (createForm) {
    createForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var submitBtn = createForm.querySelector('[type="submit"]');
      var payload = {
        branch_id: createForm.querySelector('[name="branch_id"]').value,
        name: createForm.querySelector('[name="name"]').value.trim(),
        stamps_required: Number(createForm.querySelector('[name="stamps_required"]').value),
        reward_type: createForm.querySelector('[name="reward_type"]').value,
        reward_value: createForm.querySelector('[name="reward_value"]').value.trim(),
        validity_days: Number(createForm.querySelector('[name="validity_days"]').value),
      };
      if (!payload.branch_id || !payload.name || !payload.reward_value) return;

      submitBtn.disabled = true;
      submitBtn.textContent = 'Creating…';
      apiFetch('/loyalty/reward-programs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function () {
          showToast('success', 'Reward program created.');
          createForm.reset();
          return loadPrograms();
        })
        .catch(function (error) {
          if (error.message !== 'unauthorized') showError(error.message);
        })
        .then(function () {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Create program';
        });
    });
  }

  // ---------- edit / toggle (event delegation — rows are re-rendered) ----------

  document.addEventListener('click', function (event) {
    var editBtn = event.target.closest('[data-program-edit]');
    if (editBtn) {
      var row = editBtn.closest('[data-program-row]');
      var panel = row.querySelector('[data-edit-panel]');
      if (panel) panel.hidden = !panel.hidden;
      return;
    }

    var cancelBtn = event.target.closest('[data-cancel-edit]');
    if (cancelBtn) {
      var panelToClose = cancelBtn.closest('[data-edit-panel]');
      if (panelToClose) panelToClose.hidden = true;
      return;
    }

    var saveBtn = event.target.closest('[data-save-edit]');
    if (saveBtn) {
      var panelEl = saveBtn.closest('[data-edit-panel]');
      var payload = {};
      panelEl.querySelectorAll('[data-field]').forEach(function (field) {
        var key = field.dataset.field;
        payload[key] = field.type === 'number' ? Number(field.value) : field.value.trim();
      });
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      apiFetch('/loyalty/reward-programs/' + encodeURIComponent(saveBtn.dataset.programId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then(function () {
          showToast('success', 'Reward program updated.');
          return loadPrograms();
        })
        .catch(function (error) {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Save';
          if (error.message !== 'unauthorized') showError(error.message);
        });
      return;
    }

    var toggleBtn = event.target.closest('[data-program-toggle]');
    if (toggleBtn) {
      var nextActive = toggleBtn.dataset.active !== 'true';
      toggleBtn.disabled = true;
      apiFetch('/loyalty/reward-programs/' + encodeURIComponent(toggleBtn.dataset.programId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: nextActive }),
      })
        .then(function () {
          showToast('success', nextActive ? 'Program resumed.' : 'Program paused.');
          return loadPrograms();
        })
        .catch(function (error) {
          toggleBtn.disabled = false;
          if (error.message !== 'unauthorized') showError(error.message);
        });
    }
  });
})();
