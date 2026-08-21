---
name: anime-animation
description: Anime.js animation patterns for QuickBite loyalty, OTP, and dashboard screens
tags: [anime.js, animation, frontend, loyalty, otp]
---

## When to Use This Skill
- ANIM-01: OTP screen animations (error shake, success stagger)
- ANIM-02: Review composer animations (star cascade, tag chip)
- ANIM-03: Loyalty card + stamp celebration animations
- ANIM-04: Dashboard stat count-up and toast dismiss
- Any Anime.js implementation question

## Core Patterns

### 1. Stamp Counter Increment (ANIM-03)
```javascript
// Animates the stamp count from old value to new value (not instant jump)
function animateStampCount(oldCount, newCount) {
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
}
```

### 2. New Stamp Cell Spring Animation (ANIM-03)
```javascript
// Stamp cell enters with spring physics bounce
function animateNewStamp(stampIndex) {
    anime({
        targets: `#stamp-cell-${stampIndex}`,
        scale: [0, 1.2, 1.0],
        opacity: [0, 1],
        duration: 500,
        easing: 'spring(1, 80, 10, 0)',  // mass, stiffness, damping, velocity
        delay: stampIndex * 50            // stagger: 50ms between each cell
    });
}
```

### 3. Reward Unlock 3-Step Timeline (ANIM-03)
```javascript
// Multi-step animation: overlay → text → code glow
const rewardTimeline = anime.timeline({ easing: 'easeOutExpo' });
rewardTimeline
    .add({
        targets: '#reward-overlay',
        opacity: [0, 1],
        duration: 400
    })
    .add({
        targets: '#reward-text',
        translateY: [30, 0],
        opacity: [0, 1],
        duration: 500
    }, '-=100')  // starts 100ms before previous ends
    .add({
        targets: '#redemption-code',
        scale: [0.8, 1],
        opacity: [0, 1],
        duration: 400
    }, '-=200')
    .add({
        targets: '#code-glow',
        boxShadow: [
            '0 0 0 rgba(13,148,136,0)',
            '0 0 24px rgba(13,148,136,0.6)'
        ],
        duration: 600,
        direction: 'alternate',
        loop: 3
    });
```

### 4. Star Rating Cascade (ANIM-02)
```javascript
// Fill stars left-to-right with stagger
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
```

### 5. Dashboard Stat Count-Up (ANIM-04)
```javascript
// All 4 stat numbers count up from 0 on page load
document.querySelectorAll('.stat-value').forEach((el, i) => {
    const target = parseInt(el.dataset.value);
    anime({
        targets: { val: 0 },
        val: target,
        round: 1,
        duration: 800,
        delay: i * 100,             // 100ms stagger between cards
        easing: 'easeOutQuart',
        update: a => { el.textContent = Math.round(a.animations[0].currentValue); }
    });
});
```

### 6. Toast Auto-Dismiss (ANIM-04)
```javascript
// Toast slides in via Animate.css, then Anime.js fades it out after 3s
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type} animate__animated animate__slideInRight`;
    toast.textContent = message;
    document.body.appendChild(toast);

    // Auto-dismiss after 3 seconds
    anime({
        targets: toast,
        opacity: [1, 0],
        translateX: [0, 20],
        delay: 3000,
        duration: 400,
        easing: 'easeInQuart',
        complete: () => toast.remove()
    });
}
```

### 7. OTP Success Stagger (ANIM-01)
```javascript
// All 6 OTP boxes scale up and down in sequence on success
function animateOTPSuccess() {
    anime({
        targets: '.otp-box',
        scale: [1.0, 1.2, 1.0],
        duration: 300,
        delay: anime.stagger(50),   // 50ms between each box
        easing: 'easeOutBack',
        complete: () => {
            // Redirect to loyalty card after animation
            window.location.href = '/loyalty';
        }
    });
}
```

## Re-triggerable Animate.css Shake (for OTP errors)
```javascript
// Must remove class → force reflow → re-add class to re-trigger
function triggerOTPError(containerEl) {
    containerEl.classList.remove('animate__animated', 'animate__shakeX');
    void containerEl.offsetWidth;  // forces browser reflow
    containerEl.classList.add('animate__animated', 'animate__shakeX');
}
// Called on wrong OTP submission:
triggerOTPError(document.getElementById('otp-group'));
```

## Steps for Adding a New Animation
1. Identify which library (use ANIM-06 decision matrix in `.agent/rules/04-frontend.md`)
2. Confirm CDN is loaded in `base.html` in correct order
3. Write the Anime.js function in `app/static/js/app.js`
4. Wire to the relevant DOM event (API response, button click, DOMContentLoaded)
5. Add `prefers-reduced-motion` check at page load (in `app.js` initialisation)
