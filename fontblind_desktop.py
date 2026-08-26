"""Linux browser-hosted desktop lifecycle without weakening the local boundary."""
from __future__ import annotations

import fcntl
import os
import shutil
import stat
import subprocess
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit


_MAX_STATE_BYTES = 256


class DesktopLifecycleError(RuntimeError):
    """The browser-hosted desktop lifecycle could not be established safely."""


def _owned_regular(metadata: os.stat_result) -> bool:
    getter = getattr(os, "getuid", None)
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and (not callable(getter) or int(metadata.st_uid) == int(getter()))
    )


def _runtime_root() -> Path:
    base_text = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if base_text:
        base = Path(base_text)
    else:
        getter = getattr(os, "getuid", None)
        uid = int(getter()) if callable(getter) else 0
        base = Path(tempfile.gettempdir()) / f"fontblind-runtime-{uid}"

    root = base / "fontblind" if base_text else base
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = root.lstat()
    except OSError as exc:
        raise DesktopLifecycleError("FontBlind could not create its private desktop runtime directory") from exc
    getter = getattr(os, "getuid", None)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (callable(getter) and int(metadata.st_uid) != int(getter()))
    ):
        raise DesktopLifecycleError("FontBlind found an unsafe desktop runtime directory")
    try:
        root.chmod(0o700)
    except OSError as exc:
        raise DesktopLifecycleError("FontBlind could not secure its desktop runtime directory") from exc
    return root


def _valid_loopback_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (ValueError, UnicodeError):
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= int(port) <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"http://127.0.0.1:{int(port)}"


class BrowserAppLease:
    """Own one browser-hosted FontBlind process and its anonymous reconnect URL."""

    def __init__(self, root: Path, descriptor: int | None, owned: bool) -> None:
        self.root = Path(root)
        self._descriptor = descriptor
        self.owned = bool(owned)
        self.state_path = self.root / "desktop.url"

    @classmethod
    def acquire(cls) -> "BrowserAppLease":
        root = _runtime_root()
        lock_path = root / "desktop.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise DesktopLifecycleError("FontBlind could not open its private desktop lock") from exc
        if not _owned_regular(metadata):
            os.close(descriptor)
            raise DesktopLifecycleError("FontBlind found an unsafe desktop lock")
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return cls(root, None, False)
        except OSError as exc:
            os.close(descriptor)
            raise DesktopLifecycleError("FontBlind could not claim its desktop lock") from exc
        return cls(root, descriptor, True)

    def read_existing_url(self) -> str | None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self.state_path, flags)
            metadata = os.fstat(descriptor)
            if not _owned_regular(metadata) or metadata.st_size <= 0 or metadata.st_size > _MAX_STATE_BYTES:
                return None
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = None
                payload = stream.read(_MAX_STATE_BYTES + 1)
            if len(payload) > _MAX_STATE_BYTES:
                return None
            return _valid_loopback_url(payload.decode("ascii", errors="strict"))
        except (OSError, UnicodeError):
            return None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def publish(self, url: str) -> None:
        if not self.owned:
            raise DesktopLifecycleError("A non-owner process cannot publish the desktop URL")
        canonical = _valid_loopback_url(url)
        if canonical is None:
            raise DesktopLifecycleError("FontBlind refused a non-loopback desktop URL")
        temporary: Path | None = None
        try:
            descriptor, temp_text = tempfile.mkstemp(prefix=".desktop-url-", dir=self.root)
            temporary = Path(temp_text)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write((canonical + "\n").encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            temporary = None
        except OSError as exc:
            raise DesktopLifecycleError("FontBlind could not publish its private desktop URL") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if self.owned:
            self.state_path.unlink(missing_ok=True)
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __enter__(self) -> "BrowserAppLease":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()


def open_desktop_url(url: str) -> bool:
    """Open one reviewed loopback URL through the user's desktop browser."""
    canonical = _valid_loopback_url(url)
    if canonical is None:
        return False
    try:
        if webbrowser.open_new_tab(canonical):
            return True
    except Exception:
        pass
    candidates = (("xdg-open", canonical), ("gio", "open", canonical))
    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            return True
        except OSError:
            continue
    return False
