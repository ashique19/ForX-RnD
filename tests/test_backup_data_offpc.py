import importlib.util
import pathlib
import shutil
import subprocess

import pytest

_spec = importlib.util.spec_from_file_location(
    "backup_data_offpc", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backup_data_offpc.py")
B = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(B)


def _tree(root):
    for rel, body in {"data/paper_shadow/journal.jsonl": "{}\n", "data/paper_shadow/explore_journal.jsonl": "{}\n",
                      "data/EURUSD_1h.csv": "a,b\n", "data/EURUSD_1d.csv": "x\n", "models/m.joblib": "bin",
                      "config/watchlist.yaml": "pairs: []\n", "docs/experiment_log.md": "# log\n",
                      ".env.mt5": "MT5_PASSWORD=x\n", "data/costs/api_token.txt": "t", "data/history/EURUSD.parquet": "h" * 5000}.items():
        p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body, encoding="utf-8")


def test_collect_excludes_credentials_and_unlisted(tmp_path):
    _tree(tmp_path)
    got = B.collect(B.SETS["daily"], tmp_path)
    assert "data/EURUSD_1h.csv" in got and "config/watchlist.yaml" in got and "docs/experiment_log.md" in got
    assert not any(".env" in g or "token" in g for g in got) and "data/EURUSD_1d.csv" not in got
    assert B.collect(B.SETS["history"], tmp_path) == ["data/history/EURUSD.parquet"]


def _age():
    try:
        return B.age_exe()
    except RuntimeError:
        return None


@pytest.mark.skipif(_age() is None, reason="age not installed")
def test_roundtrip_split_and_restore(tmp_path, monkeypatch):
    root = tmp_path / "root"; _tree(root)
    keydir = tmp_path / "key"; keydir.mkdir()
    keygen = str(pathlib.Path(_age()).with_name("age-keygen" + pathlib.Path(_age()).suffix))
    subprocess.run([keygen, "-o", str(keydir / "age-key.txt")], check=True, capture_output=True)
    pub = subprocess.run([keygen, "-y", str(keydir / "age-key.txt")], check=True, capture_output=True, text=True).stdout.strip()
    store = tmp_path / "store"
    monkeypatch.setattr(B, "SPLIT_BYTES", 200)  # force multi-part
    d = B.build("daily", "2026-10-19", store, root=root, recipient=pub)
    assert d["files"] == 6 and len(d["parts"]) > 1
    assert b"MT5_PASSWORD" not in b"".join(p.read_bytes() for p in store.iterdir())
    res = B.restore_test(store, keydir / "age-key.txt", root=root)
    assert res["daily"]["manifest_mismatch"] == 0 and res["daily"]["identical_to_original_now"] == 6


def test_prune_keeps_last_seven(tmp_path):
    for day in range(1, 11):
        (tmp_path / f"forx-daily-2026-10-{day:02d}.tar.gz.age").write_text("x")
    (tmp_path / "forx-history-2026-10-01.tar.gz.age.part000").write_text("x")
    (tmp_path / "forx-history-2026-10-09.tar.gz.age").write_text("x")
    B.prune(tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len([n for n in names if "daily" in n]) == 7 and names[0] == "forx-daily-2026-10-04.tar.gz.age"
    assert [n for n in names if "history" in n] == ["forx-history-2026-10-09.tar.gz.age"]


@pytest.mark.parametrize("remote", ["https://github.com/ashique19/ForX-RnD.git", "https://github.com/x/other.git"])
def test_publish_refuses_other_remotes(tmp_path, remote):
    with pytest.raises(RuntimeError, match="refusing"):
        B.publish(tmp_path, repo=tmp_path / "r", remote=remote)
