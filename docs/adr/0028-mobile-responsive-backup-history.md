# ADR 0028: Mobile-Responsive Backup History View

**Status:** Proposed
**Date:** 2026-04-22
**Author:** Leonardo Merza

## Context

### Background

The instance dashboard renders the backup history as a 7-column table (checkbox, Date, Trigger, Label, Size, Status, Actions). On narrow viewports the table's intrinsic width exceeds its container — the columns visually escape the dark "Backup History" card section, breaking the page chrome and forcing horizontal scroll on the body. Mobile users see a broken-looking page even though the underlying data is fine.

The dashboard is otherwise responsive: the header stat cards already collapse via `grid-cols-1 sm:grid-cols-2 lg:grid-cols-4` (instance_dashboard.html:28). The backup history table is the only major block that has no mobile treatment.

### Current State

- Table lives in `backup/templates/backup/instance_dashboard.html` lines 47–118, wrapped in `_card_section_start.html` with `body_class="p-0"` and an inner `<div class="overflow-visible">`.
- Per-row UI: a "…" button opens `.dropdown-menu` containing Label, Notes, Pin/Unpin, Download, View Diff, Restore, Delete.
- Bulk-action bar (`#bulk-bar`) is fixed to the bottom of the viewport and operates on every checked `.backup-checkbox`. JS lives in `backup/static/backup/js/app.js` lines 274–314 (`toggleSelectAll`, `updateBulkBar`, `getSelectedIds`).
- No JS framework — vanilla DOM queries and a single `.backup-checkbox` class are the contract between template and bulk handlers.
- ADR 0008 established the Tailwind + template-component pattern; ADR 0017 introduced the bulk bar; ADR 0012 introduced the actions dropdown. All three intersect here.

### Requirements

- The Backup History block must not overflow its card container on viewports down to ~360 px wide.
- All per-row actions available on desktop (Label, Notes, Pin/Unpin, Download, View Diff, Restore, Delete) must remain reachable on mobile.
- Bulk selection (checkbox + fixed bottom bar) must keep working on mobile, including "select all".
- Desktop (≥ md, 768 px) layout must remain visually unchanged.
- Action menu items must be defined in one place — duplicating the action list across two layouts is the failure mode this ADR is preventing.

### Constraints

- No new JS framework. Existing handlers in `app.js` are vanilla and selector-based.
- Tailwind v4 with `@apply` component classes (input.css). New shared styles should land there if reused.
- Single `.backup-checkbox` selector is the bulk-bar contract; changing it ripples through `app.js` and any future bulk endpoints (ADR 0017).
- Project convention is template includes for repeated patterns (ADR 0008, CLAUDE.md "Reusable components" rule).

## Options Considered

### Option 1: Horizontal scroll wrapper

**Description:** Wrap the existing `<table>` in `<div class="overflow-x-auto">`. The dark card stays intact; users swipe horizontally to see hidden columns.

**Pros:**
- One-line fix.
- Zero template/JS changes.
- Single source of truth — no duplicate row markup.

**Cons:**
- Bad mobile UX — actions column ends up off-screen, dropdown menu can clip outside viewport.
- The fixed bottom bulk bar overlaps a horizontally scrolling area, which is awkward.
- Doesn't address the actual user complaint (they explicitly asked for cards).

### Option 2: Hide columns at narrow widths

**Description:** Add `hidden md:table-cell` to less-important columns (Trigger, Size, Status, maybe Label), keeping the same `<table>` element with fewer visible columns on mobile.

**Pros:**
- Single DOM, single source of truth.
- Bulk-checkbox JS keeps working unchanged.
- Small CSS-only change.

**Cons:**
- Hides information users genuinely want (Status especially — knowing if a backup failed is the point of the screen).
- Still a table — narrow cells with stacked content don't read well on phones.
- Doesn't deliver the card-per-backup UX the user asked for.

### Option 3: Stacked-table CSS (single DOM, restyle rows as cards on mobile)

**Description:** Keep one `<table>`. At `< md`, override `tr/td` to `display: block` and use `::before { content: attr(data-label) }` to inject column labels. Each row visually becomes a card.

**Pros:**
- Single source of truth — no duplicate markup.
- Bulk JS works unchanged (`.backup-checkbox` still has one node per backup).
- Smaller diff than a full second template.

**Cons:**
- `data-label` attributes on every `<td>` — verbose template churn.
- CSS-only stacking constrains card layout — hard to put trigger badge inline with date, hard to put actions in a corner. The user wants a real card, not a row pretending to be one.
- Feels clever but tends to fight the developer when iterating on the design later.

### Option 4 (chosen): Dual-render with shared actions partial — table on `md+`, cards on mobile

**Description:** Render the existing table inside `<div class="hidden md:block">`. Render a new card list (`<div class="md:hidden space-y-2">…</div>`) that loops the same `backups` queryset and emits a `_backup_card.html` per record. Both layouts include a single shared `_backup_actions.html` partial that contains the Label / Notes / Pin / Download / Diff / Restore / Delete menu items, so the action list is defined once. Both layouts use the `.backup-checkbox` class on their checkboxes; `app.js` is taught to dedupe by `value` and to mirror state across both copies so resizing the viewport mid-selection stays consistent.

**Pros:**
- Card layout is purpose-built for mobile (visual hierarchy, badge placement, tap targets) instead of a table-row dressed up.
- Desktop table is unchanged — no risk of regression for the primary view.
- Action items live in one partial — adding a new action in the future updates both layouts at once. Closes the "duplicate action list" failure mode.
- Bulk-action contract preserved: same `.backup-checkbox` class, same `value=backup.pk`. The dedupe-by-value tweak is ~10 lines in `app.js`.
- Aligns with the project's existing "responsive via Tailwind utilities + template includes" pattern (ADR 0008).

**Cons:**
- Two render paths for backup rows means the page emits each backup twice in HTML (display:none on one). For the `[:50]` cap currently used, that's negligible payload.
- Slight JS complexity to keep the two copies' checkboxes in sync (required because both exist in DOM even though only one is visible at a time).
- One extra component file to maintain.

## Decision

**Chosen: Option 4 — dual-render with shared `_backup_actions.html` partial.**

**Rationale:**

- The user's complaint is visual (overflow) and the user's proposed fix is structural (cards). Options 1–3 paper over the visual problem without delivering a real mobile design; only Option 4 actually changes the layout to match the device.
- The "two layouts duplicate the action list" risk — the only real downside of dual-render — is fully mitigated by extracting `_backup_actions.html`. Once that partial exists, future ADRs that add actions touch one file, not two.
- Bulk selection is the highest-stakes interaction on this page (ADR 0017). A dedupe-by-value tweak in `getSelectedIds`/`toggleSelectAll` is a localized, testable change to `app.js`. The wire format and API contract don't move.
- Desktop is unchanged. The existing table is visually correct on `md+` and is the layout most users see most of the time.
- The 50-backup cap on the queryset bounds the duplicate-render cost — there is no scenario where this page renders thousands of rows.

## Acceptance Criteria

- [ ] **AC-1**: Given a viewport width of 375 px, when the user opens an instance dashboard with at least one backup, then the Backup History card has no horizontal overflow and no `<table>` element is visible inside it.
- [ ] **AC-2**: Given a viewport width of 1280 px, when the user opens an instance dashboard with at least one backup, then the existing 7-column `<table>` is visible and the mobile card list is not.
- [ ] **AC-3**: Given a mobile viewport, each backup card displays date (linked to detail page), trigger badge, label (with pin/notes icons), size, status badge, and an actions menu button.
- [ ] **AC-4**: Given a mobile viewport, when the user opens a card's actions menu, then the same items present on desktop (Label, Notes, Pin/Unpin, Download + Diff + Restore for successful backups, Delete) are available and invoke the same JS handlers.
- [ ] **AC-5**: Given a mobile viewport, when the user toggles a card's checkbox, then the bulk-action bar appears with the correct count, and Pin / Unpin / Download / Delete buttons act on the selected backup IDs.
- [ ] **AC-6**: Given any viewport, when the user clicks the header "select all" checkbox, then every backup is selected exactly once (no double-counting from the dual-rendered checkboxes).
- [ ] **AC-7**: Given the user has selected backups on mobile and resizes the browser to ≥ 768 px without reloading, then the same backups remain selected and the bulk bar count is unchanged.
- [ ] **AC-8**: The action menu items (Label / Notes / Pin/Unpin / Download / Diff / Restore / Delete) are defined in exactly one template file; `instance_dashboard.html` contains zero literal copies of those `<button>`/`<a>` elements outside the shared include.

## Consequences

### Positive

- Mobile users see a layout designed for their viewport instead of a broken table.
- Desktop layout is untouched — zero regression risk for the primary use case.
- Action list lives in one partial — the project gains a reusable `_backup_actions.html` for any future mobile/desktop split (e.g., a future "single backup" mobile card on the detail page).
- The dedupe-by-value tweak in `app.js` makes the bulk-bar code more robust against any future template that renders the same backup twice (e.g., a "pinned at top + chronological list" view).

### Negative

- Each backup is emitted twice in HTML on every dashboard load. Bounded by the 50-row cap — payload increase is small but real.
- Two layouts to keep visually coherent over time. Mitigated by the shared actions partial; not mitigated for the row-data fields themselves (date, trigger, label, size, status). A future field added to one layout could be forgotten in the other.
- Slightly more JS state to reason about (two checkbox elements per backup; mirror their `checked` state on change).

### Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Future contributor adds a new action only to one layout | Medium | Medium | Extract `_backup_actions.html` and document in the file's first-line comment that it is the single source of truth. Linked from CLAUDE.md "Reusable components" guidance. |
| Bulk-bar double-counts after dual render | High if untreated | High | Dedupe-by-value in `getSelectedIds`. Add unit-style DOM test or a manual case in the test plan asserting that selecting a card selects exactly one ID. |
| Mobile dropdown clipped by `overflow` on container | Medium | Low | Card actions use the same `.dropdown-menu` (already absolutely positioned) and the card container does not set `overflow: hidden`. Verify in the dev container at 375 px. |
| Dropdown right-anchor (`right-0`) clips on left side of small screens | Low | Low | Existing `.dropdown-menu` is already `right-0`; cards place the actions button on the right edge so the menu opens inward. |

## Implementation Plan

- [ ] Extract `backup/templates/backup/components/_backup_actions.html` containing the dropdown-menu inner items (Label, Notes, Pin/Unpin, Download, View Diff, Restore, Delete) parameterized by `backup` and `config`.
- [ ] Replace the inline action items in `instance_dashboard.html` desktop row with `{% include "backup/components/_backup_actions.html" with backup=backup config=config %}`.
- [ ] Wrap the existing `<table>` block in `<div class="hidden md:block">` (no inner changes).
- [ ] Add `<div class="md:hidden space-y-2 p-2">` block that loops `backups` and emits `backup/components/_backup_card.html` per record. Card layout: top row = date link + status badge; second row = trigger badge + size + label with pin/notes icons; bottom row = changes summary (if any) + "…" actions button using the shared `_backup_actions.html`.
- [ ] Empty state: card-list block shows a centered "No backups yet." message when `backups` is empty (mirror the existing `{% empty %}` branch).
- [ ] Mobile select-all: render an inline "Select all" checkbox + label at the top of the card-list block (above the first card) using `id="select-all-mobile"` so `toggleSelectAll` can be wired the same way; or unify both select-all checkboxes via a shared handler that toggles every `.backup-checkbox`.
- [ ] `app.js`: update `getSelectedIds` to dedupe by `value` (use a `Set`); update `toggleSelectAll` to set `checked` on all `.backup-checkbox` (already does); add a `change` listener (or rely on the existing `onchange="updateBulkBar()"`) that mirrors `cb.checked` to all other checkboxes with the same `value` so the desktop and mobile copies stay in sync.
- [ ] Add a card-targeted style class (or use raw Tailwind) for the card container — match the dark-mode/border conventions used by `_stat_card.html` (`border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 rounded-lg p-3`).
- [ ] Tests in `backup/tests/`: add a template-render test asserting both the table and the card list are present in the dashboard HTML, that each backup ID appears in exactly two `.backup-checkbox` elements, and that the action partial is included in both.
- [ ] Manual verification in the dev container at 375 px / 768 px / 1280 px:
  - Card layout renders, no overflow.
  - Action menu opens and each item triggers the right handler.
  - Select-all selects every backup exactly once (bulk bar shows correct count).
  - Resize from 375 → 1280 mid-selection: selection persists.
- [ ] User verifies end-to-end at production (`dca up -d --build flowhistory`); only then bump status to **Implemented** per the ADR workflow in CLAUDE.md.

## Related ADRs

- [ADR 0008](./0008-tailwind-css-and-template-components.md) — Tailwind + template-component pattern this ADR extends to a new partial.
- [ADR 0012](./0012-dashboard-actions-dropdown.md) — dropdown menu being shared across layouts here.
- [ADR 0017](./0017-bulk-actions.md) — `.backup-checkbox` contract and `#bulk-bar` behavior must keep working.
- [ADR 0019](./0019-tooltip-component.md) — prior precedent for extracting a reusable component file.

## References

- Tailwind responsive utilities: https://tailwindcss.com/docs/responsive-design
- Existing template: `backup/templates/backup/instance_dashboard.html`
- Bulk action JS: `backup/static/backup/js/app.js` (lines 274–314)
