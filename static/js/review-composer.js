/* STITCH-04 — review composer: rating + tags -> POST /reviews/generate -> draft card. */
(function () {
  var root = document.getElementById('review-composer');
  if (!root) return;

  var MAX_TAGS = 5;
  var qrToken = new URLSearchParams(window.location.search).get('t') || '';
  var stars = root.querySelectorAll('.star-btn');
  var chips = root.querySelectorAll('.tag-chip');
  var generateBtn = document.getElementById('generate-btn');
  var errorEl = document.getElementById('composer-error');
  var card = document.getElementById('ai-draft-card');
  var draftEl = document.getElementById('ai-draft-text');
  var copyBtn = document.getElementById('copy-btn');
  var copyLabel = document.getElementById('copy-btn-text');
  var ratingLabel = document.getElementById('rating-label');
  var rating = 0;
  var tags = [];

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.toggle('hidden', !msg);
  }

  function refresh() {
    generateBtn.disabled = !(rating && tags.length);
  }

  stars.forEach(function (star) {
    star.addEventListener('click', function () {
      rating = Number(star.dataset.rating);
      stars.forEach(function (s) {
        var value = Number(s.dataset.rating);
        s.classList.toggle('is-active', value <= rating);
        s.setAttribute('aria-checked', String(value === rating));
      });
      ratingLabel.textContent = rating + ' of 5 stars selected';
      if (window.anime) {
        window.anime({ targets: star, scale: [0.8, 1], duration: 600, easing: 'spring(1, 80, 10, 0)' });
      }
      refresh();
    });
  });

  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      var tag = chip.dataset.tag;
      var idx = tags.indexOf(tag);
      if (idx >= 0) {
        tags.splice(idx, 1);
      } else if (tags.length < MAX_TAGS) {
        tags.push(tag);
      }
      chip.setAttribute('aria-pressed', String(tags.indexOf(tag) >= 0));
      refresh();
    });
  });

  generateBtn.addEventListener('click', function () {
    showError('');
    generateBtn.disabled = true;
    fetch(root.dataset.generateUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ branch_qr_token: qrToken, rating: rating, tags: tags }),
    })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (r) {
        if (!r.ok) {
          var err = r.data && r.data.error;
          showError((err && err.message) || 'Could not draft your review. Please try again.');
          return;
        }
        draftEl.textContent = r.data.draft; // textContent: AI output is never parsed as HTML
        card.classList.remove('hidden');
        card.classList.add('flex', 'animate__animated', 'animate__fadeInUp');
      })
      .catch(function () { showError('Network error. Please try again.'); })
      .then(refresh);
  });

  copyBtn.addEventListener('click', function () {
    var target = document.querySelector(copyBtn.dataset.clipboardTarget);
    if (!target || !navigator.clipboard) return;
    navigator.clipboard.writeText(target.textContent).then(function () {
      copyLabel.textContent = 'Copied to clipboard!';
      setTimeout(function () { copyLabel.textContent = 'Copy review'; }, 2200);
    });
  });
})();
