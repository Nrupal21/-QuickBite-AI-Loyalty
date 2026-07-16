---
trigger: model_decision
description: Activate when generating Stitch.ai screens, adding Jinja2 wiring, or implementing animations
---

# QuickBite — Frontend Rules

## Stitch.ai Screen Generation Workflow
1. Write Stitch.ai prompt including: hex colours, screen purpose, key components
2. Generate HTML + Tailwind
3. Export to correct `app/templates/{domain}/` subfolder (see STITCH tickets in Doc 5)
4. Wire Jinja2 `{{ variable }}` and `{% %}` tags for dynamic data
5. Add Animate.css entrance animations (Animate.css in `<head>`)
6. Add Anime.js interactions (Anime.js before `</body>`)
7. Three.js + GSAP only for `templates/landing/index.html`

## Animation Decision Matrix — One Library per Task
| Animation Task | Library | Class / Code |
|---------------|---------|-------------|
| Page/card entrance | Animate.css | `animate__animated animate__fadeInUp` |
| OTP error shake | Animate.css | `animate__animated animate__shakeX` |
| Toast slide-in | Animate.css | `animate__animated animate__slideInRight` |
| Upgrade modal | Animate.css | `animate__animated animate__zoomIn` |
| Loading skeleton | Animate.css | `animate__animated animate__pulse animate__infinite` |
| Stamp counter 0→N | Anime.js | `targets: {val: old}, val: new, round: 1` |
| New stamp cell | Anime.js | `easing: 'spring(1,80,10,0)'` |
| Progress bar fill | Anime.js | `width: newPct+'%', easing: 'easeInOutQuart'` |
| Reward unlock timeline | Anime.js | `anime.timeline()` with `.add()` chain |
| 3D landing hero | Three.js r128 | `MeshToonMaterial`, lerped mouse rotation |
| Scroll card reveal | GSAP 3.12 | `scrollTrigger: { start: 'top 80%' }` |

## CDN Loading Order in base.html (MUST follow)
```html
<!-- In <head> — CSS first -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/animate.css/4.1.1/animate.min.css">

<!-- Before </body> — in this exact order -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.1/anime.min.js"></script>

<!-- Landing page only — use defer -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js" defer></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js" defer></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/ScrollTrigger.min.js" defer></script>

<!-- canvas-confetti — stamp celebration only -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/canvas-confetti/1.9.3/confetti.browser.min.js" defer></script>
```

## Colour Palette (always use these exact hex codes)
```css
--color-blue:   #1A56DB;  /* Primary CTAs */
--color-teal:   #0D9488;  /* Loyalty brand colour */
--color-orange: #FF6B35;  /* Stars, conversion CTAs */
--color-dark:   #1E2A3A;  /* Primary text */
--color-muted:  #64748B;  /* Secondary text */
--color-green:  #059669;  /* Success states */
--color-red:    #DC2626;  /* Error states */
```

## prefers-reduced-motion (accessibility — required)
```javascript
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if (prefersReducedMotion) {
    anime.speed = 100;                                          // near-instant
    gsap.globalTimeline.pause();                               // disable GSAP
    document.querySelectorAll('.animate__animated')
        .forEach(el => el.classList.remove('animate__animated')); // disable Animate.css
}
```
