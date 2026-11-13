"""One JVM per process, with crforge's installed jars on its class path."""

from __future__ import annotations

from pathlib import Path
import threading

try:  # imported lazily so `--help` works without the optional dependency
    import jpype
except ImportError as error:  # pragma: no cover - environment dependent
    jpype = None
    _IMPORT_ERROR = error
else:
    _IMPORT_ERROR = None

DEFAULT_INSTALL = (
    Path(__file__).resolve().parents[1] / ".resource" / "crforge" / "env" / "build" / "install" / "env"
)
DEFAULT_TABLES = Path.home() / ".crforge" / "tables" / "16.635.8"

_lock = threading.Lock()
_started_with: Path | None = None


class JvmError(RuntimeError):
    """The JVM could not start, or was started with another distribution."""


def jars(install: str | Path) -> list[str]:
    folder = Path(install).expanduser().resolve()
    files = sorted((folder / "lib").glob("*.jar"))
    if not files:
        raise JvmError(f"no crforge jars under {folder / 'lib'}; run `./gradlew :env:installDist` first")
    return [str(path) for path in files]


def start(install: str | Path = DEFAULT_INSTALL, *, jvm_args: tuple[str, ...] = ()) -> None:
    """Start the JVM once with ``install``'s jars; a later call must name the same folder."""

    global _started_with
    if jpype is None:
        raise JvmError(
            "jpype is not installed; run `pip install jpype1` (and `./gradlew :env:installDist`)"
        ) from _IMPORT_ERROR
    folder = Path(install).expanduser().resolve()
    with _lock:
        if jpype.isJVMStarted():
            if _started_with is None:
                raise JvmError("the JVM was started outside crforge_bridge: its jars are unknown")
            if _started_with != folder:
                raise JvmError(
                    f"the JVM runs the jars of {_started_with}; it cannot be restarted with {folder}"
                )
            return
        jpype.startJVM(
            *jvm_args,
            "-Djava.awt.headless=true",
            classpath=jars(folder),
            convertStrings=False,
        )
        _started_with = folder


def java_class(name: str):
    if jpype is None or not jpype.isJVMStarted():
        raise JvmError("the JVM is not started: call crforge_bridge.jvm.start() first")
    return jpype.JClass(name)
