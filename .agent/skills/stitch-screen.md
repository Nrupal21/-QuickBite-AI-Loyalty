---
name: stitch-screen
description: How to generate a Stitch.ai screen for QuickBite and wire it to FastAPI with Jinja2
tags: [stitch.ai, frontend, jinja2, tailwind, screens]
---

## When to Use This Skill
- Any STITCH-01 to STITCH-12 ticket
- Generating a new customer or dashboard screen
- Wiring a generated screen to a FastAPI endpoint

## The 5-Step Process

### Step 1: Write the Stitch.ai Prompt
Include these in every prompt:
- QuickBite teal `#0D9488` as the primary brand colour for loyalty screens
- QuickBite blue `#1A56DB` as the primary CTA colour for dashboard
- Inter font for all text
- The screen's specific purpose and key components
- Mobile-first for customer screens, desktop for dashboard

**Example prompt for STITCH-05 (Loyalty Card):**
```
"Digital loyalty card screen. Teal-to-green gradient card (rounded-2xl, border-2, border teal #0D9488).
Large stamp count at top in Inter 48px bold ('4 / 10 Stamps').
2-row by 5-column grid of stamp cells: filled cells are solid teal #0D9488 circles with a star icon inside,
empty cells are grey outline circles. Reward progress bar below the grid (h-3, teal fill).
Reward name text below the bar ('Free Coffee after 10 stamps').
Restaurant branding at the bottom. Mobile-first (375px viewport)."
```

### Step 2: Generate and Review
- Generate HTML + Tailwind CSS in Stitch.ai
- Check colours match the Doc 4 palette (11 colours with exact hex codes)
- Check typography matches Doc 4 (Inter weights, sizes)
- Check layout is mobile-first (customer screens) or desktop-first (dashboard)

### Step 3: Export to Correct Path
```
Customer screens:  app/templates/customer/{screen_name}.html
Dashboard screens: app/templates/dashboard/{screen_name}.html
Billing screens:   app/templates/billing/{screen_name}.html
Admin screens:     app/templates/admin/{screen_name}.html
Landing page:      app/templates/landing/index.html
```

### Step 4: Wire Jinja2 Tags
Replace static placeholder data with Jinja2 template variables:
```html
<!-- Static Stitch.ai output -->
<h1>Marco's Pizza</h1>
<p>4 / 10 Stamps</p>

<!-- After Jinja2 wiring -->
<h1>{{ restaurant.name }}</h1>
<p>{{ customer.stamp_count }} / {{ reward_program.stamps_required }} Stamps</p>
```

For lists:
```html
<!-- Stamp grid with Jinja2 loop -->
{% for i in range(reward_program.stamps_required) %}
  {% if i < customer.stamp_count %}
    <div class="stamp-cell filled bg-[#0D9488] rounded-full" id="stamp-cell-{{ i }}">⭐</div>
  {% else %}
    <div class="stamp-cell empty border-2 border-gray-200 rounded-full" id="stamp-cell-{{ i }}"></div>
  {% endif %}
{% endfor %}
```

For conditionals:
```html
{% if reward_unlocked %}
  <div id="reward-card" class="bg-[#0D9488] text-white rounded-2xl p-6">
    <p class="text-2xl font-bold">🎁 Reward Unlocked!</p>
    <p class="font-mono text-3xl tracking-widest">{{ redemption_code }}</p>
  </div>
{% endif %}
```

### Step 5: Wire to FastAPI Route
In the FastAPI route, render the Jinja2 template:
```python
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

@router.get("/loyalty/card", response_class=HTMLResponse)
async def loyalty_card_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer),
) -> HTMLResponse:
    # Get data for template
    loyalty_data = await loyalty_service.get_card_data(current_customer.id)
    return templates.TemplateResponse("customer/loyalty_card.html", {
        "request": request,
        "customer": current_customer,
        "reward_program": loyalty_data.reward_program,
        "stamp_count": loyalty_data.stamp_count,
        "reward_unlocked": loyalty_data.reward_unlocked,
        "redemption_code": loyalty_data.redemption_code,
        "progress_percent": loyalty_data.progress_percent,
    })
```

## Important IDs to Always Include (for JS wiring)
Every generated screen should have these IDs for animations and JS hooks:

**OTP screens:**
- `#otp-group` — container for the 6 OTP boxes (shake target)
- `#otp-error` — error message div (hidden by default)
- `#resend-timer` — resend countdown text
- `#otp-box-0` through `#otp-box-5` — individual input boxes

**Loyalty card:**
- `#stamp-count` — the "4/10" text (Anime.js counter target)
- `#stamp-cell-N` — each stamp cell (spring animation target)
- `#progress-bar` — the reward progress bar (width animation target)
- `#reward-card` — the reward unlock card (shown/hidden conditionally)
- `#confetti-trigger` — empty div triggers canvas-confetti

**Dashboard:**
- `#sentiment-chart` — Chart.js chart container
- `#new-events-badge` — WebSocket notification badge
- `.stat-value` — all stat card numbers (data-value attribute = target number)
