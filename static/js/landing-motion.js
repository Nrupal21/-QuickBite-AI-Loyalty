/* QuickBite AI + Loyalty — landing page motion.
   GSAP + ScrollTrigger for entrance and scroll-reveal animation.
   Falls back to a static (fully visible) page if GSAP fails to load or the
   visitor has requested reduced motion. */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var hasGsap = typeof window.gsap !== 'undefined';

  document.documentElement.classList.remove('no-js');

  // ---- Card spotlight — cursor-follow glow, hover-capable pointers only,
  // independent of GSAP (pure CSS transition + mousemove). Shared by the
  // hero mock card and every bento card so it reads as one motif. ---------
  if (!reduceMotion && window.matchMedia('(hover: hover)').matches) {
    document.querySelectorAll('.mock-card, .bento-card').forEach(function (card) {
      var spot = card.querySelector('[data-spotlight]');
      if (!spot) return;
      card.addEventListener('mousemove', function (e) {
        var rect = card.getBoundingClientRect();
        spot.style.left = e.clientX - rect.left + 'px';
        spot.style.top = e.clientY - rect.top + 'px';
      });
      card.addEventListener('mouseenter', function () {
        spot.classList.add('is-active');
      });
      card.addEventListener('mouseleave', function () {
        spot.classList.remove('is-active');
      });
    });
  }

  if (!hasGsap || reduceMotion) {
    document.querySelectorAll('[data-reveal]').forEach(function (el) {
      el.style.opacity = '1';
      el.style.transform = 'none';
    });
    document.querySelectorAll('[data-accent-draw]').forEach(function (el) {
      el.style.transform = 'scaleX(1)';
    });
    animateStaticFills();
    return;
  }

  gsap.registerPlugin(ScrollTrigger);

  // ---- Hero entrance -------------------------------------------------------
  var heroTl = gsap.timeline({ defaults: { ease: 'power3.out', duration: 0.7 } });
  heroTl
    .from('[data-hero-eyebrow]', { opacity: 0, y: 14 })
    .from('[data-hero-title] .line', { opacity: 0, y: 28, stagger: 0.08 }, '-=0.4')
    .from('[data-hero-sub]', { opacity: 0, y: 16 }, '-=0.3')
    .from('[data-hero-actions] > *', { opacity: 0, y: 12, stagger: 0.08 }, '-=0.35')
    .from('[data-hero-fineprint]', { opacity: 0, y: 10 }, '-=0.3')
    // Stacked mock cards settle in two beats: the back card lands first
    // (subtle — it only nudges into its own resting rotate/offset/scale),
    // then the front card + floating chips drop in on top of it.
    .from(
      '[data-hero-visual-back]',
      { opacity: 0, y: 16, rotate: -10, scale: 0.9, duration: 0.7, ease: 'power2.out' },
      '-=0.6'
    )
    .from(
      '[data-hero-visual-front], .mock-card-badge, .mock-card-qr',
      { opacity: 0, y: 24, scale: 0.96, duration: 0.8, ease: 'power2.out', stagger: 0.06 },
      '-=0.55'
    );

  // ---- Generic scroll reveal ------------------------------------------------
  var revealGroups = {};
  document.querySelectorAll('[data-reveal]').forEach(function (el) {
    var group = el.getAttribute('data-reveal-group') || 'default-' + Math.random();
    (revealGroups[group] = revealGroups[group] || []).push(el);
  });

  Object.keys(revealGroups).forEach(function (key) {
    var els = revealGroups[key];
    gsap.from(els, {
      opacity: 0,
      y: 24,
      duration: 0.6,
      ease: 'power2.out',
      stagger: 0.08,
      scrollTrigger: {
        trigger: els[0].closest('[data-reveal-scope]') || els[0],
        start: 'top 85%',
        toggleActions: 'play none none reverse'
      }
    });
  });

  // ---- Bento icon-tile pop-in — a second, slightly-delayed beat on top of
  // the card fade above, so the reveal reads as considered rather than one
  // flat tween applied to everything. ---------------------------------------
  var bentoIcons = document.querySelectorAll('.bento-card .icon-tile');
  if (bentoIcons.length) {
    gsap.from(bentoIcons, {
      opacity: 0,
      scale: 0.5,
      duration: 0.5,
      delay: 0.15,
      ease: 'back.out(1.7)',
      stagger: 0.08,
      scrollTrigger: {
        trigger: '.bento',
        start: 'top 85%',
        toggleActions: 'play none none reverse'
      }
    });
  }

  // ---- Steps connector line draw-in -----------------------------------------
  var stepsLine = document.querySelector('.steps-line-fill');
  if (stepsLine) {
    gsap.to(stepsLine, {
      width: '100%',
      ease: 'none',
      scrollTrigger: {
        trigger: '.steps',
        start: 'top 70%',
        end: 'bottom 60%',
        scrub: 0.6
      }
    });
  }

  // ---- Stat counters ----------------------------------------------------
  document.querySelectorAll('[data-count-to]').forEach(function (el) {
    var target = parseFloat(el.getAttribute('data-count-to'));
    var suffix = el.getAttribute('data-count-suffix') || '';
    var decimals = el.getAttribute('data-count-decimals') ? parseInt(el.getAttribute('data-count-decimals'), 10) : 0;
    var counter = { val: 0 };
    var stat = el.closest('.stat');
    var accent = stat ? stat.querySelector('[data-accent-draw]') : null;
    ScrollTrigger.create({
      trigger: el,
      start: 'top 88%',
      once: true,
      onEnter: function () {
        if (accent) {
          gsap.to(accent, { scaleX: 1, duration: 0.5, ease: 'power3.out' });
        }
        gsap.to(counter, {
          val: target,
          duration: 1.1,
          delay: 0.1,
          ease: 'power3.out',
          onUpdate: function () {
            el.textContent = counter.val.toFixed(decimals) + suffix;
          }
        });
      }
    });
  });

  // ---- Dashboard-card progress bars --------------------------------------
  document.querySelectorAll('[data-progress]').forEach(function (el) {
    var value = el.getAttribute('data-progress');
    ScrollTrigger.create({
      trigger: el,
      start: 'top 90%',
      once: true,
      onEnter: function () {
        gsap.fromTo(el, { width: '0%' }, { width: value + '%', duration: 1, ease: 'power2.out' });
      }
    });
  });

  // ---- Geofence ring "ping" — plays a few times on scroll-into-view, then
  // rests, instead of animating forever like the old CSS keyframe did -------
  document.querySelectorAll('.geofence-viz').forEach(function (viz) {
    var rings = viz.querySelectorAll('.geofence-ring');
    if (!rings.length) return;
    ScrollTrigger.create({
      trigger: viz,
      start: 'top 85%',
      once: true,
      onEnter: function () {
        gsap.fromTo(
          rings,
          { scale: 0.55, opacity: 0.9 },
          {
            scale: 1.15,
            opacity: 0,
            duration: 1.1,
            ease: 'power1.out',
            stagger: 0.35,
            repeat: 2,
            onComplete: function () {
              gsap.set(rings, { scale: 1, opacity: 1 });
            }
          }
        );
      }
    });
  });

  // ---- Loyalty stamp fill (hero mock) ------------------------------------
  var stamps = document.querySelectorAll('[data-hero-visual] .mock-stamp');
  if (stamps.length) {
    gsap.from(stamps, {
      scale: 0,
      opacity: 0,
      duration: 0.4,
      ease: 'back.out(2)',
      stagger: 0.08,
      delay: 1.1
    });
  }

  // ---- Smooth in-page anchor scroll (accounts for fixed nav) -------------
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener('click', function (event) {
      var id = link.getAttribute('href');
      if (!id || id === '#') return;
      var target = document.querySelector(id);
      if (!target) return;
      event.preventDefault();
      var navHeight = nav ? nav.offsetHeight : 0;
      var top = target.getBoundingClientRect().top + window.scrollY - navHeight - 16;
      window.scrollTo({ top: top, behavior: 'smooth' });
    });
  });

  function animateStaticFills() {
    document.querySelectorAll('[data-progress]').forEach(function (el) {
      el.style.width = el.getAttribute('data-progress') + '%';
    });
    document.querySelectorAll('[data-count-to]').forEach(function (el) {
      var target = parseFloat(el.getAttribute('data-count-to'));
      var suffix = el.getAttribute('data-count-suffix') || '';
      var decimals = el.getAttribute('data-count-decimals') ? parseInt(el.getAttribute('data-count-decimals'), 10) : 0;
      el.textContent = target.toFixed(decimals) + suffix;
    });
  }
})();
