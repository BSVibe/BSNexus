# G7 — Mobile Web/PWA First UX E2E Checklist

> Milestone: greenfield rebuild G7. Founder must be able to **Direct / Decide / Review** entirely from a phone-shaped viewport. PR #71 (decision-locks O2) already shipped the PWA shell + service worker; this milestone fills the UX layer.
>
> Acceptance source: `~/Docs/BSNexus/planning/greenfield/rebuild-plan.md` §G7 + `~/Docs/BSNexus/product/04-core-ux-spec.md` §Mobile UX Requirements.

## Test viewport

- iPhone 13: 390 × 844 (`devices['iPhone 13']`, chromium engine — Playwright project `iphone-13`)
- Pixel 5: 393 × 851 (Mobile Chrome — Playwright project `pixel-5`)
- Both projects use `isMobile: true` and a touch-capable user agent.

## Direct (Direction input)

- [x] Dashboard `/dashboard` shows a **Direction input card** above the fold on iPhone 13 viewport
- [x] Card has a multiline textarea with placeholder asking for the founder's directive
- [x] Submit button is at least 44 × 44 CSS pixels (WCAG 2.5.5 AAA touch target)
- [x] Empty body submit is blocked (button disabled or no API call fires)
- [x] Successful submit hits `POST /api/v1/directions` (NOT legacy `/api/v1/messages`)
- [x] On 201 response with a `request` payload, the textarea clears and a confirmation surface appears
- [ ] On 201 response with `routing` (no project assigned + multiple projects exist), a project picker appears with each option as a thumb-friendly button (≥44px)
- [ ] Picking an option from the routing prompt re-submits the direction with `target_hint = project_id`
- [x] No horizontal overflow on the page (`scrollWidth - clientWidth ≤ 2`)

## Decide (Decision Inbox)

- [x] `/dashboard` shows the existing `DecisionInboxStrip` at the top (already locked priority slot)
- [ ] Tapping a strip row navigates to `/projects/{project_id}?tab=decisions`
- [ ] DecisionsView page is fully readable on iPhone 13 with reduced padding (≤16px horizontal)
- [x] Each option button on a decision card is at least 44 × 44 CSS pixels
- [ ] The custom-resolution input + submit button stack vertically on narrow viewports (no horizontal overflow)
- [ ] Tapping an option fires `POST /api/v1/decisions/{id}/resolve` and the card transitions to the resolved state
- [ ] Resolved decisions appear in the resolved section without a page reload

## Review (Brief + Deliverable)

- [x] `/projects/{id}` Brief tab renders 5 sections in order: shipped, needs decision, blocked, running, next
- [ ] Sections collapse to single-column layout on iPhone 13 with reduced padding (≤16px horizontal)
- [ ] DeliverableCard shows: type icon, title, ProofBadge, proof_summary (truncated), verifier_type badge, relTime
- [x] Re-verify button (when `verifier_type` present) is at least 44 × 44 CSS pixels
- [ ] Copy-id button is at least 44 × 44 CSS pixels
- [x] No horizontal overflow on Brief view at 390px viewport
- [ ] Tapping `Re-verify` triggers `POST /api/v1/deliverables/{id}/verify` and invalidates queries

## PWA shell (regression — PR #71 baseline)

- [ ] `manifest.webmanifest` still served from `/manifest.webmanifest` with 200 OK
- [ ] `sw.js` registers in production builds without errors in console
- [ ] Viewport meta includes `viewport-fit=cover` (iOS notch / safe-area)
- [ ] Mobile breakpoint (≤767px) keeps all touch targets at 44px floor (`globals.css :≤767px` rule)
- [ ] Hamburger drawer + chat-rail FAB still work on iPhone 13 viewport (existing `mobile.spec.ts` and `mobile-chat-rail.spec.ts` continue to pass)

## Cross-surface founder flow

- [ ] On iPhone 13 viewport, founder can: open dashboard → submit a direction → navigate to project → see brief → resolve a decision → review a deliverable, all without horizontal scroll and without leaving mobile breakpoints
- [ ] No console errors during the full flow (errors caught with `page.on('pageerror')` / `console` event)

## Desktop regression (≥1024px)

- [ ] Existing desktop layout unchanged (3-column grid, sidebar visible, no FAB shown)
- [ ] All existing Playwright projects (`chromium`, full desktop suite) keep passing
