# Contributing

FontBlind welcomes focused bug reports, reproducible font-format edge cases, test improvements, and small reviewable patches.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Build the native app on macOS 13 or newer with Apple command-line developer tools:

```bash
./build-fontblind-app.command --no-install
```

Do not attach proprietary font binaries to public issues. Prefer a minimal freely licensed reproducer or a programmatically generated test fixture.
