/* QuickBite AI + Loyalty — the scan sheet, opened by the dock's centre button.
 *
 * A real flow, not a decorative button: it drives the camera, reads a QR code
 * with the browser's native BarcodeDetector (Chrome/Edge/Android — no
 * library; Safari and Firefox fall straight to the manual-code field, which
 * every browser gets too, since some receipts get typed in on purpose), and
 * posts to POST /api/v1/loyalty/scan — the same endpoint the rest of the
 * loyalty system already uses (app/api/v1/routers/loyalty.py). The service
 * layer's error messages (geofence, rate limit, invalid code) are already
 * written for a diner to read, so they're surfaced verbatim rather than
 * re-worded here.
 *
 * State lives entirely in which one of six sibling <div data-scan-step> is
 * visible; this file only ever shows one at a time and never touches markup
 * beyond that.
 */
(function () {
  'use strict';

  var sheet = document.querySelector('[data-scansheet]');
  var openBtn = document.querySelector('[data-scan-open]');
  if (!sheet || !openBtn) return;

  var steps = {};
  Array.prototype.slice.call(sheet.querySelectorAll('[data-scan-step]')).forEach(function (el) {
    steps[el.getAttribute('data-scan-step')] = el;
  });

  function showStep(name) {
    Object.keys(steps).forEach(function (key) {
      steps[key].hidden = key !== name;
    });
  }

  var video = sheet.querySelector('[data-scan-video]');
  var hint = sheet.querySelector('[data-scan-hint]');
  var manualInput = sheet.querySelector('[data-scan-manual-input]');
  var errorMessageEl = sheet.querySelector('[data-scan-error-message]');

  var stream = null;
  var detectTimer = null;
  var detector = 'BarcodeDetector' in window ? new window.BarcodeDetector({ formats: ['qr_code'] }) : null;

  function stopCamera() {
    if (detectTimer) {
      window.clearInterval(detectTimer);
      detectTimer = null;
    }
    if (stream) {
      stream.getTracks().forEach(function (track) {
        track.stop();
      });
      stream = null;
    }
    if (video) video.srcObject = null;
  }

  // ------------------------------------------------------------ open/close

  function open() {
    sheet.hidden = false;
    document.body.style.overflow = 'hidden';
    showStep('prompt');
  }

  function close() {
    stopCamera();
    sheet.hidden = true;
    document.body.style.overflow = '';
  }

  openBtn.addEventListener('click', open);
  Array.prototype.slice.call(sheet.querySelectorAll('[data-scansheet-close]')).forEach(function (el) {
    el.addEventListener('click', close);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !sheet.hidden) close();
  });

  // -------------------------------------------------------------- submit

  function getPosition() {
    return new Promise(function (resolve, reject) {
      if (!('geolocation' in navigator)) {
        reject(new Error("Your browser can't share your location, so QuickBite can't confirm you're at the restaurant."));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        function (position) {
          resolve(position.coords);
        },
        function () {
          reject(new Error("Location access is needed to confirm you're at the restaurant. Enable it and try again."));
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
      );
    });
  }

  function submitScan(qrToken) {
    showStep('submitting');
    getPosition()
      .then(function (coords) {
        return fetch('/api/v1/loyalty/scan', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            qr_token: qrToken,
            gps_lat: coords.latitude,
            gps_lng: coords.longitude,
          }),
        });
      })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!response.ok) {
              var err = (data && data.detail && data.detail.error) || {};
              throw new Error(err.message || "That didn't work. Try again.");
            }
            return data;
          });
      })
      .then(renderSuccess)
      .catch(function (error) {
        if (errorMessageEl) errorMessageEl.textContent = error.message;
        showStep('error');
      });
  }

  function renderSuccess(result) {
    var countEl = sheet.querySelector('[data-scan-stamp-count]');
    var fillEl = sheet.querySelector('[data-scan-progress-fill]');
    var progressText = sheet.querySelector('[data-scan-progress-text]');
    var rewardEl = sheet.querySelector('[data-scan-reward]');
    var codeEl = sheet.querySelector('[data-scan-redemption-code]');

    if (countEl) countEl.textContent = result.stamp_count;

    var progress = Math.max(0, Math.min(1, result.reward_progress || 0));
    if (fillEl) fillEl.style.setProperty('--scan-progress', progress);
    if (progressText) {
      progressText.textContent = result.reward_unlocked
        ? 'Reward unlocked!'
        : Math.round(progress * 100) + '% of the way to your next reward';
    }

    if (rewardEl) rewardEl.hidden = !result.reward_unlocked;
    if (codeEl) codeEl.textContent = result.redemption_code || '';

    showStep('success');
  }

  // ------------------------------------------------------------- camera

  var startBtn = sheet.querySelector('[data-scan-start]');
  var cancelBtn = sheet.querySelector('[data-scan-cancel]');

  function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      openManual();
      return;
    }

    showStep('camera');
    if (hint) hint.textContent = 'Looking for a QR code…';

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'environment' } })
      .then(function (mediaStream) {
        stream = mediaStream;
        video.srcObject = mediaStream;
        return video.play();
      })
      .then(function () {
        if (!detector) {
          if (hint) hint.textContent = "Your browser can't read QR codes automatically.";
          window.setTimeout(openManual, 900);
          return;
        }
        // Polling BarcodeDetector on an interval rather than every rAF frame
        // — a QR read is cheap but not free, and a receipt isn't moving.
        detectTimer = window.setInterval(function () {
          detector
            .detect(video)
            .then(function (codes) {
              if (codes.length) {
                var value = codes[0].rawValue;
                stopCamera();
                submitScan(value);
              }
            })
            .catch(function () {
              // A single frame failing to decode isn't an error — the QR may
              // just be out of frame or blurred mid-focus. The next tick
              // retries on its own.
            });
        }, 350);
      })
      .catch(function () {
        if (hint) hint.textContent = 'Camera access was blocked. You can enter the code instead.';
        window.setTimeout(openManual, 1200);
      });
  }

  function openManual() {
    stopCamera();
    showStep('manual');
    if (manualInput) {
      manualInput.value = '';
      manualInput.focus();
    }
  }

  if (startBtn) startBtn.addEventListener('click', startCamera);
  if (cancelBtn) {
    cancelBtn.addEventListener('click', function () {
      stopCamera();
      showStep('prompt');
    });
  }

  var manualOpenBtn = sheet.querySelector('[data-scan-manual-open]');
  var manualCancelBtn = sheet.querySelector('[data-scan-manual-cancel]');
  var manualSubmitBtn = sheet.querySelector('[data-scan-manual-submit]');
  var retryBtn = sheet.querySelector('[data-scan-retry]');

  if (manualOpenBtn) manualOpenBtn.addEventListener('click', openManual);
  if (manualCancelBtn) {
    manualCancelBtn.addEventListener('click', function () {
      showStep('prompt');
    });
  }
  if (manualSubmitBtn) {
    manualSubmitBtn.addEventListener('click', function () {
      var value = ((manualInput && manualInput.value) || '').trim();
      // Mirrors ScanRequest.qr_token's min_length=10 (app/schemas/loyalty.py)
      // — catches an obviously-incomplete code before spending a network
      // round trip and a slice of the 6/hour scan rate limit on it.
      if (value.length < 10) {
        manualInput.focus();
        return;
      }
      submitScan(value);
    });
  }
  if (retryBtn) {
    retryBtn.addEventListener('click', function () {
      showStep('prompt');
    });
  }
})();
