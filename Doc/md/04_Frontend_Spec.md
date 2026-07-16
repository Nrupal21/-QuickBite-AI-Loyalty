# QuickBite AI + Loyalty
## Frontend Specification Document · v3.0

> **Document 4 of 6** · Confidential · v3.0 adds Anime.js 3.2.1 + Animate.css 4.1.1 to the animation stack

---

## 1. Stitch.ai — Primary Frontend Platform

Stitch.ai is the AI-powered frontend development platform. Describe a UI screen in plain English → Stitch.ai generates clean HTML + Tailwind CSS. All UI starts here, then gets wired to FastAPI via Jinja2 and animated using the 4-library animation stack.

> 💡 Stitch.ai generates **structure and layout**. Anime.js and Animate.css are added manually after export. Three.js and GSAP are added separately for the landing page only.

### The 5-Step Workflow

1. **Write prompt** — describe the screen. Include colour hex codes, component purpose, and key interactions.
2. **Generate** — Stitch.ai produces HTML + Tailwind. Review against this document.
3. **Refine** — iterate with follow-up prompts until the component matches spec.
4. **Export** — copy HTML into the appropriate `app/templates/` subfolder.
5. **Animate** — add Animate.css entrance classes for page loads. Add Anime.js timelines for complex interactions. Three.js and GSAP for landing page only.

### Stitch.ai Screen Map

| Screen | Prompt Summary | Backend Endpoint | Animation Layer |
|--------|---------------|-----------------|-----------------|
| Customer — OTP Login | Mobile OTP login. Phone pill input (+91 flag). 6-box OTP entry. Teal `#0D9488`. Resend timer. | `POST /auth/customer/otp-request` | Animate.css: `slideInUp` on mount. Anime.js: shake on wrong code. |
| Customer — OTP Verify | 6 individual 48×56px boxes. Teal focus ring. Auto-submit on 6th digit. | `POST /auth/customer/otp-verify` | Anime.js: success stagger pulse. Animate.css: `shakeX` on error. |
| Customer — Registration | Single card. Pre-filled phone. Name + email + WhatsApp toggle. Orange CTA. | `POST /api/v1/customers/register` | Animate.css: `fadeInUp` on card mount. |
| Customer — Review Composer | Full-screen mobile. 5 stars (60px orange). Tag chips. AI draft in Georgia serif. | `POST /api/v1/reviews/generate` | Anime.js: star fill cascade. Animate.css: `fadeIn` on draft card. |
| Customer — Loyalty Card | Teal gradient card. `4/10 Stamps` counter Inter 48px. 10-cell stamp grid. Reward progress bar. | `GET /api/v1/loyalty/card/{id}` | Anime.js: counter increment, progress bar fill. Animate.css: `slideInUp` on entrance. |
| Customer — Stamp Collected | Full-screen teal. Animated stamp icon. Updated count. Reward card if threshold reached. | (Client-side) | Anime.js: full celebration timeline. canvas-confetti burst. Animate.css: `bounceIn` on reward badge. |
| Customer — Scratch Card | Grey overlay. Swipe to reveal. Monospace 6-char redemption code. | `GET /api/v1/loyalty/scratch-card/{id}` | Canvas API: scratch mechanic. Anime.js: sparkle on reveal. |
| Owner — Dashboard | SaaS sidebar. 4 stat cards. Chart. Review list + approve/reject. WS badge. | `GET /api/v1/dashboard/stats  WS /dashboard/stream` | Animate.css: `slideInRight` on WS toasts. Anime.js: stat count-up. |
| Owner — Loyalty Analytics | Daily scan heatmap. Top 10 customers. Branch comparison. Fraud log. | `GET /api/v1/loyalty/analytics` | Anime.js: counter animations on stat numbers. |
| Owner — Branch + QR Setup | Branch form: name, address, geofence radius slider. QR preview. Download. | `POST /api/v1/branches` | Animate.css: `fadeIn` on QR preview card. |
| Billing / Upgrade | 3-column pricing. Feature checklist. Highlighted Pro card. CTAs. | `POST /api/v1/billing/checkout/{plan_id}` | Animate.css: `pulse` on Pro badge. `zoomIn` on upgrade modal. |
| Landing Page Hero | Full-viewport. Three.js 3D model (centre). Headline. Orange CTA. GSAP scroll triggers. | (Static + Three.js) | **Three.js:** 3D hero. **GSAP:** scroll narrative. Anime.js / Animate.css: not used here. |

---

## 2. Colour Palette

> Always reference colours by hex code in Stitch.ai prompts. Example: *"Use `#0D9488` as the primary CTA colour for all loyalty screens."*

| Swatch | Name | Hex Code | Usage | Example Contexts |
|--------|------|----------|-------|-----------------|
| 🔵 | Primary Blue | `#1A56DB` | CTAs, active states, links, headers | Login button, dashboard nav, approval badges |
| 🟢 | Loyalty Teal | `#0D9488` | All loyalty flows — stamps, rewards, OTP | OTP input ring, stamp card, 'Stamp Collected' screen |
| 🟠 | Accent Orange | `#FF6B35` | Highlights, star ratings, conversion CTAs | Star rating widget, 'Post to Google', plan cards |
| ⬛ | Dark Ink | `#1E2A3A` | Primary body text, headings | Page titles, review body text |
| 🩶 | Muted Slate | `#64748B` | Secondary text, placeholders, timestamps | Meta info, helper text, dates |
| 🔷 | Light Blue Tint | `#EFF6FF` | Page backgrounds, card fills, tag chips | Dashboard card backgrounds |
| 🟩 | Success Green | `#059669` | Confirmations, approved states | 'Approved' badge, 5-star indicator |
| 🔴 | Danger Red | `#DC2626` | Errors, lockout warnings | Login error, OTP error state |
| 🟡 | Warning Amber | `#D97706` | Pending states, 80% usage warnings | 'Pending approval' badge |
| 🟣 | Purple | `#7C3AED` | Premium tier, Pro plan | Pro plan card accent |
| ⬜ | White | `#FFFFFF` | Modals, cards, nav bar, form inputs | All card and modal backgrounds |

---

## 3. Typography

| Element | Font | Size / Weight | Usage |
|---------|------|---------------|-------|
| Page Title (H1) | Inter | 40px / 800 | Major screen titles |
| Section Heading (H2) | Inter | 28px / 700 | Card headers, section dividers |
| Sub-heading (H3) | Inter | 20px / 600 | Sub-section labels |
| Body Text | Inter | 16px / 400 | Paragraphs, descriptions |
| Small / Meta | Inter | 13px / 400 | Timestamps, secondary labels |
| Button Text | Inter | 15px / 600 | All CTA buttons |
| Monospace / Code | JetBrains Mono | 14px / 400 | Trace IDs, redemption codes |
| AI Review Draft | Georgia (serif) | 17px / 400 | AI-generated review — serif for authenticity feel |
| ★ OTP Digit Input | Inter | 24px / 700 | Large digit in each OTP box. Centred, bold, teal. |
| ★ Stamp Counter | Inter | 48px / 800 | Large stamp count: '4/10'. Anime.js animates the increment. |
| ★ Reward Unlock Heading | Inter | 30px / 700 | 'Reward Unlocked! 🎁' — prominent, teal |
| ★ Redemption Code | JetBrains Mono | 28px / 700 | 6-character code. Easy to read aloud to staff. |

---

## 4. Component Styles

### Buttons
```
Primary (Blue):    bg-[#1A56DB] text-white px-6 py-3 rounded-xl font-semibold hover:bg-[#1648C0] transition-all
Loyalty (Teal):    bg-[#0D9488] text-white px-6 py-3 rounded-xl font-semibold hover:bg-[#0B7A70]
Secondary:         border-2 border-[#1A56DB] text-[#1A56DB] px-6 py-3 rounded-xl hover:bg-[#EFF6FF]
Success:           bg-[#059669] text-white px-5 py-3 rounded-xl — Approve actions only
Danger:            bg-[#DC2626] text-white px-5 py-3 rounded-xl — Destructive actions ONLY
Ghost:             text-[#64748B] px-4 py-2 rounded-lg hover:bg-gray-100
Loading:           opacity-60 cursor-wait + Animate.css: animate__animated animate__pulse animate__infinite
```

### Form Inputs
```
Text Input:   border border-gray-300 rounded-xl px-4 py-3 focus:ring-2 focus:ring-[#1A56DB]
Error state:  border-red-500 + Animate.css: animate__animated animate__shakeX
Success:      border-green-500 + Animate.css: animate__animated animate__bounceIn on checkmark icon
Disabled:     bg-gray-100 text-gray-400 cursor-not-allowed opacity-60
```

### ★ OTP Authentication Components
```
Phone Input:       rounded-full px-4 py-3 flex items-center gap-2 (flag + code + number in one pill)

OTP Box (default): w-12 h-14 text-2xl font-bold text-center border-2 border-gray-200 rounded-xl
                   focus:border-[#0D9488] focus:ring-2 focus:ring-[#0D9488]/20
                   inputmode='numeric' maxlength='1'

OTP Box (filled):  border-[#0D9488] bg-[#F0FDF4] text-[#0D9488]

OTP Box (error):   border-red-500
                   + Animate.css: animate__animated animate__shakeX on the container div
                   Remove class in animationend listener to allow re-trigger.

OTP success:       Anime.js stagger — all 6 boxes scale 1.0→1.1→1.0, 50ms between each

Resend Timer:      text-sm text-[#64748B] — shows '⏱ Resend in 01:47'
                   Anime.js animates colour from grey (#64748B) → teal (#0D9488) when timer hits 0
```

### ★ Loyalty Components
```
Stamp Grid:       grid grid-cols-5 gap-2
                  Filled: bg-[#0D9488] rounded-full 48×48px
                  Empty:  border-2 border-gray-200 rounded-full
                  New stamp: Anime.js spring — scale(0) → scale(1.2) → scale(1.0), 400ms

Loyalty Card:     bg-gradient-to-br from-[#F0FDF4] to-[#ECFDF5] rounded-2xl border-2 border-[#0D9488]
                  Animate.css: animate__animated animate__slideInUp on card entrance

Progress Bar:     h-3 bg-[#0D9488] rounded-full
                  Anime.js: width animates from old% to new%, 700ms easeInOutQuart

Reward Unlock:    bg-[#0D9488] text-white rounded-2xl p-6
                  Anime.js 3-step timeline: overlay fade → text slide up → code glow pulse

Scratch Card:     Canvas API with globalCompositeOperation='destination-out'
                  Reveal at 70% scratched. Anime.js: sparkle particles on full reveal.

Stamp Collected:  canvas-confetti burst (200 particles, #0D9488 + #FF6B35)
                  Anime.js: stamp counter increments from old → new value
```

### Cards & Modals
```
Review Card:   bg-white rounded-2xl shadow-sm border border-gray-100 p-5
Stat Card:     bg-white rounded-2xl p-6 with 4px solid left-border accent
               Anime.js: number count-up from 0 to final value on dashboard load

Upgrade Modal: fixed inset-0 bg-black/40 backdrop-blur-sm
               Panel: bg-white rounded-2xl max-w-md p-6
               Animate.css: animate__animated animate__zoomIn animate__faster on panel

Toast (success): fixed top-4 right-4 bg-[#059669] text-white px-5 py-3 rounded-xl
                 Animate.css: animate__animated animate__slideInRight
                 Anime.js: opacity fade-out after 3s, then remove from DOM

Toast (error):   same but bg-[#DC2626]
                 Animate.css: animate__animated animate__slideInRight animate__faster
```

---

## 5. Spacing & Layout Rules

| Rule | Value |
|------|-------|
| Base unit | 4px (Tailwind spacing scale). Never use arbitrary values. |
| Page max-width | 1280px centered. Horizontal padding: 24px mobile / 48px desktop. |
| Card inner padding | 24px (p-6). Smaller cards use 16px (p-4). |
| Section gaps | 32px (gap-8) between major sections. 16px (gap-4) between related cards. |
| Mobile breakpoints | sm: 640px / md: 768px / lg: 1024px / xl: 1280px |
| OTP input screen | Full-width on mobile. OTP box group centred. Max 320px on desktop. |
| Review composer | Full-width on mobile — no sidebars. One action step at a time. |
| Loyalty card | Centred vertical layout. Stamp grid pinned to viewport centre on mobile. |
| Registration card | max-w-md mx-auto. Fields stack vertically with gap-y-4. |
| Dashboard grid | 12-column. Stats row = 4 equal cards. Review list = 8/12 + 4/12 sidebar. |
| Touch targets | Minimum 44×44px. OTP boxes: 48×56px. |

---

## 6. API & Integration Specification

| Service | Endpoints | Data In → Data Out |
|---------|-----------|---------------------|
| OpenAI GPT-4o | `POST /v1/chat/completions` | System prompt + context + tags → Natural review draft |
| Google Gemini | `POST /v1/models/gemini-1.5-pro:generateContent` | Same as OpenAI — Redis feature flag fallback |
| ★ Twilio SMS (OTP) | `POST /Accounts/{SID}/Messages.json` | To: E.164 phone + Body: 'Your QuickBite code: {otp}' → SID + delivery status |
| ★ SendGrid (Email OTP) | `POST /v3/mail/send` | To: email + Subject: OTP + 6-digit code → Message ID |
| Google OAuth 2.0 | `GET /o/oauth2/auth, POST /token` | Auth code + PKCE verifier → Access token + Refresh token |
| Google My Business | `GET .../reviews, POST .../reply` | Page cursor → Reviews list. Approved response → Posted reply. |
| Twilio WhatsApp | `POST /Messages.json (whatsapp: prefix)` | Reward template → WhatsApp delivery status. Pro+ only. |
| Stripe Subscriptions | `POST /v1/checkout/sessions` | Plan price ID → Checkout URL |
| Stripe Webhooks | `POST /webhooks/stripe (received)` | `invoice.payment_failed`, `subscription.updated` → DB plan status updates |
| Sentry | Auto SDK in FastAPI | Unhandled exceptions → Error event with context |
| Cloudflare R2 | S3-compatible PUT/GET | QR code PNGs, exports, logos → Signed public URLs |

---

## 7. Animation & 3D Stack ★ v3.0 — 4-Library Architecture

QuickBite uses **four animation libraries**, each with a clearly defined role. Never substitute one for another.

### 7.1 The Four Libraries

| Library | Version | Role | Used On | NOT Used On |
|---------|---------|------|---------|-------------|
| **Three.js** | r128 | 3D WebGL hero | Landing page ONLY | Customer screens, dashboard |
| **GSAP + ScrollTrigger** | 3.12 | Scroll-driven narrative | Landing page ONLY | Customer screens, dashboard |
| **Anime.js** | 3.2.1 ★ NEW | Complex JS animation timelines | ALL customer + dashboard screens | Landing page |
| **Animate.css** | 4.1.1 ★ NEW | Utility CSS class animations | ALL screens — entrances + feedback | (used everywhere) |

### 7.2 Decision Matrix — Which Library for Which Animation

| Animation Task | Three.js | GSAP | Anime.js | Animate.css |
|----------------|:--------:|:----:|:--------:|:-----------:|
| 3D model on landing page hero | ✅ | ❌ | ❌ | ❌ |
| Scroll-driven section reveal (landing) | ❌ | ✅ | ❌ | ❌ |
| Parallax background elements (landing) | ❌ | ✅ | ❌ | ❌ |
| Page / card entrance animation | ❌ | ❌ | ❌ | ✅ `fadeInUp` |
| Error shake on OTP input | ❌ | ❌ | ❌ | ✅ `shakeX` |
| Toast notification slide-in | ❌ | ❌ | ❌ | ✅ `slideInRight` |
| Success checkmark bounce | ❌ | ❌ | ❌ | ✅ `bounceIn` |
| Loading spinner / pulse | ❌ | ❌ | ❌ | ✅ `pulse infinite` |
| Upgrade modal entrance | ❌ | ❌ | ❌ | ✅ `zoomIn` |
| OTP box success stagger | ❌ | ❌ | ✅ stagger | ❌ |
| Star rating fill cascade | ❌ | ❌ | ✅ stagger | ❌ |
| Stamp counter 0 → N increment | ❌ | ❌ | ✅ timeline | ❌ |
| Loyalty progress bar width fill | ❌ | ❌ | ✅ easeInOutQuart | ❌ |
| Stamp cell scale-up on new stamp | ❌ | ❌ | ✅ spring easing | ❌ |
| Reward unlock sequence (multi-step) | ❌ | ❌ | ✅ timeline | ❌ |
| Scratch card sparkle on full reveal | ❌ | ❌ | ✅ + canvas | ❌ |
| Dashboard stat number count-up | ❌ | ❌ | ✅ timeline | ❌ |
| Resend timer countdown colour | ❌ | ❌ | ✅ color prop | ❌ |
| canvas-confetti stamp celebration | — | — | (separate lib) | — |

### 7.3 CDN Loading — Correct Order in `base.html`

```html
<!-- In <head> — CSS first, available before any content renders -->
<link rel="preload" as="style"
  href="https://cdnjs.cloudflare.com/ajax/libs/animate.css/4.1.1/animate.min.css">
<link rel="stylesheet"
  href="https://cdnjs.cloudflare.com/ajax/libs/animate.css/4.1.1/animate.min.css">

<!-- Before </body> — JS in dependency order -->

<!-- Anime.js — for all customer + dashboard screens -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.1/anime.min.js"></script>

<!-- Landing page only — deferred, not loaded on every page -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js" defer></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js" defer></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/ScrollTrigger.min.js" defer></script>

<!-- canvas-confetti — stamp celebration only -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/canvas-confetti/1.9.3/confetti.browser.min.js" defer></script>

<!-- Your app code last -->
<script src="/static/js/app.js"></script>
```

> ⚠️ **Rule:** Animate.css loads in `<head>` so it is available before the first render. Anime.js loads before `</body>` and before `app.js`. Three.js and GSAP use `defer` — they are only needed on the landing page.

### 7.4 Animate.css — Usage Guide

| Use Case | Classes to Add | When to Apply |
|----------|---------------|---------------|
| Page / card entrance | `animate__animated animate__fadeInUp` | Add to container div on DOMContentLoaded |
| Loyalty card entrance | `animate__animated animate__slideInUp` | Applied to loyalty card wrapper on stamp collected screen |
| OTP input error | `animate__animated animate__shakeX` | Add via JS on 401 response. Remove in `animationend` listener. |
| Toast notification | `animate__animated animate__slideInRight animate__faster` | Added to toast div on creation |
| Success checkmark | `animate__animated animate__bounceIn` | On green checkmark icon appearing |
| Upgrade modal | `animate__animated animate__zoomIn animate__faster` | On modal panel when shown |
| Loading state | `animate__animated animate__pulse animate__infinite` | On skeleton loaders. Remove when data arrives. |
| Custom duration | `style="--animate-duration: 0.4s"` | Inline CSS variable override |

```javascript
// Re-triggering an animation (e.g. wrong OTP again after first error)
const otpGroup = document.getElementById('otp-group');

function triggerOTPError() {
  // Remove first so animation can fire again
  otpGroup.classList.remove('animate__animated', 'animate__shakeX');
  // Force reflow — browser must see the removal
  void otpGroup.offsetWidth;
  // Re-add
  otpGroup.classList.add('animate__animated', 'animate__shakeX');
}
```

### 7.5 Anime.js — Key Code Patterns

```javascript
// ── Stamp counter increment ────────────────────────────────────────────
anime({
  targets: { val: oldCount },
  val: newCount,
  round: 1,
  duration: 600,
  easing: 'easeOutQuart',
  update: function(anim) {
    document.querySelector('#stamp-count').innerHTML =
      Math.round(anim.animations[0].currentValue) + '/10';
  }
});

// ── New stamp cell — spring bounce ────────────────────────────────────
anime({
  targets: '#stamp-cell-' + stampIndex,
  scale: [0, 1.2, 1.0],
  opacity: [0, 1],
  duration: 500,
  easing: 'spring(1, 80, 10, 0)',
  delay: stampIndex * 50   // stagger per cell
});

// ── Reward unlock — multi-step timeline ──────────────────────────────
const tl = anime.timeline({ easing: 'easeOutExpo' });
tl
  .add({ targets: '#reward-overlay', opacity: [0, 1], duration: 400 })
  .add({ targets: '#reward-text', translateY: [30, 0], opacity: [0, 1], duration: 500 }, '-=100')
  .add({ targets: '#redemption-code', scale: [0.8, 1], opacity: [0, 1], duration: 400 }, '-=200')
  .add({ targets: '#code-glow',
         boxShadow: ['0 0 0 rgba(13,148,136,0)', '0 0 24px rgba(13,148,136,0.6)'],
         duration: 600, direction: 'alternate', loop: 3 });

// ── Star rating fill cascade ──────────────────────────────────────────
function fillStars(rating) {
  anime({
    targets: '.star-icon',
    color: (el, i) => i < rating ? '#FF6B35' : '#E5E7EB',
    scale: (el, i) => i < rating ? [1, 1.3, 1] : 1,
    duration: 300,
    delay: anime.stagger(60),   // 60ms per star
    easing: 'easeOutBack'
  });
}

// ── Loyalty progress bar fill ─────────────────────────────────────────
anime({
  targets: '#progress-bar',
  width: newPercent + '%',
  duration: 700,
  easing: 'easeInOutQuart'
});

// ── Toast auto-dismiss after 3s ───────────────────────────────────────
anime({
  targets: '#toast',
  opacity: [1, 0],
  translateX: [0, 20],
  delay: 3000,
  duration: 400,
  easing: 'easeInQuart',
  complete: () => document.getElementById('toast').remove()
});
```

### 7.6 Three.js — Landing Page Hero

```javascript
// Initialise — landing page only, loaded deferred
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(75, w / h, 0.1, 100);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });

// Cel-shading (cartoon / Ghibli aesthetic)
const material = new THREE.MeshToonMaterial({ gradientMap: toonGradientTexture });

// Lerped mouse tracking — smooth, non-jarring
let targetX = 0;
document.addEventListener('mousemove', e => {
  targetX = e.clientX / window.innerWidth;
});

function animate() {
  requestAnimationFrame(animate);
  // Lerp — gradually moves toward target (0.05 = 5% per frame)
  mesh.rotation.y += (targetX * 0.5 - mesh.rotation.y) * 0.05;
  renderer.render(scene, camera);
}
animate();

// IntersectionObserver — only render when hero is visible
const observer = new IntersectionObserver(entries => {
  entries[0].isIntersecting ? animate() : cancelAnimationFrame(animId);
});
observer.observe(document.getElementById('hero-canvas'));
```

### 7.7 GSAP + ScrollTrigger — Landing Page Scroll Narrative

```javascript
// Scroll-driven card reveal — landing page only
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
    toggleActions: 'play none none reverse'
  }
});

// Section pin — section sticks while content animates in
ScrollTrigger.create({
  trigger: '.value-prop-section',
  start: 'top top',
  end: '+=500',
  pin: true,
  scrub: 1
});
```

### 7.8 Accessibility — prefers-reduced-motion

```javascript
// Check once at page load — applies to all 4 libraries
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (prefersReducedMotion) {
  // Three.js — lock model, no rotation
  targetX = 0;

  // GSAP — pause all scroll animations
  gsap.globalTimeline.pause();

  // Anime.js — near-instant (no animation visible)
  anime.speed = 100;

  // Animate.css — remove all animation classes
  document.querySelectorAll('.animate__animated')
    .forEach(el => el.classList.remove('animate__animated'));
}
```

---

*Document 4 of 6 · QuickBite AI + Loyalty · Frontend Specification v3.0 · Confidential*
