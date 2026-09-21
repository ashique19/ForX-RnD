"""Install preflight: OK/MISSING checklist, including missing Python."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from forex_lab.install_check import (
    CHECK_FOREX_LAB,
    CHECK_NETWORK,
    CHECK_PIP,
    CHECK_PYTHON,
    CHECK_STREAMLIT,
    CHECK_VENV,
    CHECK_WRITE,
    PYTHON_DOWNLOAD_URL,
    CheckItem,
    InstallReport,
    InterpreterProbe,
    build_preflight_report,
    discover_python,
    format_checklist,
    missing_python_items,
    pip_install_requirements,
    probe_interpreter,
    python_install_hint,
    run_install,
    verify_imports,
)
from forex_lab.paths import project_root


def _probe(
    *,
    version=(3, 12, 1),
    pip=True,
    venv=True,
    exe="C:\\Python312\\python.exe",
) -> InterpreterProbe:
    return InterpreterProbe(
        executable=exe,
        version_info=version,
        has_pip=pip,
        has_venv=venv,
        pip_detail="24.0" if pip else "ERR ModuleNotFoundError",
        venv_detail="ok" if venv else "ERR ModuleNotFoundError",
    )


def test_missing_python_items_include_install_link():
    items = missing_python_items()
    names = [i.name for i in items]
    assert names[:3] == [CHECK_PYTHON, CHECK_PIP, CHECK_VENV]
    assert all(not i.ok and i.status == "MISSING" for i in items)
    text = python_install_hint()
    assert PYTHON_DOWNLOAD_URL in text
    assert "INSTALL.bat" in text
    assert "3.11" in text
    assert any(PYTHON_DOWNLOAD_URL in (i.hint or "") for i in items)


def test_discover_python_none_when_nothing_on_path():
    found = discover_python(
        which=lambda _name: None,
        path_exists=lambda _p: False,
        env={},
        runner=lambda *_a, **_k: (_ for _ in ()).throw(OSError("no py")),
    )
    assert found is None


def test_discover_python_uses_forx_python_override():
    fake = Path("/opt/forx/python")
    found = discover_python(
        which=lambda _name: None,
        path_exists=lambda p: Path(p) == fake,
        env={"FORX_PYTHON": str(fake)},
        runner=lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert found == str(fake)


def test_discover_python_uses_which_then_resolves_executable():
    def which(name: str) -> str | None:
        return "/usr/bin/python3.12" if name == "python3.12" else None

    def runner(cmd, **_k):
        if cmd[0].endswith("python3.12") and cmd[1] == "-c":
            return SimpleNamespace(returncode=0, stdout="/resolved/python3.12\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    def path_exists(path) -> bool:
        # Path("/x") becomes "\x" on Windows; normalize for the mock.
        s = str(path).replace(chr(92), "/")
        return s in {"/resolved/python3.12", "/usr/bin/python3.12"}

    found = discover_python(
        which=which,
        path_exists=path_exists,
        env={},
        runner=runner,
    )
    assert found == "/resolved/python3.12"

def test_preflight_missing_python_prints_ok_missing_checklist(tmp_path):
    (tmp_path / "requirements.txt").write_text("streamlit>=1.32\n", encoding="utf-8")
    report = build_preflight_report(
        root=tmp_path,
        python_exe=None,
        skip_network=True,
    )
    text = format_checklist(report, title="Preflight checklist")
    assert "[MISSING]" in text
    assert CHECK_PYTHON in text
    assert PYTHON_DOWNLOAD_URL in text
    assert "[OK]" in text
    assert "requirements.txt" in text
    assert not report.ok
    missing_names = [i.name for i in report.required_missing()]
    assert CHECK_PYTHON in missing_names
    assert CHECK_PIP in missing_names
    assert CHECK_VENV in missing_names


def test_old_python_is_missing_not_ok(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    report = build_preflight_report(
        root=tmp_path,
        python_exe="/usr/bin/python3.10",
        probe=_probe(version=(3, 10, 11), exe="/usr/bin/python3.10"),
        skip_network=True,
    )
    py = next(i for i in report.items if i.name == CHECK_PYTHON)
    assert not py.ok
    assert "3.10" in py.detail
    assert "3.11" in py.detail
    assert PYTHON_DOWNLOAD_URL in py.hint
    assert not report.ok


def test_python_311_and_312_ok(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    for ver in ((3, 11, 0), (3, 12, 7)):
        report = build_preflight_report(
            root=tmp_path,
            python_exe="python",
            probe=_probe(version=ver),
            skip_network=True,
        )
        py = next(i for i in report.items if i.name == CHECK_PYTHON)
        assert py.ok, ver
        assert py.status == "OK"


def test_missing_pip_and_venv(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    report = build_preflight_report(
        root=tmp_path,
        python_exe="python",
        probe=_probe(pip=False, venv=False),
        skip_network=True,
    )
    pip = next(i for i in report.items if i.name == CHECK_PIP)
    venv = next(i for i in report.items if i.name == CHECK_VENV)
    assert not pip.ok and pip.status == "MISSING"
    assert not venv.ok and venv.status == "MISSING"
    assert "ensurepip" in pip.hint
    assert "venv" in venv.hint.lower()
    assert not report.ok


def test_no_write_access_is_missing(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    report = build_preflight_report(
        root=tmp_path,
        python_exe="python",
        probe=_probe(),
        write_item=CheckItem(CHECK_WRITE, False, "Permission denied", hint="writable folder"),
        skip_network=True,
    )
    write = next(i for i in report.items if i.name == CHECK_WRITE)
    assert not write.ok
    assert "Permission denied" in write.detail
    assert not report.ok


def test_check_network_optional_via_connect_failure():
    from forex_lab.install_check import check_network

    item = check_network(connect=lambda: (_ for _ in ()).throw(OSError("Name or service not known")))
    assert not item.ok and not item.required
    assert item.status == "MISSING"
    assert "pip needs internet" in item.hint


def test_network_optional_missing_does_not_fail_required(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    report = build_preflight_report(
        root=tmp_path,
        python_exe="python",
        probe=_probe(),
        network_item=CheckItem(
            CHECK_NETWORK,
            False,
            "timed out",
            required=False,
            hint="pip needs internet",
        ),
        skip_network=False,
    )
    net = next(i for i in report.items if i.name == CHECK_NETWORK)
    assert not net.ok and not net.required
    assert report.ok
    text = format_checklist(report)
    assert "optional" in text.lower()
    assert "MISSING" in text


def test_all_ok_checklist_labels(tmp_path):
    (tmp_path / "requirements.txt").write_text("streamlit\n", encoding="utf-8")
    report = build_preflight_report(
        root=tmp_path,
        python_exe="python",
        probe=_probe(),
        skip_network=True,
    )
    text = format_checklist(report)
    assert "[OK]" in text
    assert "[MISSING]" not in text
    assert report.ok
    assert "all checks OK" in text


def test_probe_interpreter_parses_runner_output():
    def runner(cmd, **_k):
        assert cmd[0] == "/tmp/py" and cmd[1] == "-c"
        return SimpleNamespace(
            returncode=0,
            stdout="version 3.11.9\npip 24.2\nvenv ok\n",
            stderr="",
        )

    probed = probe_interpreter("/tmp/py", runner=runner)
    assert probed.version_info == (3, 11, 9)
    assert probed.version_ok
    assert probed.has_pip and probed.has_venv


def test_probe_interpreter_missing_executable():
    def runner(cmd, **_k):
        raise FileNotFoundError(cmd[0])

    probed = probe_interpreter("/no/such/python", runner=runner)
    assert probed.version_info is None
    assert not probed.version_ok
    assert "not found" in probed.error


def test_verify_imports_ok_and_missing():
    def runner_ok(cmd, **_k):
        return SimpleNamespace(
            returncode=0,
            stdout="streamlit OK 1.38.0\nforex_lab OK 0.3.0\n",
            stderr="",
        )

    ok_items = verify_imports(python_exe="python", runner=runner_ok)
    assert [i.name for i in ok_items] == [CHECK_STREAMLIT, CHECK_FOREX_LAB]
    assert all(i.ok for i in ok_items)

    def runner_miss(cmd, **_k):
        return SimpleNamespace(
            returncode=0,
            stdout="streamlit MISSING ModuleNotFoundError\nforex_lab MISSING ModuleNotFoundError\n",
            stderr="",
        )

    bad = verify_imports(python_exe="python", runner=runner_miss)
    assert all(not i.ok and i.status == "MISSING" for i in bad)


def test_run_install_stops_on_missing_python_without_creating_venv(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    created = {"venv": 0, "pip": 0, "verify": 0}

    def create_venv(root, python_exe, **_k):
        created["venv"] += 1
        return CheckItem(".venv", True, "created")

    def pip_install(root, **_k):
        created["pip"] += 1
        return CheckItem("pip install", True, "ok")

    def verify(**_k):
        created["verify"] += 1
        return [CheckItem(CHECK_STREAMLIT, True, "1"), CheckItem(CHECK_FOREX_LAB, True, "1")]

    report = run_install(
        root=tmp_path,
        python_exe=None,
        skip_network=True,
        create_venv_fn=create_venv,
        pip_install_fn=pip_install,
        verify_fn=verify,
    )
    assert not report.ok
    assert created == {"venv": 0, "pip": 0, "verify": 0}
    assert any(i.name == CHECK_PYTHON and not i.ok for i in report.items)


def test_run_install_happy_path_appends_venv_pip_imports(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")

    def create_venv(root, python_exe, **_k):
        assert python_exe == "python"
        return CheckItem(".venv", True, "created")

    def pip_install(root, **_k):
        return CheckItem("pip install", True, "requirements.txt installed")

    def verify(**_k):
        return [
            CheckItem(CHECK_STREAMLIT, True, "1.38.0"),
            CheckItem(CHECK_FOREX_LAB, True, "0.3.0"),
        ]

    report = run_install(
        root=tmp_path,
        python_exe="python",
        probe=_probe(),
        skip_network=True,
        create_venv_fn=create_venv,
        pip_install_fn=pip_install,
        verify_fn=verify,
    )
    names = [i.name for i in report.items]
    assert ".venv" in names
    assert "pip install" in names
    assert CHECK_STREAMLIT in names
    assert CHECK_FOREX_LAB in names
    assert report.ok
    text = format_checklist(report)
    assert "[OK]" in text
    assert "[MISSING]" not in text


def test_run_install_stops_if_pip_fails(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    verified = []

    report = run_install(
        root=tmp_path,
        python_exe="python",
        probe=_probe(),
        skip_network=True,
        create_venv_fn=lambda *_a, **_k: CheckItem(".venv", True, "ok"),
        pip_install_fn=lambda *_a, **_k: CheckItem("pip install", False, "Could not find a version"),
        verify_fn=lambda **_k: verified.append("no") or [],
    )
    assert not report.ok
    assert verified == []
    assert any(i.name == "pip install" and not i.ok for i in report.items)


def test_pip_install_reports_missing_on_nonzero_exit(tmp_path):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    calls = []

    def runner(cmd, **_k):
        calls.append(cmd)
        if "pip" in cmd and "install" in cmd and "-r" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: No matching distribution")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    item = pip_install_requirements(tmp_path, runner=runner, python_exe="python")
    assert not item.ok
    assert "No matching distribution" in item.detail
    assert any("-r" in c for c in calls)


def test_install_bat_mentions_checklist_python_link_and_run_ui():
    text = (project_root() / "INSTALL.bat").read_text(encoding="utf-8")
    assert "forex_lab.install_check" in text
    assert "--install" in text
    assert "python.org/downloads" in text
    assert "[MISSING] Python 3.11+" in text
    assert "RUN_UI.bat" in text
    assert "INSTALL_LAUNCH_UI" in text
    assert "Add python.exe to PATH" in text
    assert "FORX_PYTHON" in text


def test_install_check_cli_preflight_missing_python(tmp_path, capsys):
    (tmp_path / "requirements.txt").write_text("x\n", encoding="utf-8")
    from forex_lab.install_check import main

    rc = main(
        [
            "--preflight",
            "--root",
            str(tmp_path),
            "--python",
            str(tmp_path / "no-such-python"),
            "--skip-network",
        ]
    )
    # Fake path is passed through; probe will fail -> MISSING Python.
    out = capsys.readouterr().out
    assert rc == 1
    assert "[MISSING]" in out
    assert CHECK_PYTHON in out
