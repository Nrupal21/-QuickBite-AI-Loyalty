/*
 * QuickBite — shared frontend helpers (ANIM-06).
 * Loaded last on every page. Provides reduced-motion handling,
 * the re-triggerable shake, and the toast factory used across screens.
 */

/* Reduced motion — applies to all four libraries (Doc 4 §7.8). */
const qbReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (qbReducedMotion) {
    if (window.anime) anime.speed = 100;                 // near-instant
    if (window.gsap) gsap.globalTimeline.pause();        // freeze scroll narratives
    document.querySelectorAll('.animate__animated')
        .forEach((el) => el.classList.remove('animate__animated'));
}

/* Re-triggerable Animate.css shake — remove, force reflow, re-add. */
function triggerShake(el) {
    el.classList.remove('animate__animated', 'animate__shakeX');
    void el.offsetWidth;
    el.classList.add('animate__animated', 'animate__shakeX');
}

/* Toast: Animate.css slide-in, Anime.js fade-out after 3s (Doc 4 §4). */
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    const bg = type === 'success' ? 'bg-[#059669]' : 'bg-[#DC2626]';
    toast.className =
        `fixed top-4 right-4 z-50 ${bg} text-white px-5 py-3 rounded-xl shadow-lg ` +
        'animate__animated animate__slideInRight animate__faster';
    toast.setAttribute('role', 'status');
    toast.textContent = message;
    document.body.appendChild(toast);

    if (window.anime && !qbReducedMotion) {
        anime({
            targets: toast,
            opacity: [1, 0],
            translateX: [0, 20],
            delay: 3000,
            duration: 400,
            easing: 'easeInQuart',
            complete: () => toast.remove(),
        });
    } else {
        setTimeout(() => toast.remove(), 3000);
    }
}

window.qbReducedMotion = qbReducedMotion;
window.triggerShake = triggerShake;
window.showToast = showToast;
