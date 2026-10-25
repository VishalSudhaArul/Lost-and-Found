# Campus Lost & Found: design notes

Companion to [SKILL.md](SKILL.md), which holds the rules for people and AI tools. This file explains why.

## Direction
A campus service that has to feel **trustworthy** (security staff handle people's belongings and identity checks) and **warm** (someone has just lost something important). Navy gives structure and authority, indigo marks the main action, and amber is the "found" and "kindness" accent used in the luggage-tag cards, the found journey and community rewards.

## Why these choices
- **One accent per meaning.** Red only for lost/urgent, amber for pending and "found", green for returned, so people can scan status without reading.
- **Luggage-tag cards.** The punched hole and reference code make every item feel like a real tagged object at the security desk, and the reference is what staff and owners quote to each other.
- **Metallic tiers.** Bronze → Kindness Ambassador echoes familiar reward ladders. The light metal shades are kept for the medal shine only, because white text on them failed contrast (as low as 1.4:1). Tiles now use the deeper stops (≥ 4.6:1).
- **Two shells.** Visitors see a public site (header and footer); signed-in people get the app shell (sidebar and top bar). Students, teachers and staff each have their own sign-in.

## Audit against the shared design-skill rulebook (October 2026)
Rules adopted from the DESIGN.md / SKILL.md convention: WCAG 2.2 AA contrast, explicit interaction states, edge-case handling and testable quality gates.

| Check | Before | After |
|---|---|---|
| `--muted` text on `--bg-2` | 4.21:1 (fail) | 5.48:1 |
| "No photo" placeholder text | 2.78:1 (fail) | uses `--muted` |
| Form placeholder text | 2.64:1 (fail) | 4.53:1 |
| White text on gold / silver / platinum tier tiles | 1.4–2.9:1 (fail) | 4.6–5.9:1 |
| White text on promo banner | 2.1–2.8:1 (fail) | 5.2:1+ |
| Pressed state on chips, tabs, cards | missing | added |
| Visible focus ring on link cards | box-shadow only | 3px outline |
| Long emails/references in cards and tables | could overflow | wrap |

Styles from the collection that were considered and not used: their palettes (e.g. Impeccable's amber #CC8800 and Professional's #FECE14) fail contrast on white, and their fonts (Chakra Petch, Noto Serif Display, Poppins) don't match a calm campus service as well as Inter.

## Maintenance
- Add colours as tokens in `app.css` (`:root`), then check contrast before use.
- New components belong in `templates/partials/` with a one-line comment saying what data they accept.
- Keep `community.css` for rewards and homepage storytelling. Everything else goes in `app.css`.
