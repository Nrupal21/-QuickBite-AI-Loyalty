---
name: QuickBite AI + Loyalty
description: A multi-tenant restaurant SaaS turning one receipt QR into an AI review draft and a loyalty stamp — no app, no password.
colors:
  brand-primary: "#1A56DB"
  brand-loyalty-teal: "#0D9488"
  brand-accent-orange: "#FF6B35"
  brand-success: "#059669"
  brand-danger: "#DC2626"
  brand-warning: "#D97706"
  brand-ink: "#1E2A3A"
  brand-muted: "#64748B"
  brand-bg: "#F8FAFC"
  nav-ground: "#0B0B0D"
  nav-teal-lift: "#2DD4BF"
  white: "#FFFFFF"
  surface-variant: "#E2E1ED"
  outline-variant: "#C3C5D7"
  accent-orange-on-tint: "#C2410C"
  warning-on-tint: "#B45309"
  loyalty-tint-wash: "#F0FDFA"
  trust-blue-hover: "#1544AD"
  loyalty-teal-hover: "#0B7E73"
  success-hover: "#047857"
typography:
  headline:
    fontFamily: "Hanken Grotesk, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontWeight: 800
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Hanken Grotesk, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontWeight: 500
  mono:
    fontFamily: "ui-monospace, SF Mono, Consolas, monospace"
    fontWeight: 400
rounded:
  sm: "0.375rem"
  md: "0.875rem"
  lg: "1.25rem"
  pill: "9999px"
spacing:
  sm: "0.75rem"
  md: "1.25rem"
  lg: "1.5rem"
  xl: "2rem"
components:
  glass-panel:
    backgroundColor: "{colors.brand-bg}"
    rounded: "{rounded.lg}"
    padding: "1.5rem"
  kpi-icon-chip:
    rounded: "{rounded.md}"
    size: "2.75rem"
    height: "2.75rem"
    width: "2.75rem"
---

# Design System: QuickBite AI + Loyalty

## Overview

**Creative North Star: "The Frosted Order Slip"**

QuickBite reads as a restaurant's own paper trail turned instant and translucent: receipt-QR, review draft, stamp card — layered sheets of near-white glass over a warm-hued kitchen light (the locked blue/teal/orange), never a slab of corporate SaaS grey. The public-facing nav already committed to this material on its dark floating pill (`static/css/nav.css`); the authenticated dashboards (`app/templates/dashboard/`) carry the same recipe at a second, lighter density, so the product reads as one glass system worn two ways — dark and ambient in the chrome that survives every page, pale and legible where staff work.

Density and restraint are load-bearing, not decorative: this is Operate surface first (staff reading real numbers under time pressure), Persuade second (the landing page's drenched-teal loyalty moment). Glass is the material of both, but Operate's glass stays quiet — translucent, not glowing; frosted, not gradient-lit.

**Confirmed anti-references:** generic AI-SaaS cream-and-gradient-text pages; corporate-enterprise grey density; uniform same-size icon-card grids as page scaffolding; colored `border-left` accent stripes on cards (an earlier draft of the dashboard used these — removed, see Do's and Don'ts).

**Key Characteristics:**
- Frosted glass panels (blur + saturate + lit top edge + soft offset shadow + a fixed specular highlight) over a low-alpha three-hue mesh, never a flat white card.
- One locked hex palette (Doc 4) carried everywhere; no page invents a new hue.
- Hanken Grotesk, one family, product and marketing alike — Operate density calls for a tighter scale than a display/body pairing would give.
- Real data or nothing: every number, chart, and gauge in the product is wired to a live endpoint; empty states teach rather than fake a placeholder metric.

## Colors

The palette is locked at the product level (`PRODUCT.md`, "Committed color, exact hexes") and expressed identically across `app/templates/dashboard/*`'s Tailwind config, `static/css/nav.css`, and `static/css/dashboard.css`. Nothing here introduces a new hue — glass is a finish applied to these colors, not a new palette.

### Primary
- **Trust Blue** (`#1A56DB`, hover `#1544AD` — the same darkening `nav.css` already uses for its own primary hover): primary actions, the active nav/sidebar state, review-count and total-reviews data, chart lines. Doc 4's "anchors trust."

### Secondary
- **Loyalty Teal** (`#0D9488`, hover `#0B7E73`): reserved for loyalty flows only — stamps, redemption, the QR-scan icon chip, the redemption-rate gauge fill, the branch-performance bar gradient's second stop. Never used outside a loyalty context anywhere in the product (Doc 4 lock).

### Tertiary
- **Conversion Orange** (`#FF6B35`): the rating/star icon chip, anything meant to read as warm and appetizing rather than operational. Doc 4's "converts."

### Neutral
- **Ink** (`#1E2A3A`): all body and data text on light surfaces.
- **Muted** (`#64748B`): labels, captions, secondary text — tinted from Ink's hue family, never plain gray (craft-floor: "tint secondary text from that hue... never gray").
- **Background** (`#F8FAFC`): the base the glass mesh sits on; the fallback flat color wherever `backdrop-filter` is unsupported or `prefers-reduced-transparency` is set.
- **Success / Danger / Warning** (`#059669`, hover `#047857` / `#DC2626` / `#D97706`): state semantics only — never decorative. Success carries the Reviews page's "Approve & post" action (a positive, confirming action on a *reply*, not a loyalty flow, so Loyalty Teal is off-limits for it under its own rule above).
- **Nav Ground** (`#0B0B0D`) with **Teal Lift** (`#2DD4BF`): the dark half of the same glass system, used only by the floating top pill and mobile dock (`static/css/nav.css`) — the one surface in the product that stays dark regardless of page.
- **White** (`#FFFFFF`): the glass system's non-transparent fallback (`@supports not (backdrop-filter)`, `prefers-reduced-transparency`, `forced-colors`), and the flat surfaces (gauge label, chart point fill, focus outline offset) that sit *on* the glass rather than *as* the glass.
- **Surface Variant** (`#E2E1ED`) / **Outline Variant** (`#C3C5D7`): pre-existing Material-derived tokens from each dashboard screen's own Tailwind config (predates this glass pass) — carried into the chart's "no data that day" empty-point styling so a missing-data marker reads as structurally neutral rather than borrowing a brand hue.
- **Accent-Orange-on-Tint** (`#C2410C`) / **Warning-on-Tint** (`#B45309`): the Accessible Tint Rule derivatives below — never used as a background, only as an icon glyph color over its own locked-hue tint.
- **Loyalty Tint Wash** (`#F0FDFA`): the palest possible tint of Loyalty Teal, the top stop of the one glass panel allowed a teal-tinted fill (`.qb-glass--tinted-teal`, the Loyalty Snapshot card) — a single named exception to Glass Panel's otherwise-neutral white fill, scoped to loyalty content only, consistent with the Loyalty-Only Teal Rule.

### Named Rules
**The Loyalty-Only Teal Rule.** `#0D9488` (and its `#2DD4BF` dark-glass lift) render loyalty state and nothing else — a stamp count, a redemption metric, the scan action. A non-loyalty feature reaching for teal is a palette violation, not a style choice.

**The Accessible Tint Rule.** An icon glyph sitting inside its own tinted chip (`.qb-kpi-icon--*` in `static/css/dashboard.css`) uses a darkened derivative of its locked hue — `#C2410C` for the Orange chip, `#B45309` for the Warning chip — never the raw brand hex, which fails icon-on-tint contrast at the chip's ~14% background opacity. The chip's *background* stays the exact locked hex at low alpha; only the *foreground glyph* darkens. This is a fixed, documented derivation (not a free color choice) so "the palette is law" stays literally true even where a flat application of it would be illegible.

## Typography

**Body/Display Font:** Hanken Grotesk (with `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`)

**Label/Mono Font:** the system monospace stack (`ui-monospace, "SF Mono", Consolas, monospace`) — reserved for the craft floor's sanctioned exception, displaying a literal code or value a person copies or types back (a masked phone number, a QR/scan token). Never a costume for "technical" elsewhere.

**Character:** One workhorse grotesque carries headings, labels, buttons, and dense tabular data — an Operate surface (per `.claude/skills/impeccable/reference/operate.md`) doesn't earn a display/body pairing; the type does its work through weight and size steps, not a second voice.

### Hierarchy
- **KPI value** (800, `1.75rem`/`28px`, tabular numerals): the one truly large number on any given panel — `.qb-kpi-value`, `.qb-gauge-value`.
- **Panel title** (700, `1.125rem`/`18px`): card and section headers.
- **Body / label** (500–600, `0.8125rem`–`0.875rem`): captions, KPI labels, table cells.
- **Micro label** (600–700, `0.6875rem`–`0.75rem`, uppercase, tracked): axis ticks, the "LAST 7 DAYS" / "REDEEMED" captions.

### Named Rules
**The Tight-Ratio Rule.** Product surfaces deliberately run a ~1.1–1.3 step ratio between adjacent sizes rather than a dramatic display scale — more type elements at lower contrast per step is correct here (`operate.md`), and this is a documented exception to any generic "flat hierarchy" heuristic run against this codebase.

## Layout

Two densities of one shell: a fixed 240px glass sidebar plus a sticky glass header on desktop (`min-width: 900px`, matching `nav.css`'s own dock/pill breakpoint), collapsing to full-width content with no sidebar below it — the floating bottom dock (`static/css/nav.css`) is the entire mobile navigation at that point, so the sidebar has nothing left to coexist with. Content sits in a `max-w-7xl` column with consistent `1.5rem`–`2rem` gutters (`0.8125rem`–`1.25rem`/`p-5` on mobile with extra bottom clearance so the fixed dock never covers the last card).

Dashboard grids are asymmetric by design, not a repeated card size: a KPI strip is one bar in divided segments (never four separate cards); the primary content row runs 2:1 (chart : status panel); the secondary bento row runs roughly 1.6:1:1.2 (snapshot : gauge : ranked list). Nothing in the product repeats the same-size icon-card grid as a whole-page scaffold.

## Elevation & Depth

Hybrid: flat color fields for structural chrome (sidebar links, table rows), true frosted glass for every panel that holds data. The glass recipe is one system at two densities:

- **Dark glass** (`static/css/nav.css`, the floating nav pill and mobile dock): `background: rgba(19,19,24,0.82)`, `backdrop-filter: blur(18–24px) saturate(160–180%)`, a lit top border (`rgba(255,255,255,0.22)`) simulating light catching the top edge of the pane.
- **Light glass** (`static/css/dashboard.css`, every dashboard panel): a top-to-bottom white gradient fill (`rgba(255,255,255,0.74)` → `rgba(255,255,255,0.5)`), `backdrop-filter: blur(24px) saturate(180%)`, the same lit-top-edge device (`rgba(255,255,255,0.95)` border-top), plus a fixed `radial-gradient` specular highlight in the panel's own top-left corner (`.qb-glass::before`) — the "liquid" cue that reads even on a screenshot, not just on hover.
- Both densities sit over a page-level mesh: three-to-four low-alpha radial gradients in the locked hues (`.qb-dash-mesh`), so the frost has real color to refract rather than reading as plain translucency over white.

### Shadow Vocabulary
- **Panel** (`0 1px 2px rgba(30,42,58,0.05), 0 20px 40px -24px rgba(30,42,58,0.28)`): every glass panel — always an offset plus a soft blur, never a flat colored halo.
- **Nav pill** (`0 4px 12px rgba(0,0,0,0.4), 0 24px 64px -20px rgba(0,0,0,0.75)`): the dark-glass equivalent, heavier for its floating-over-content role.

### Named Rules
**The No-Flat-Glass Rule.** `backdrop-filter` on this product always pairs with a mesh or content worth refracting underneath and a lit top edge — glass used over a plain solid background with no highlight is the "decoration, not a specific effect" pattern the craft floor refuses, and is a fidelity defect against this file.

## Shapes

Panel radius: `1.25rem` (`--glass-radius`). Icon chips: `0.875rem`. Pills and gauges/dots: fully round. The sidebar is the one panel with an asymmetric radius (`0 1.25rem 1.25rem 0`) since it's flush to the viewport's left edge. No hard corners anywhere in the glass system; the dark nav pill uses a larger `24px` radius befitting its floating, detached-from-the-viewport-edge role.

## Components

### Glass Panel (signature)
- **Corner Style:** `1.25rem` (`0` on the flush edge of the sidebar/sticky header variants).
- **Background:** the light-glass gradient fill described in Elevation & Depth.
- **Shadow Strategy:** the Panel shadow above.
- **Border:** `1px solid rgba(30,42,58,0.08)`, top edge overridden to `rgba(255,255,255,0.95)`.
- **Fallback:** solid `#ffffff`, no blur, under `prefers-reduced-transparency`, `forced-colors`, or `@supports not (backdrop-filter)`.

### KPI Strip
- **Style:** one Glass Panel in a CSS grid, `N` equal columns, `1px` hairline dividers between segments (`rgba(30,42,58,0.08)`) — never separate cards, never a colored border-left accent.
- **Icon chip:** locked hue at ~12–14% background alpha, glyph in the Accessible Tint Rule's darkened derivative.
- **Value:** the KPI-value type step, `font-variant-numeric: tabular-nums`.
- **Loading:** a shimmering skeleton block (`.qb-skel`) in place of the value — never a spinner over live content.

### Radial Gauge
- **Style:** a two-circle SVG (track + fill), track at 14% loyalty-teal alpha, fill at full teal, `stroke-linecap: round`, animated via `stroke-dashoffset` on real data change (600ms, `cubic-bezier(0.16,1,0.3,1)`).
- **Rule:** the arc length is always derived from a real percentage in the API response — this component never ships as a decorative ring with no bound value.

### Ranked Bar List (Branch Performance)
- **Style:** name + proportional bar (gradient blue→teal) + count, bar width driven by `transform: scaleX()` (never `width`, to stay off the compositor-thrash path) with `transform-origin: left`.

### Chart (Sentiment Trend)
- **Style:** hand-rolled SVG area + line, no charting dependency (matches the project's existing zero-dependency convention). Smoothed with per-segment cubic Béziers.
- **Rule:** a day with no underlying data breaks the line into a new segment and renders as a neutral, mid-baseline hollow point — it is never interpolated or dragged to the scale's floor, which would misread as a sentiment crash instead of an absence of data.

### Navigation
- **Style:** see `static/css/nav.css`'s own header comment — a floating dark-glass pill (desktop, ≥900px) and a magnifying bottom dock (mobile, <900px), shared by every **public** page (landing, auth, profile). The authenticated dashboards (`app/templates/dashboard/*`) do **not** include this bar — a signed-in staff/owner surface showing a guest "Sign in / Get started" CTA is a real defect, not a shared-chrome convenience. The dashboards are a fully self-contained shell instead (below).

### Dashboard Shell (signature)
- **Desktop (≥900px):** the fixed light-glass sidebar (`.qb-glass--sidebar`), full viewport height, `top-0` — there is no public pill above it to clear space for.
- **Mobile (<900px):** a dedicated `.qb-mobile-tabbar` — a full-width light-glass bar fixed to the viewport bottom, three items (Dashboard, Loyalty Analytics, Account), replacing the public dock. Active route: `aria-current="page"`, same tint as an open Account trigger (`aria-expanded="true"`) — both read as "this is where you are."
- **Account identity:** one `#account-sheet` glass panel (avatar initial, role, Sign Out), triggered by `[data-account-trigger]` — the sidebar's bottom chip on desktop, the tab bar's Account tab on mobile. Desktop anchors it as a popover near the chip; mobile renders it as a bottom sheet above the tab bar with a dimming scrim (`.qb-account-scrim`, mobile-only — a full-page scrim over a small desktop popover is heavier than the affordance needs). One shared JS module, `static/js/dashboard-shell.js`, drives both anchors identically.

## Do's and Don'ts

### Do:
- **Do** keep every glass panel's `backdrop-filter` paired with the page mesh and the panel's own specular highlight (The No-Flat-Glass Rule).
- **Do** derive gauges, sparkline-equivalents, and proportional bars from a real API value; label an empty state honestly when there is none yet.
- **Do** darken a locked hue for on-tint icon contrast per The Accessible Tint Rule rather than reaching for an unlocked color.
- **Do** animate bar/fill changes via `transform`, never `width`/`height`/`padding` (layout-thrash).

### Don't:
- **Don't** include `partials/nav.html` (the public marketing nav) on an authenticated dashboard screen — the dashboard shell owns its own sidebar, mobile tab bar, and account identity.
- **Don't** use a colored `border-left`/`border-right` accent above 1px on any card, row, or panel — use an icon chip or a segment divider instead.
- **Don't** lay out a whole surface as a grid of same-size icon+heading+text cards. Vary panel size and composition (KPI strip as one bar, asymmetric bento rows).
- **Don't** use `#0D9488`/`#2DD4BF` (Loyalty Teal) for anything outside a loyalty metric or action.
- **Don't** add a kicker or eyebrow label above any heading.
- **Don't** interpolate a data series across a real gap (a day/period with no data) — break the line and mark the point as empty instead of implying a value that was never measured.
