"""Stdlib-only install preflight for Forex Research Lab.

Used by INSTALL.bat (Windows) and ``python -m forex_lab.install_check``.
Does not import pip packages — safe to run before ``pip install -r requirements.txt``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

MIN_PYTHON = (3, 11)
PYTHON_DOWNLOAD_URL = "https://www.python.org/downloads/"

CHECK_PYTHON = "Python 3.11+"
CHECK_PIP = "pip"
CHECK_VENV = "venv module"
CHECK_WRITE = "Write access"
CHECK_NETWORK = "Network (pip)"
CHECK_REQUIREMENTS = "requirements.txt"
CHECK_STREAMLIT = "streamlit import"
CHECK_FOREX_LAB = "forex_lab import"

_PY_LAUNCHER_VERSIONS = ("3.14", "3.13", "3.12", "3.11", "3")
_WHICH_NAMES = (
    "python3.14",
    "python3.13",
    "python3.12",
    "python3.11",
    "python3",
    "python",
)
_WIN_VERSION_TAGS = ("314", "313", "312", "311")

Runner = Callable[..., Any]
WhichFn = Callable[[str], str | None]
ExistsFn = Callable[[Path], bool]


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def python_install_hint() -> str:
    return (
        f"Install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer from "
        f"{PYTHON_DOWNLOAD_URL} — on Windows tick \"Add python.exe to PATH\" "
        "and leave pip checked, then re-run INSTALL.bat."
    )


@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str
    required: bool = True
    hint: str = ""

    @property
    def status(self) -> str:
        return "OK" if self.ok else "MISSING"


@dataclass
class InterpreterProbe:
    executable: str | None
    version_info: tuple[int, int, int] | None = None
    has_pip: bool = False
    has_venv: bool = False
    pip_detail: str = ""
    venv_detail: str = ""
    error: str = ""

    @property
    def version_ok(self) -> bool:
        if self.version_info is None:
            return False
        return self.version_info[:2] >= MIN_PYTHON


@dataclass
class InstallReport:
    items: list[CheckItem] = field(default_factory=list)
    root: Path | None = None

    def add(self, item: CheckItem) -> CheckItem:
        self.items.append(item)
        return item

    def required_missing(self) -> list[CheckItem]:
        return [i for i in self.items if i.required and not i.ok]

    @property
    def ok(self) -> bool:
        return not self.required_missing()


def format_checklist(report: InstallReport, *, title: str = "Install checklist") -> str:
    lines = [title]
    if report.root is not None:
        lines.append(f"Root: {report.root}")
    lines.append("")
    width = max((len(i.name) for i in report.items), default=8)
    for item in report.items:
        tag = f"[{item.status}]"
        extra = f"  {item.detail}" if item.detail else ""
        opt = " (optional)" if not item.required else ""
        lines.append(f"  {tag:<9} {item.name:<{width}}{extra}{opt}")
        if not item.ok and item.hint:
            lines.append(f"            -> {item.hint}")
    lines.append("")
    missing = report.required_missing()
    if missing:
        names = ", ".join(i.name for i in missing)
        lines.append(f"Result: {len(missing)} required item(s) MISSING ({names}).")
        lines.append("Fix the MISSING rows above, then re-run INSTALL.bat.")
    else:
        optional_miss = [i for i in report.items if not i.required and not i.ok]
        if optional_miss:
            names = ", ".join(i.name for i in optional_miss)
            lines.append(f"Result: required checks OK. Optional MISSING: {names}.")
        else:
            lines.append("Result: all checks OK.")
    return "\n".join(lines)


def missing_python_items(*, detail: str = "not found on PATH") -> list[CheckItem]:
    hint = python_install_hint()
    return [
        CheckItem(CHECK_PYTHON, False, detail, hint=hint),
        CheckItem(CHECK_PIP, False, "needs Python", hint=hint),
        CheckItem(CHECK_VENV, False, "needs Python", hint=hint),
    ]


def _win_python_candidates(env: Mapping[str, str]) -> list[Path]:
    local = env.get("LOCALAPPDATA", "")
    pf = env.get("ProgramFiles", "")
    pf86 = env.get("ProgramFiles(x86)", "")
    out: list[Path] = []
    for tag in _WIN_VERSION_TAGS:
        for base in (local, pf, pf86):
            if not base:
                continue
            if base == local:
                out.append(Path(base) / "Programs" / "Python" / f"Python{tag}" / "python.exe")
            else:
                out.append(Path(base) / f"Python{tag}" / "python.exe")
    return out


def discover_python(
    *,
    which: WhichFn | None = None,
    path_exists: ExistsFn | None = None,
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
) -> str | None:
    """Return a Python executable path, or None if nothing looks usable."""
    which = which or shutil.which
    path_exists = path_exists or (lambda p: Path(p).exists())
    env = env if env is not None else os.environ
    run = runner or subprocess.run

    override = (env.get("FORX_PYTHON") or "").strip()
    if override and path_exists(Path(override)):
        return override

    def _exe_from(cmd: Sequence[str]) -> str | None:
        try:
            proc = run(
                list(cmd) + ["-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if getattr(proc, "returncode", 1) != 0:
            return None
        text = (getattr(proc, "stdout", None) or "").strip()
        if text and path_exists(Path(text)):
            return text
        return None

    if which("py"):
        for ver in _PY_LAUNCHER_VERSIONS:
            found = _exe_from(["py", f"-{ver}"])
            if found:
                return found

    for name in _WHICH_NAMES:
        found = which(name)
        if not found:
            continue
        resolved = _exe_from([found]) or found
        if resolved:
            return resolved

    for candidate in _win_python_candidates(env):
        if path_exists(candidate):
            return str(candidate)
    return None


_PROBE_SCRIPT = (
    "import sys\n"
    "print('version', f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')\n"
    "try:\n"
    "    import pip\n"
    "    print('pip', getattr(pip, '__version__', 'ok'))\n"
    "except Exception as exc:\n"
    "    print('pip', 'ERR', type(exc).__name__, str(exc)[:120])\n"
    "try:\n"
    "    import venv\n"
    "    print('venv', 'ok')\n"
    "except Exception as exc:\n"
    "    print('venv', 'ERR', type(exc).__name__, str(exc)[:120])\n"
)


def _parse_version(text: str) -> tuple[int, int, int] | None:
    parts = text.strip().split(".")
    try:
        nums = [int(p) for p in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def probe_interpreter(python_exe: str, *, runner: Runner | None = None) -> InterpreterProbe:
    run = runner or subprocess.run
    try:
        proc = run(
            [python_exe, "-c", _PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError:
        return InterpreterProbe(executable=python_exe, error="executable not found")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return InterpreterProbe(executable=python_exe, error=str(exc))

    out = (getattr(proc, "stdout", None) or "") + "\n" + (getattr(proc, "stderr", None) or "")
    if getattr(proc, "returncode", 1) != 0 and "version " not in out:
        return InterpreterProbe(
            executable=python_exe,
            error=(out.strip() or f"exit {getattr(proc, 'returncode', '?')}"),
        )

    version = None
    has_pip = False
    has_venv = False
    pip_detail = ""
    venv_detail = ""
    for raw in out.splitlines():
        line = raw.strip()
        if line.startswith("version "):
            version = _parse_version(line.split(" ", 1)[1])
        elif line.startswith("pip "):
            rest = line.split(" ", 1)[1]
            if rest.startswith("ERR"):
                pip_detail = rest
            else:
                has_pip = True
                pip_detail = rest
        elif line.startswith("venv "):
            rest = line.split(" ", 1)[1]
            if rest.startswith("ERR"):
                venv_detail = rest
            else:
                has_venv = True
                venv_detail = rest
    return InterpreterProbe(
        executable=python_exe,
        version_info=version,
        has_pip=has_pip,
        has_venv=has_venv,
        pip_detail=pip_detail or ("pip  (python -m pip)"),
        venv_detail=venv_detail or "import venv",
    )


def check_write_access(root: Path) -> CheckItem:
    probe = root / ".install_write_probe"
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckItem(CHECK_WRITE, True, f"can write {root}")
    except OSError as exc:
        return CheckItem(
            CHECK_WRITE,
            False,
            str(exc),
            hint="Copy the project to a writable folder (not Program Files) and re-run as a user with write permission.",
        )


def check_network(
    *,
    host: str = "pypi.org",
    port: int = 443,
    timeout: float = 5.0,
    urlopen: Callable[..., Any] | None = None,
    connect: Callable[..., Any] | None = None,
) -> CheckItem:
    try:
        if urlopen is not None:
            req = urllib_request_get(f"https://{host}/")
            with urlopen(req, timeout=timeout) as resp:
                getattr(resp, "status", None)
        else:
            opener = connect
            if opener is None:
                import socket

                sock = socket.create_connection((host, port), timeout)
                sock.close()
            else:
                sock = opener()
                close = getattr(sock, "close", None)
                if callable(close):
                    close()
        return CheckItem(CHECK_NETWORK, True, f"{host}:{port} reachable", required=False)
    except Exception as exc:  # noqa: BLE001 — any failure is "no network for pip"
        return CheckItem(
            CHECK_NETWORK,
            False,
            str(exc) or type(exc).__name__,
            required=False,
            hint="pip needs internet unless wheels are already cached. Connect, or set HTTP(S)_PROXY, then retry.",
        )


def urllib_request_get(url: str) -> Any:
    import urllib.request

    return urllib.request.Request(url, method="GET", headers={"User-Agent": "ForX-RnD-install-check"})


def check_requirements(root: Path) -> CheckItem:
    path = root / "requirements.txt"
    if path.is_file():
        return CheckItem(CHECK_REQUIREMENTS, True, f"found {path.name}")
    return CheckItem(
        CHECK_REQUIREMENTS,
        False,
        f"not found at {path}",
        hint="Run the installer from the Forex Research Lab folder (the one that contains requirements.txt).",
    )


def build_preflight_report(
    *,
    root: Path,
    python_exe: str | None,
    probe: InterpreterProbe | None = None,
    write_item: CheckItem | None = None,
    network_item: CheckItem | None = None,
    requirements_item: CheckItem | None = None,
    skip_network: bool = False,
    runner: Runner | None = None,
    urlopen: Callable[..., Any] | None = None,
) -> InstallReport:
    report = InstallReport(root=root)
    if not python_exe:
        for item in missing_python_items():
            report.add(item)
    else:
        probed = probe if probe is not None else probe_interpreter(python_exe, runner=runner)
        if probed.version_info is None:
            report.add(
                CheckItem(
                    CHECK_PYTHON,
                    False,
                    probed.error or f"could not run {python_exe}",
                    hint=python_install_hint(),
                )
            )
        else:
            ver = ".".join(str(x) for x in probed.version_info)
            if probed.version_ok:
                report.add(CheckItem(CHECK_PYTHON, True, f"{ver}  ({python_exe})"))
            else:
                report.add(
                    CheckItem(
                        CHECK_PYTHON,
                        False,
                        f"found {ver} (need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)",
                        hint=python_install_hint(),
                    )
                )
        if probed.has_pip:
            report.add(CheckItem(CHECK_PIP, True, probed.pip_detail or "ok"))
        else:
            report.add(
                CheckItem(
                    CHECK_PIP,
                    False,
                    probed.pip_detail or "python -m pip / import pip failed",
                    hint='Reinstall Python and keep "pip" checked, or run: python -m ensurepip --upgrade',
                )
            )
        if probed.has_venv:
            report.add(CheckItem(CHECK_VENV, True, probed.venv_detail or "import venv ok"))
        else:
            report.add(
                CheckItem(
                    CHECK_VENV,
                    False,
                    probed.venv_detail or "import venv failed",
                    hint="Windows: reinstall from python.org (venv is included). Linux: install python3-venv.",
                )
            )

    report.add(write_item if write_item is not None else check_write_access(root))
    if skip_network:
        report.add(CheckItem(CHECK_NETWORK, True, "skipped", required=False))
    else:
        report.add(network_item if network_item is not None else check_network(urlopen=urlopen))
    report.add(requirements_item if requirements_item is not None else check_requirements(root))
    return report


def venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def create_venv(
    root: Path,
    python_exe: str,
    *,
    runner: Runner | None = None,
) -> CheckItem:
    dest = root / ".venv"
    py = venv_python(root)
    if py.is_file():
        return CheckItem(".venv", True, f"already exists ({py})")
    if dest.exists():
        return CheckItem(
            ".venv",
            False,
            f"{dest} exists but {py.name} is missing",
            hint="Delete the .venv folder and re-run INSTALL.bat.",
        )
    run = runner or subprocess.run
    try:
        proc = run(
            [python_exe, "-m", "venv", str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckItem(
            ".venv",
            False,
            str(exc),
            hint="Close other programs using this folder and check write permission.",
        )
    if getattr(proc, "returncode", 1) != 0:
        err = ((getattr(proc, "stderr", None) or getattr(proc, "stdout", None) or "")).strip()
        return CheckItem(".venv", False, err or f"venv exit {proc.returncode}")
    if not py.is_file():
        return CheckItem(".venv", False, f"venv created but {py} is missing")
    return CheckItem(".venv", True, f"created {dest}")


def pip_install_requirements(
    root: Path,
    *,
    runner: Runner | None = None,
    python_exe: str | None = None,
) -> CheckItem:
    py = python_exe or str(venv_python(root))
    req = str(root / "requirements.txt")
    run = runner or subprocess.run
    try:
        up = run(
            [py, "-m", "pip", "install", "-U", "pip"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        inst = run(
            [py, "-m", "pip", "install", "-r", req],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckItem(
            "pip install",
            False,
            str(exc),
            hint="Check Network (pip) above. Connect to the internet and retry INSTALL.bat.",
        )
    if getattr(inst, "returncode", 1) != 0:
        err = ((getattr(inst, "stderr", None) or getattr(inst, "stdout", None) or "")).strip()
        tail = "\n".join(err.splitlines()[-8:]) if err else f"pip exit {inst.returncode}"
        pip_up = getattr(up, "returncode", 1)
        extra = "" if pip_up == 0 else " (pip upgrade also failed)"
        return CheckItem(
            "pip install",
            False,
            tail + extra,
            hint="Need network for PyPI, or a working pip cache. See Network (pip) in the checklist.",
        )
    return CheckItem("pip install", True, "requirements.txt installed")


def verify_imports(
    *,
    python_exe: str,
    runner: Runner | None = None,
) -> list[CheckItem]:
    run = runner or subprocess.run
    script = (
        "import importlib\n"
        "mods = ('streamlit', 'forex_lab')\n"
        "for name in mods:\n"
        "    try:\n"
        "        m = importlib.import_module(name)\n"
        "        ver = getattr(m, '__version__', 'ok')\n"
        "        print(name, 'OK', ver)\n"
        "    except Exception as exc:\n"
        "        print(name, 'MISSING', type(exc).__name__, str(exc)[:160])\n"
    )
    try:
        proc = run(
            [python_exe, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [
            CheckItem(CHECK_STREAMLIT, False, str(exc)),
            CheckItem(CHECK_FOREX_LAB, False, str(exc)),
        ]
    found: dict[str, CheckItem] = {}
    out = getattr(proc, "stdout", None) or ""
    for raw in out.splitlines():
        parts = raw.strip().split(" ", 2)
        if len(parts) < 2:
            continue
        name, status = parts[0], parts[1]
        detail = parts[2] if len(parts) > 2 else ""
        label = CHECK_STREAMLIT if name == "streamlit" else CHECK_FOREX_LAB if name == "forex_lab" else name
        found[name] = CheckItem(label, status == "OK", detail or status)
    items = [
        found.get("streamlit")
        or CheckItem(CHECK_STREAMLIT, False, "no output from verifier"),
        found.get("forex_lab")
        or CheckItem(CHECK_FOREX_LAB, False, "no output from verifier"),
    ]
    if getattr(proc, "returncode", 1) != 0 and all(i.ok for i in items):
        err = (getattr(proc, "stderr", None) or "").strip()
        items.append(CheckItem("import verifier", False, err or f"exit {proc.returncode}"))
    return items


def run_install(
    *,
    root: Path,
    python_exe: str | None,
    skip_network: bool = False,
    runner: Runner | None = None,
    urlopen: Callable[..., Any] | None = None,
    create_venv_fn: Callable[..., CheckItem] | None = None,
    pip_install_fn: Callable[..., CheckItem] | None = None,
    verify_fn: Callable[..., list[CheckItem]] | None = None,
    probe: InterpreterProbe | None = None,
) -> InstallReport:
    """Preflight, create .venv, pip install, verify streamlit + forex_lab."""
    report = build_preflight_report(
        root=root,
        python_exe=python_exe,
        probe=probe,
        skip_network=skip_network,
        runner=runner,
        urlopen=urlopen,
    )
    if not report.ok or not python_exe:
        return report

    venv_item = (create_venv_fn or create_venv)(root, python_exe, runner=runner)
    report.add(venv_item)
    if not venv_item.ok:
        return report

    pip_item = (pip_install_fn or pip_install_requirements)(
        root, runner=runner, python_exe=str(venv_python(root))
    )
    report.add(pip_item)
    if not pip_item.ok:
        return report

    for item in (verify_fn or verify_imports)(
        python_exe=str(venv_python(root)),
        runner=runner,
    ):
        report.add(item)
    return report


def _print_report(report: InstallReport, *, title: str) -> int:
    text = format_checklist(report, title=title)
    try:
        from forex_lab.console import ascii_text

        text = ascii_text(text)
    except Exception:
        text = text.encode("ascii", "replace").decode("ascii")
    try:
        print(text)
    except Exception:
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write((text + "\n").encode("ascii", "replace"))
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forex Research Lab installer checks (OK/MISSING checklist).",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Preflight, create .venv, pip install -r requirements.txt, verify imports.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Requirement checks only (no venv / pip).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify streamlit + forex_lab import using .venv (or --python).",
    )
    parser.add_argument("--python", default="", help="Python executable to probe (default: discover / this interpreter).")
    parser.add_argument("--root", default="", help="Project root (default: repo containing this file).")
    parser.add_argument("--skip-network", action="store_true", help="Do not probe PyPI.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).resolve() if args.root else project_root()
    python_exe = (args.python or "").strip() or discover_python() or sys.executable

    if args.verify:
        py = args.python.strip() or str(venv_python(root))
        report = InstallReport(root=root)
        if not Path(py).exists() and py == str(venv_python(root)):
            report.add(
                CheckItem(
                    ".venv",
                    False,
                    f"missing {py}",
                    hint="Run INSTALL.bat first (creates .venv and installs requirements.txt).",
                )
            )
        else:
            for item in verify_imports(python_exe=py):
                report.add(item)
        return _print_report(report, title="Import verification")

    if args.install:
        report = run_install(root=root, python_exe=python_exe, skip_network=args.skip_network)
        return _print_report(report, title="Install checklist")

    # default: preflight
    report = build_preflight_report(
        root=root,
        python_exe=python_exe,
        skip_network=args.skip_network,
    )
    title = "Preflight checklist" if args.preflight or True else "Install checklist"
    return _print_report(report, title=title)


if __name__ == "__main__":
    if __package__ is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
