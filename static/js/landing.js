/*
 * QuickBite landing page — ANIM-05.
 * Three.js r128 cel-shaded hero (procedural: stamp disc, star, QR cube cluster)
 * + GSAP ScrollTrigger scroll narrative. Both fully disabled under
 * prefers-reduced-motion; the CSS .hero-fallback gradient is the static state.
 */

(function () {
    'use strict';

    var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ── Three.js hero ────────────────────────────────────────────────── */

    function webglAvailable() {
        try {
            var c = document.createElement('canvas');
            return !!(window.WebGLRenderingContext &&
                (c.getContext('webgl') || c.getContext('experimental-webgl')));
        } catch (e) {
            return false;
        }
    }

    function initHero() {
        var canvas = document.getElementById('hero-canvas');
        if (!canvas || !window.THREE || !webglAvailable()) return; // fallback gradient stays

        var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        var scene = new THREE.Scene();
        var camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
        camera.position.set(0, 0, 10);

        // Toon gradient map — 4-step grayscale ramp for cel shading
        var ramp = new Uint8Array([80, 140, 200, 255]);
        var gradientMap = new THREE.DataTexture(ramp, 4, 1, THREE.LuminanceFormat);
        gradientMap.minFilter = THREE.NearestFilter;
        gradientMap.magFilter = THREE.NearestFilter;
        gradientMap.needsUpdate = true;

        function toon(hex) {
            return new THREE.MeshToonMaterial({ color: hex, gradientMap: gradientMap });
        }

        var group = new THREE.Group();

        // Teal loyalty stamp disc
        var disc = new THREE.Mesh(new THREE.CylinderGeometry(1.25, 1.25, 0.28, 48), toon(0x0D9488));
        disc.rotation.x = Math.PI / 2.4;
        disc.position.set(-1.6, -0.9, 0);
        group.add(disc);
        var discRing = new THREE.Mesh(new THREE.TorusGeometry(1.25, 0.09, 12, 48), toon(0x0B7A70));
        discRing.rotation.x = Math.PI / 2.4 + Math.PI / 2;
        discRing.position.copy(disc.position);
        group.add(discRing);

        // Orange 5-point review star
        var starShape = new THREE.Shape();
        for (var i = 0; i < 10; i++) {
            var r = i % 2 === 0 ? 1.0 : 0.45;
            var a = (i / 10) * Math.PI * 2 - Math.PI / 2;
            var x = Math.cos(a) * r;
            var y = Math.sin(a) * r;
            if (i === 0) starShape.moveTo(x, y); else starShape.lineTo(x, y);
        }
        starShape.closePath();
        var star = new THREE.Mesh(
            new THREE.ExtrudeGeometry(starShape, { depth: 0.35, bevelEnabled: false }),
            toon(0xFF6B35)
        );
        star.position.set(1.7, 1.5, -0.5);
        star.rotation.z = 0.25;
        group.add(star);

        // Blue QR cube cluster — 4×4 pattern of small boxes
        var qr = new THREE.Group();
        var pattern = [1,0,1,1, 0,1,1,0, 1,1,0,1, 1,0,1,1];
        var cell = new THREE.BoxGeometry(0.34, 0.34, 0.34);
        for (var row = 0; row < 4; row++) {
            for (var col = 0; col < 4; col++) {
                if (!pattern[row * 4 + col]) continue;
                var cube = new THREE.Mesh(cell, toon(0x1A56DB));
                cube.position.set(col * 0.42 - 0.63, row * 0.42 - 0.63, 0);
                qr.add(cube);
            }
        }
        qr.position.set(2.6, -0.6, 0.4);
        qr.rotation.z = -0.15;
        group.add(qr);

        scene.add(group);
        scene.add(new THREE.AmbientLight(0xffffff, 0.75));
        var sun = new THREE.DirectionalLight(0xffffff, 0.9);
        sun.position.set(3, 5, 6);
        scene.add(sun);

        function layout() {
            var w = canvas.clientWidth || window.innerWidth;
            var h = canvas.clientHeight || window.innerHeight;
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
            // Desktop: composition sits right of the copy. Mobile: behind it, smaller.
            if (w >= 1024) {
                group.position.set(2.8, 0, 0);
                group.scale.setScalar(1);
            } else {
                group.position.set(0.8, 1.8, -2);
                group.scale.setScalar(0.6);
            }
        }
        layout();
        window.addEventListener('resize', layout);

        if (reducedMotion) {
            renderer.render(scene, camera); // one static frame, rotation locked
            return;
        }

        // Lerped mouse tracking — smooth, non-jarring (Doc 4 §7.6)
        var targetX = 0;
        var targetY = 0;
        document.addEventListener('mousemove', function (e) {
            targetX = (e.clientX / window.innerWidth) - 0.5;
            targetY = (e.clientY / window.innerHeight) - 0.5;
        });

        var rafId = null;
        var clock = new THREE.Clock();
        function animate() {
            rafId = requestAnimationFrame(animate);
            var t = clock.getElapsedTime();
            group.rotation.y += (targetX * 0.5 - group.rotation.y) * 0.05;
            group.rotation.x += (targetY * 0.3 - group.rotation.x) * 0.05;
            star.rotation.y = t * 0.4;
            disc.rotation.z = t * 0.25;
            qr.position.y = -0.6 + Math.sin(t * 0.8) * 0.15;
            star.position.y = 1.5 + Math.sin(t * 0.6 + 1) * 0.12;
            renderer.render(scene, camera);
        }

        // Only render while the hero is on screen (ANIM-05 performance criterion)
        var observer = new IntersectionObserver(function (entries) {
            if (entries[0].isIntersecting) {
                if (rafId === null) animate();
            } else if (rafId !== null) {
                cancelAnimationFrame(rafId);
                rafId = null;
            }
        });
        observer.observe(canvas);
    }

    /* ── GSAP scroll narrative ────────────────────────────────────────── */

    function initScroll() {
        if (!window.gsap || !window.ScrollTrigger) return;
        if (reducedMotion) {
            gsap.globalTimeline.pause();
            return;
        }
        gsap.registerPlugin(ScrollTrigger);

        // Hero pins for the first 500px; copy drifts up as you scrub through
        gsap.to('.hero-copy', {
            y: -60,
            opacity: 0.35,
            ease: 'none',
            scrollTrigger: {
                trigger: '.hero',
                start: 'top top',
                end: '+=500',
                pin: true,
                scrub: 1,
            },
        });

        // Feature blocks stagger-reveal (Doc 4 §7.7)
        gsap.from('.feature-card', {
            opacity: 0,
            y: 40,
            duration: 0.6,
            stagger: 0.1,
            ease: 'power2.out',
            scrollTrigger: {
                trigger: '.features-section',
                start: 'top 80%',
                end: 'bottom 20%',
                toggleActions: 'play none none reverse',
            },
        });

        // How-it-works: dim inactive steps; the active one is full strength
        var steps = gsap.utils.toArray('.how-step');
        steps.forEach(function (step) {
            gsap.set(step, { opacity: 0.35 });
            ScrollTrigger.create({
                trigger: step,
                start: 'top 65%',
                end: 'bottom 35%',
                onToggle: function (self) {
                    gsap.to(step, { opacity: self.isActive ? 1 : 0.35, duration: 0.3 });
                    if (self.isActive) syncPhone(Number(step.dataset.step));
                },
            });
        });

        // Section mood shifts white → tint as the story progresses
        gsap.to('.how-section', {
            backgroundColor: '#EFF6FF',
            ease: 'none',
            scrollTrigger: {
                trigger: '.how-section',
                start: 'top 40%',
                end: 'bottom 60%',
                scrub: true,
            },
        });

        // Dashboard mock stat count-up when the feature scrolls in (Anime.js lane)
        ScrollTrigger.create({
            trigger: '.features-section',
            start: 'top 60%',
            once: true,
            onEnter: function () {
                if (!window.anime) return;
                document.querySelectorAll('.stat-value').forEach(function (el, i) {
                    var target = parseFloat(el.dataset.value);
                    var decimals = String(el.dataset.value).indexOf('.') > -1 ? 1 : 0;
                    anime({
                        targets: { val: 0 },
                        val: target,
                        duration: 800,
                        delay: i * 100,
                        easing: 'easeOutQuart',
                        update: function (a) {
                            el.textContent = a.animations[0].currentValue.toFixed(decimals);
                        },
                    });
                });
            },
        });

        // Nav gains a shadow once the page scrolls
        ScrollTrigger.create({
            start: 80,
            onUpdate: function (self) {
                document.getElementById('site-nav').classList.toggle('shadow-sm', self.scroll() > 80);
            },
        });
    }

    // The sticky phone mockup reacts to the active story step:
    // step 3 lands the 5th stamp with the Anime.js spring from ANIM-03.
    var phoneAtStep3 = false;
    function syncPhone(step) {
        var count = document.getElementById('mock-stamp-count');
        var bar = document.getElementById('mock-progress');
        if (!count || !bar) return;
        if (step === 3 && !phoneAtStep3) {
            phoneAtStep3 = true;
            var cell = document.querySelector('.mock-stamp[data-index="4"]');
            if (cell) {
                cell.classList.remove('border-2', 'border-gray-200');
                cell.classList.add('stamp-cell-filled');
                cell.textContent = '★';
                if (window.anime) {
                    anime({
                        targets: cell,
                        scale: [0, 1.2, 1.0],
                        opacity: [0, 1],
                        duration: 500,
                        easing: 'spring(1, 80, 10, 0)',
                    });
                    anime({
                        targets: { val: 4 },
                        val: 5,
                        round: 1,
                        duration: 600,
                        easing: 'easeOutQuart',
                        update: function (a) {
                            count.innerHTML = Math.round(a.animations[0].currentValue) +
                                '<span class="text-[#64748B] text-2xl font-bold"> / 10</span>';
                        },
                    });
                }
            }
            if (window.anime) {
                anime({ targets: bar, width: '50%', duration: 700, easing: 'easeInOutQuart' });
            } else {
                bar.style.width = '50%';
            }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
    function boot() {
        initHero();
        initScroll();
    }
})();
