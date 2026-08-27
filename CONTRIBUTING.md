# Contributing

FontBlind welcomes focused bug reports, reproducible font-format edge cases, test improvements, and small reviewable patches.

Read [`docs/maintenance/REPOSITORY_STATE.md`](docs/maintenance/REPOSITORY_STATE.md) before changing platform or release claims.

## Development gate

```bash
python -m pip install .
python -m pip check
python -m compileall -q .
for file in web/*.js; do node --check "$file"; done
node --test tests/*.test.cjs
python -m unittest discover -s tests -v
```

For repeatability checks against fonts you are entitled to test:

```bash
python lab_gauntlet.py oblique /path/to/corpus --loops 3
python lab_gauntlet.py variable Regular.ttf Bold.ttf --loops 3
```

Build the native package on macOS 13+ with Apple command-line developer tools:

```bash
./build-fontblind-app.command --no-install
```

Build the experimental Garuda package only against its documented target and acceptance boundary. Do not generalise a passing Arch-container job into generic Linux support.

## Repository rules

- Preserve FontBlind’s transformation/packaging boundary; do not add Font Previewer’s Study or typography-decision model.
- Add tests at a public contract or package seam.
- Never weaken a refusal, privacy check, checksum, or release gate merely to make CI green.
- Do not attach proprietary, paid, client, system, or mystery font binaries to Git or public issues. Use a pinned open-licensed reproducer or generated fixture.
- Keep branches short-lived and follow [`docs/maintenance/BRANCH_POLICY.md`](docs/maintenance/BRANCH_POLICY.md).
