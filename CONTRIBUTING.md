# Contributing

FontBlind welcomes focused bug reports, reproducible font-format edge cases, test improvements, and small reviewable patches.

## Development

```bash
python -m pip install .
python -m pip check
node --check web/app.js
python -m unittest discover -s tests -v
```

For repeatability checks against fonts you are entitled to test:

```bash
python lab_gauntlet.py oblique /path/to/corpus --loops 3
python lab_gauntlet.py variable Regular.ttf Bold.ttf --loops 3
```

Build the native app on macOS 13 or newer with Apple command-line developer tools:

```bash
./build-fontblind-app.command --no-install
```

Do not attach proprietary font binaries to public issues. Prefer a minimal freely licensed reproducer or a programmatically generated test fixture.
