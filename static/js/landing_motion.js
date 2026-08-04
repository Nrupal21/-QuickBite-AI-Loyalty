/**
 * QuickBite AI Landing Page Motion Engine
 * Powered by GSAP ScrollTrigger, Three.js r128, and Anime.js 3.2.1
 * Colors strictly driven by static/css/color.css
 */

document.addEventListener("DOMContentLoaded", () => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // ----------------------------------------------------
    // 0. MOBILE NAV SLIDE-IN PANEL
    // ----------------------------------------------------
    const mobileNavOpenBtn = document.getElementById("mobile-nav-open");
    const mobileNavCloseBtn = document.getElementById("mobile-nav-close");
    const mobileNavPanel = document.getElementById("mobile-nav-panel");

    function openMobileNav() {
        if (!mobileNavPanel) return;
        mobileNavPanel.classList.add("open");
        if (mobileNavOpenBtn) mobileNavOpenBtn.setAttribute("aria-expanded", "true");
    }

    function closeMobileNav() {
        if (!mobileNavPanel) return;
        mobileNavPanel.classList.remove("open");
        if (mobileNavOpenBtn) mobileNavOpenBtn.setAttribute("aria-expanded", "false");
    }

    if (mobileNavOpenBtn) mobileNavOpenBtn.addEventListener("click", openMobileNav);
    if (mobileNavCloseBtn) mobileNavCloseBtn.addEventListener("click", closeMobileNav);
    if (mobileNavPanel) {
        mobileNavPanel.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", closeMobileNav);
        });
    }
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeMobileNav();
    });

    // ----------------------------------------------------
    // 1. SECTION 0: PRELOADER (White Background Curtain Reveal)
    // ----------------------------------------------------
    const preloader = document.getElementById("qb-preloader");
    const loaderPercent = document.getElementById("loader-percent");
    const loaderBar = document.getElementById("loader-bar");

    let progress = 0;
    const interval = setInterval(() => {
        progress += Math.floor(Math.random() * 12) + 8;
        if (progress >= 100) {
            progress = 100;
            clearInterval(interval);
            setTimeout(dismissPreloader, 300);
        }
        if (loaderPercent) loaderPercent.textContent = `${progress}%`;
        if (loaderBar) loaderBar.style.width = `${progress}%`;
    }, 60);

    function dismissPreloader() {
        if (!preloader) return;
        anime({
            targets: preloader,
            opacity: [1, 0],
            translateY: [0, "-100%"],
            easing: "cubicBezier(0.77, 0, 0.175, 1)",
            duration: 800,
            complete: () => {
                preloader.style.display = "none";
                initHeroAnimations();
            }
        });
    }

    // ----------------------------------------------------
    // 2. CUSTOM DUAL-RING SPRING CURSOR
    // ----------------------------------------------------
    const cursorDot = document.getElementById("cursor-dot");
    const cursorRing = document.getElementById("cursor-ring");
    const cursorBadge = document.getElementById("cursor-badge");

    let mouseX = window.innerWidth / 2;
    let mouseY = window.innerHeight / 2;
    let ringX = mouseX;
    let ringY = mouseY;

    window.addEventListener("mousemove", (e) => {
        mouseX = e.clientX;
        mouseY = e.clientY;
        if (cursorDot) {
            cursorDot.style.transform = `translate3d(${mouseX}px, ${mouseY}px, 0)`;
        }
    });

    function renderCursor() {
        ringX += (mouseX - ringX) * 0.15;
        ringY += (mouseY - ringY) * 0.15;

        if (cursorRing) {
            cursorRing.style.transform = `translate3d(${ringX - 20}px, ${ringY - 20}px, 0)`;
        }

        requestAnimationFrame(renderCursor);
    }
    renderCursor();

    // Magnetic and state-morphing targets
    const interactiveElements = document.querySelectorAll("[data-cursor]");
    interactiveElements.forEach((el) => {
        el.addEventListener("mouseenter", () => {
            const mode = el.getAttribute("data-cursor");
            if (cursorBadge) {
                cursorBadge.textContent = mode;
                cursorBadge.classList.remove("opacity-0", "scale-90");
                cursorBadge.classList.add("opacity-100", "scale-100");
            }
            if (cursorRing) {
                cursorRing.classList.add("ring-expanded");
            }
        });

        el.addEventListener("mouseleave", () => {
            if (cursorBadge) {
                cursorBadge.classList.add("opacity-0", "scale-90");
                cursorBadge.classList.remove("opacity-100", "scale-100");
            }
            if (cursorRing) {
                cursorRing.classList.remove("ring-expanded");
            }
        });
    });

    // ----------------------------------------------------
    // 3. THREE.JS 3D RESTAURANT TABLE STAND
    // ----------------------------------------------------
    const canvasContainer = document.getElementById("hero-canvas") || document.getElementById("table-stand-3d");
    let scene, camera, renderer, standGroup;

    if (canvasContainer && typeof THREE !== "undefined") {
        scene = new THREE.Scene();

        camera = new THREE.PerspectiveCamera(
            45,
            canvasContainer.clientWidth / canvasContainer.clientHeight,
            0.1,
            1000
        );
        camera.position.set(0, 0.5, 4.5);

        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
        renderer.setSize(canvasContainer.clientWidth, canvasContainer.clientHeight);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        canvasContainer.appendChild(renderer.domElement);

        // Lighting
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.85);
        scene.add(ambientLight);

        const pointLight1 = new THREE.PointLight(0x0fecf0, 2, 10);
        pointLight1.position.set(2, 3, 2);
        scene.add(pointLight1);

        const pointLight2 = new THREE.PointLight(0xfb5204, 1.5, 10);
        pointLight2.position.set(-2, -1, 2);
        scene.add(pointLight2);

        standGroup = new THREE.Group();

        // Base plate (Brass Metallic)
        const baseGeo = new THREE.BoxGeometry(1.6, 0.12, 1.0);
        const brassMat = new THREE.MeshStandardMaterial({
            color: 0xcca633, // --color-vanilla-custard-500
            metalness: 0.8,
            roughness: 0.25
        });
        const baseMesh = new THREE.Mesh(baseGeo, brassMat);
        baseMesh.position.y = -0.8;
        standGroup.add(baseMesh);

        // Acrylic Glass Stand Body
        const glassGeo = new THREE.BoxGeometry(1.4, 1.8, 0.08);
        const glassMat = new THREE.MeshPhysicalMaterial({
            color: 0xffffff,
            transparent: true,
            opacity: 0.85,
            roughness: 0.1,
            transmission: 0.9,
            thickness: 0.5
        });
        const glassMesh = new THREE.Mesh(glassGeo, glassMat);
        glassMesh.position.y = 0.1;
        standGroup.add(glassMesh);

        // QR Code Faceplate (Electric Dark Cyan border)
        const qrGeo = new THREE.PlaneGeometry(1.1, 1.1);
        const canvasQR = document.createElement("canvas");
        canvasQR.width = 256;
        canvasQR.height = 256;
        const ctx = canvasQR.getContext("2d");
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, 256, 256);
        ctx.strokeStyle = "#0fecf0";
        ctx.lineWidth = 12;
        ctx.strokeRect(10, 10, 236, 236);

        // Draw simple stylized QR grid
        ctx.fillStyle = "#001a24";
        for (let i = 0; i < 8; i++) {
            for (let j = 0; j < 8; j++) {
                if ((i + j) % 2 === 0) {
                    ctx.fillRect(30 + i * 24, 30 + j * 24, 20, 20);
                }
            }
        }

        const qrTexture = new THREE.CanvasTexture(canvasQR);
        const qrMat = new THREE.MeshBasicMaterial({ map: qrTexture });
        const qrMesh = new THREE.Mesh(qrGeo, qrMat);
        qrMesh.position.set(0, 0.1, 0.045);
        standGroup.add(qrMesh);

        scene.add(standGroup);

        // Mouse Parallax & Animation Loop
        let targetRotX = 0;
        let targetRotY = 0;

        window.addEventListener("mousemove", (e) => {
            const normX = (e.clientX / window.innerWidth) - 0.5;
            const normY = (e.clientY / window.innerHeight) - 0.5;
            targetRotY = normX * 0.6;
            targetRotX = normY * 0.4;
        });

        function animate3D() {
            requestAnimationFrame(animate3D);
            if (standGroup) {
                standGroup.rotation.y += (targetRotY - standGroup.rotation.y) * 0.05;
                standGroup.rotation.x += (targetRotX - standGroup.rotation.x) * 0.05;
                if (!reducedMotion) {
                    standGroup.position.y = Math.sin(Date.now() * 0.0015) * 0.08;
                }
            }
            renderer.render(scene, camera);
        }
        animate3D();

        // Responsive Resize
        window.addEventListener("resize", () => {
            if (!canvasContainer) return;
            camera.aspect = canvasContainer.clientWidth / canvasContainer.clientHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(canvasContainer.clientWidth, canvasContainer.clientHeight);
        });
    }

    // ----------------------------------------------------
    // 3B. HERO HEADLINE CHARACTER-REVEAL (masked, per-line)
    // ----------------------------------------------------
    function splitHeroHeading(heading) {
        // Wrap each text node's characters in spans, preserving existing
        // element boundaries (e.g. the <br/> and <span class="text-gradient">).
        const walk = (node) => {
            Array.from(node.childNodes).forEach((child) => {
                if (child.nodeType === Node.TEXT_NODE) {
                    const frag = document.createDocumentFragment();
                    const words = child.textContent.split(/(\s+)/);
                    words.forEach((word) => {
                        if (word.trim() === "") {
                            frag.appendChild(document.createTextNode(word));
                            return;
                        }
                        const lineSpan = document.createElement("span");
                        lineSpan.className = "text-line";
                        Array.from(word).forEach((ch) => {
                            const charSpan = document.createElement("span");
                            charSpan.className = "char";
                            charSpan.textContent = ch;
                            lineSpan.appendChild(charSpan);
                        });
                        frag.appendChild(lineSpan);
                    });
                    node.replaceChild(frag, child);
                } else if (child.nodeType === Node.ELEMENT_NODE) {
                    walk(child);
                }
            });
        };
        walk(heading);
        return heading.querySelectorAll(".char");
    }

    function revealHeroHeading() {
        const heading = document.querySelector(".hero-heading");
        if (!heading) return;

        if (reducedMotion) {
            heading.style.opacity = 1;
            return;
        }

        const chars = splitHeroHeading(heading);
        gsap.set(chars, { opacity: 0, y: "100%", rotateX: -85 });
        gsap.to(chars, {
            opacity: 1,
            y: "0%",
            rotateX: 0,
            duration: 0.9,
            stagger: 0.018,
            ease: "power4.out"
        });
    }

    // ----------------------------------------------------
    // 3C. SCROLL-PROGRESS RAIL (side tick indicator)
    // ----------------------------------------------------
    function initScrollRail() {
        const ticks = document.querySelectorAll("#scroll-rail .rail-tick");
        if (!ticks.length || typeof ScrollTrigger === "undefined") return;

        ticks.forEach((tick) => {
            const targetSelector = tick.getAttribute("data-rail-target");
            const target = document.querySelector(targetSelector);
            if (!target) return;

            tick.addEventListener("click", () => {
                target.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth" });
            });

            ScrollTrigger.create({
                trigger: target,
                start: "top center",
                end: "bottom center",
                onEnter: () => tick.classList.add("active"),
                onEnterBack: () => tick.classList.add("active"),
                onLeave: () => tick.classList.remove("active"),
                onLeaveBack: () => tick.classList.remove("active")
            });
        });
    }

    // ----------------------------------------------------
    // 4. GSAP SCROLLTRIGGER HERO & INTERACTIVE STAGE
    // ----------------------------------------------------
    function initHeroAnimations() {
        if (typeof gsap === "undefined" || typeof ScrollTrigger === "undefined") return;
        gsap.registerPlugin(ScrollTrigger);

        revealHeroHeading();
        initScrollRail();

        // Hero Title Fade-Up with Custom Cubic-Bezier
        gsap.from(".hero-stagger", {
            y: reducedMotion ? 0 : 40,
            opacity: 0,
            duration: reducedMotion ? 0.01 : 1,
            stagger: reducedMotion ? 0 : 0.15,
            ease: reducedMotion ? "none" : "cubic-bezier(0.23, 1, 0.32, 1)"
        });

        // Batch scroll-reveal entry animations for feature sections (gsap-scrolltrigger skill)
        ScrollTrigger.batch(".scroll-trigger-section", {
            onEnter: (batch) => {
                gsap.from(batch, {
                    opacity: 0,
                    y: reducedMotion ? 0 : 30,
                    duration: reducedMotion ? 0.01 : 0.8,
                    stagger: reducedMotion ? 0 : 0.15,
                    ease: "power2.out"
                });
            },
            once: true
        });

        // Scrub Zoom for 3D Stand into Smart AI Review Section
        if (canvasContainer && !reducedMotion) {
            gsap.to(canvasContainer, {
                scrollTrigger: {
                    trigger: "#hero-section",
                    start: "top top",
                    end: "bottom top",
                    scrub: 1
                },
                scale: 1.15,
                y: 60,
                opacity: 0.9
            });
        }

        // Horizontal Scroll Pinning for Customer Reviews Wall
        const reviewsContainer = document.getElementById("reviews-track");
        if (reviewsContainer) {
            const totalScrollWidth = reviewsContainer.scrollWidth - window.innerWidth + 200;
            gsap.to(reviewsContainer, {
                x: () => -totalScrollWidth,
                ease: "none",
                scrollTrigger: {
                    trigger: "#reviews",
                    pin: true,
                    scrub: 1,
                    end: () => "+=" + totalScrollWidth,
                    invalidateOnRefresh: true
                }
            });
        }
    }

    // ----------------------------------------------------
    // 5. SMART AI REVIEW SUGGESTION GENERATOR (Direct QR Flow)
    // ----------------------------------------------------
    const dishChips = document.querySelectorAll(".dish-chip");
    const aiReviewText = document.getElementById("ai-review-output");
    const aiDraftBadge = document.getElementById("ai-draft-badge");

    const reviewTemplates = {
        "Butter Chicken": "The Crispy Butter Chicken at QuickBite is absolutely outstanding! Creamy, rich gravy paired perfectly with hot garlic naan. Service was incredibly warm and fast.",
        "Dal Makhani": "Best slow-cooked Dal Makhani in town! Velvet smooth texture and authentic aroma. Staff made sure our family felt right at home.",
        "Paneer Tikka": "Smoky, perfectly spiced Paneer Tikka grilled to perfection. Generous portions and quick digital check-out. 5 stars all around!",
        "Ambience": "Incredible dining atmosphere with modern music and quick QR ordering. Clean tables and high quality hospitality!"
    };

    let activeTyping = null;

    dishChips.forEach((chip) => {
        chip.addEventListener("click", () => {
            dishChips.forEach(c => {
                c.classList.remove("bg-rusty-spice-500", "text-white");
                c.classList.add("bg-white", "text-ink-950");
            });
            chip.classList.remove("bg-white", "text-ink-950");
            chip.classList.add("bg-rusty-spice-500", "text-white");

            const dishName = chip.getAttribute("data-dish");
            const newText = reviewTemplates[dishName] || reviewTemplates["Butter Chicken"];

            // Live Typing Animation with cleanup
            if (aiReviewText) {
                if (activeTyping) clearInterval(activeTyping);
                if (aiDraftBadge) aiDraftBadge.textContent = "AI Drafting...";
                aiReviewText.innerHTML = "";
                let i = 0;
                activeTyping = setInterval(() => {
                    if (i < newText.length) {
                        aiReviewText.innerHTML += newText.charAt(i);
                        i++;
                    } else {
                        clearInterval(activeTyping);
                        activeTyping = null;
                        if (aiDraftBadge) aiDraftBadge.textContent = "Draft Ready ✨";
                    }
                }, 16);
            }
        });
    });

    // ----------------------------------------------------
    // 6. LOYALTY CARD SCROLL-STAMPING COUNTER
    // ----------------------------------------------------
    const stampCells = document.querySelectorAll(".stamp-cell");

    const stampTrigger = document.getElementById("loyalty-section");
    if (stampTrigger && typeof ScrollTrigger !== "undefined") {
        ScrollTrigger.create({
            trigger: "#loyalty-section",
            start: "top 60%",
            onEnter: () => animateStampSequence(),
            once: true
        });
    }

    function animateStampSequence() {
        stampCells.forEach((cell, idx) => {
            setTimeout(() => {
                if (typeof anime !== "undefined" && !reducedMotion) {
                    anime({
                        targets: cell,
                        scale: [0.8, 1.15, 1],
                        opacity: [0.4, 1],
                        easing: "spring(1, 80, 10, 0)",
                        complete: () => {
                            cell.classList.add("stamped");
                        }
                    });
                } else {
                    cell.classList.add("stamped");
                }
            }, reducedMotion ? 0 : idx * 300);
        });
    }
});

