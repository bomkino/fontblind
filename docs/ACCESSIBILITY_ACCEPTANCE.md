# Human accessibility acceptance

This is a short listening and interaction pass for the packaged macOS application. It supplements—not replaces—the automated accessibility tests.

Record macOS version, hardware, VoiceOver version, FontBlind build SHA, date, and tester initials before starting.

## Setup

1. Build or unzip the exact release candidate.
2. Enable **Reduce Motion** in macOS accessibility settings.
3. Set browser or application zoom to 200% where available.
4. Turn VoiceOver on.
5. Do not use a pointer during the first pass.

## Workbench navigation

Expected:

- The navigation is announced as a tab list.
- Blind, Oblique Lab, and Variable Lab report selected/unselected state.
- Left/right arrow navigation moves between tabs without trapping focus.
- The skip link moves directly into the active workbench.
- The active workbench has a useful accessible name.

## Upload and processing

For each workbench:

1. Reach the dropzone by keyboard.
2. Confirm its instructions are announced.
3. Choose a valid font.
4. Confirm processing state is announced once, without repeated chatter.
5. Confirm controls that cannot be used during processing are disabled.
6. Confirm success moves focus to the result region.
7. Repeat with an invalid or unsupported input and confirm refusal copy is clear and contains no path or filename.

## Generated axes

For `slnt`, `wght`, and `wdth` results:

- Every axis has a useful label, value, minimum, and maximum.
- Keyboard changes announce the new value without excessive repetition.
- Anonymous master presets are reachable and identify their coordinates, not source names.
- Proof tiles state whether they are exact masters and can move the live controls.
- Two-axis navigation remains understandable at 200% zoom.

## Static freeze

1. Choose an interior axis position.
2. Activate **Freeze current position**.
3. Confirm busy state and exact coordinates are announced.
4. Move an axis while a freeze is running; the stale result must not appear.
5. Freeze again and confirm focus moves to the frozen result.
6. Confirm the result announces its exact coordinates.
7. Confirm each download has a descriptive name.
8. Confirm every proof row speaks “Passed” or “Failed”, not merely a decorative symbol.
9. Move an axis after success; the old frozen result must disappear and its invalidation must be announced.

## Reflow and motion

At 200% zoom and a narrow window:

- No essential control is clipped horizontally.
- Reading and focus order remain logical.
- Download cards and proof rows reflow without overlap.
- Focus indicators remain visible.
- Reduced motion removes non-essential transitions without suppressing status changes.

## Lifecycle

- Reset returns focus to a sensible point in the same workbench.
- Replacing a generated result removes stale static controls.
- Closing and reopening the app exposes no previous font or result.
- Quitting during processing does not leave a speaking or hung background application.

## Pass criteria

Pass only when all statements above are true without relying on sight or a pointer.

Record failures as exact journeys: workbench, starting control, keystrokes, spoken output, expected output, and whether the fault blocks completion. Do not convert subjective uncertainty into a green check. If this pass has not been performed, release notes must say **human VoiceOver acceptance not performed**.
