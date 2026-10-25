---
name: campus-lost-and-found-design
description: Design rules for the Campus Lost & Found Django templates. Use when creating or editing any template, partial or stylesheet in lostfound/templates or lostfound/static.
---

# Campus Lost & Found: design skill

Follow these rules for every screen. "Must" rules are non-negotiable; "should" rules are strong defaults.
Format adapted from the open DESIGN.md / SKILL.md convention (bergside/awesome-design-skills); tokens are this project's own.

## Brand and tone
- Product: a trustworthy campus service that brings belongings home and recognises honest finders.
- Voice: warm, plain and calm. Short sentences, specific labels ("Report a found item", not "Submit").
- Must never shame someone for losing an item, or imply points are needed to use the service.
- Must never show private identifying details, verification answers or reporter contact details on public screens.

## Tokens (lostfound/static/lostfound/css/app.css, community.css)
Use the CSS variables, never raw hex, in templates.

| Role | Token | Value |
|---|---|---|
| Structure / dark surfaces | `--navy` | #14213D |
| Primary action, links | `--indigo` | #4F46E5 |
| Accent, "found" | `--amber` | #F5B335 (fills only; text on it must be `--navy`) |
| Page background | `--bg` / `--bg-2` | #F5F7FB / #EEF1F7 |
| Body text / secondary text | `--text` / `--muted` | #0F172A / #556274 |
| Success, returned | `--ok` | #15803D |
| Lost, urgent, errors | `--bad` | #C62828 |
| Pending | `--amber-ink` on `--amber-wash` | #8A5300 on #FFF7E6 |
| Metallic tiers | `.metal-{bronze,silver,gold,platinum,indigo,green}` | `-2`/`-3` stops carry white text; `-1` is shine only |

- Font: Inter (400–900). Headings 700–800 with tight letter-spacing; body 15.5px/1.6.
- Spacing should use 4 / 8 / 12 / 16 / 24 / 32 px (0.25–2rem) steps; larger section gaps use 2.75rem.
- Radius: `--radius-s` 8px (inputs, buttons), `--radius-m` 12px (cards), `--radius-l` 18px (feature cards).
- Shadows: `--shadow-s` resting, `--shadow-m` hover, `--shadow-l` floating panels only.

## Components (reuse before creating)
- Shell: `base.html` + `partials/sidebar.html`, `topbar.html`, `site_header.html`, `site_footer.html`, `messages.html`.
- Items: `partials/item_card.html` (grid/list), `partials/item_row.html` (compact). Both take a redacted `PublicItem`, never a model.
- Status: `{% badge value label %}` → `partials/status_badge.html`. Tiers/badges: `partials/points_badge.html`.
- Forms: `{% field form.x %}` → `partials/field.html` (label, help, error linked by id).
- Icons: `{% icon "name" %}` from `partials/icons.html`. Never use emoji as interface icons.
- Layout helpers: `.page-head`, `.panel`, `.split`, `.grid-2/3/4`, `.figures`, `.track`, `.timeline`, `.empty`.

## Interaction states (must)
Every interactive element must have: default, hover, `:focus-visible` (visible ring), active/pressed, disabled.
Async actions must show loading (`aria-busy="true"` on the submit button; app.js does this for forms).
Errors must appear next to the field (`.field.has-error`) and in a summary notice for long forms.

## Accessibility (WCAG 2.2 AA, must)
- Text contrast ≥ 4.5:1 (≥ 3:1 for 24px+ or 19px bold). Check new colour pairs before using them.
- `--amber` must not be used for text on white; white text must not sit on `-1` metal stops.
- Keyboard-first: every action reachable by Tab, menus close with Esc, focus is never hidden.
- Every input has a `<label>`; decorative icons are `aria-hidden`; meaningful images have alt text.
- Respect `prefers-reduced-motion`; motion is limited to small hover lifts and fades.

## Content edge cases (must)
- Long labels and emails wrap (`overflow-wrap: anywhere` is set globally for headings, cells and notices).
- Every list has an empty state with an icon, one sentence and a useful next action.
- Pages must not scroll horizontally at 375px wide.

## Do / Don't
- Do use real database counts; label sample data. Don't invent statistics.
- Do keep one primary action per area. Don't put two filled primary buttons side by side unless they're the lost/found pair.
- Do give every button a working URL or form. Don't ship buttons that do nothing.
- Do reuse partials and tokens. Don't add one-off inline colours.

## Quality gates (check before committing UI changes)
1. `python manage.py test` passes (includes privacy-leak and role-access tests).
2. No new colour pair below 4.5:1 for body text.
3. Page works at 375px and 1440px with no horizontal scroll.
4. Keyboard: Tab through the page; every control shows a focus ring and works with Enter/Space.
5. Empty, error and loading states exist for any new list or form.
