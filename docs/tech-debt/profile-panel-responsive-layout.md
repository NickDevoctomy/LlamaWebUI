# Technical debt: responsive Profiles panel layout

**Status:** Deferred  
**Observed:** 2026-09-23  
**Scope:** Frontend Profiles workspace; desktop/tablet and mobile widths

## Evidence

User-provided screenshots of the Profiles page show layout problems at multiple viewport widths:

- The profile rows keep their action buttons in a wide horizontal strip. At narrower desktop/tablet width, actions near the right edge are clipped; the Export action is only partly visible and later actions extend outside the viewport.
- At phone width, profile header actions wrap within already narrow buttons, the profile-row action strip is wider than the viewport, and actions on the right are inaccessible without horizontal overflow.
- Long model paths are ellipsized, which is acceptable only if the full value remains available by a discoverable method such as a title or copy affordance.
- The fixed bottom navigation remains visible in the mobile screenshot; profile content and action rows need sufficient bottom clearance and must not be obscured by it.

These are observations from the supplied screenshots, not a claim that all desktop widths or profile workflows have been manually tested.

## User impact

Profile management is difficult or impossible on narrow viewports: users may not see or reach Export, Command, Clone, or Delete actions. Header buttons also become harder to scan and tap when their labels break across lines. This is a responsive usability issue, not a data-integrity issue.

## Deferred direction

Revisit when responsive-layout debt is scheduled. Keep the current profile workflows and API behavior intact; do not use this item to expand the active sequential implementation plan.

Potential layout approaches to evaluate then:

- Reflow profile actions into a second, wrapping row or a compact overflow menu at narrow widths.
- Give the profile identity/path area a min-width of zero and a deliberate truncation/copy/title treatment.
- Allow header actions to wrap without compressing their labels into narrow columns.
- Add responsive spacing so the fixed mobile navigation does not cover the last profile row or dialog actions.

## Completion criteria

- At a normal desktop viewport, every profile action is visible and operable without horizontal page scrolling.
- At `390x844`, no profile control is clipped beyond the viewport; actions remain reachable and have usable tap targets.
- No horizontal overflow is introduced by long aliases, executable paths, model paths, or action labels.
- The fixed bottom navigation does not obscure profile content, menus, or dialog actions.
- Verify create, import command, edit, validate, export, command view, clone, reset, and delete flows at desktop and mobile sizes without creating tokens, downloads, or enabling a profile unintentionally.
