"""Nightly encrypted off-PC backup of ForX runtime data to a SEPARATE private GitHub repo.

* Encrypts with age to the PUBLIC recipient in C:\\AI\\forx-backup-key\\age-recipient.txt;
  the private key (age-key.txt, same folder, outside every repo) is needed only to restore.
* Sets: "daily" (journals, paper broker, 1h CSVs, costs, calendar, models, champion,
  watchlist, experiment log) every run; "history" (data/history, large, mostly static)
  only when its content hash changes. Credentials (.env*, *secret*, *token*, keys) are never
  included. Archives over 90 MB are split into parts (GitHub's 100 MB file limit).
* Keeps the last 7 daily archives + the latest history archive in a local store and
  publishes exactly that set as ONE orphan commit force-pushed to the backup repo only
  (history squashed so the repo stays small). Refuses any other remote.
* Offline / repo missing: archives are still made locally; publish is retried next run.
* Logs to data/backup/backup.log. Restore check: --restore-test.

Run: .venv\\Scripts\\python.exe scripts\\backup_data_offpc.py [--if-missed] [--restore-test] [--no-publish]
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DHAKA = ZoneInfo("Asia/Dhaka")
REMOTE = "https://github.com/ashique19/ForX-data-backup.git"
KEY_DIR = Path(os.environ.get("FORX_BACKUP_KEY_DIR", r"C:\AI\forx-backup-key"))
STORE = Path(os.environ.get("FORX_BACKUP_STORE", r"C:\AI\forx-backup-store"))
REPO_DIR = Path(os.environ.get("FORX_BACKUP_REPO_DIR", r"C:\AI\forx-data-backup-repo"))
LOG = ROOT / "data" / "backup" / "backup.log"
KEEP_DAILY = 7
SPLIT_BYTES = 90 * 1024 * 1024
SETS = {
    "daily": ["data/paper_shadow/**", "data/paper_broker.json", "data/*_1h.csv", "data/costs/**", "data/calendar/**",
              "models/**", "data/champion/**", "config/watchlist.yaml", "docs/experiment_log.md"],
    "history": ["data/history/**"],
}
EXCLUDE = re.compile(r"(^|[\\/])\.env|secret|token|credential|password|\.pem$|\.key$|\.pfx$|id_rsa|age-key", re.I)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC ") + msg
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


def age_exe() -> str:
    for c in (shutil.which("age"), str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links/age.exe")):
        if c and Path(c).is_file():
            return c
    raise RuntimeError("age not found (winget install FiloSottile.age)")


def collect(patterns: list[str], root: Path = ROOT) -> list[str]:
    out: set[str] = set()
    for pat in patterns:
        if pat.endswith("/**"):
            base = root / pat[:-3]
            if base.is_dir():
                out |= {p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()}
        else:
            d = root / Path(pat).parent
            if d.is_dir():
                out |= {p.relative_to(root).as_posix() for p in d.iterdir()
                        if p.is_file() and fnmatch.fnmatch(p.name, Path(pat).name)}
    return sorted(r for r in out if not EXCLUDE.search(r) and not r.endswith((".tmp", ".lock")))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(set_name: str, stamp: str, out_dir: Path, root: Path = ROOT, recipient: str | None = None) -> dict:
    """Snapshot -> manifest -> tar.gz -> age (public key) -> split. Returns the manifest summary."""
    files = collect(SETS[set_name], root)
    recipient = recipient or (KEY_DIR / "age-recipient.txt").read_text(encoding="utf-8").strip()
    if not recipient.startswith("age1"):
        raise RuntimeError("bad age recipient")
    with tempfile.TemporaryDirectory(prefix="forx_bk_") as td:
        stage = Path(td) / "stage"
        manifest = {}
        for rel in files:
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(root / rel, dst)  # snapshot first: live journals keep appending
            except OSError:
                continue
            manifest[rel] = {"sha256": sha256(dst), "size": dst.stat().st_size}
        meta = {"set": set_name, "stamp": stamp, "created_utc": datetime.now(timezone.utc).isoformat(), "files": manifest}
        tgz = Path(td) / "a.tar.gz"
        with tarfile.open(tgz, "w:gz", compresslevel=6) as tar:
            raw = json.dumps(meta, indent=1).encode("utf-8")
            info = tarfile.TarInfo("MANIFEST.json"); info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
            for rel in manifest:
                tar.add(stage / rel, arcname=rel)
        enc = Path(td) / "a.age"
        subprocess.run([age_exe(), "-r", recipient, "-o", str(enc), str(tgz)], check=True, capture_output=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"forx-{set_name}-{stamp}.tar.gz.age"
        for old in out_dir.glob(name + "*"):
            old.unlink()
        parts = []
        size = enc.stat().st_size
        if size <= SPLIT_BYTES:
            shutil.move(str(enc), out_dir / name); parts.append(name)
        else:
            with enc.open("rb") as fh:
                i = 0
                while chunk := fh.read(SPLIT_BYTES):
                    pn = f"{name}.part{i:03d}"; (out_dir / pn).write_bytes(chunk); parts.append(pn); i += 1
    content = hashlib.sha256(json.dumps({k: v["sha256"] for k, v in manifest.items()}, sort_keys=True).encode()).hexdigest()
    return {"set": set_name, "stamp": stamp, "files": len(manifest), "bytes": size, "parts": parts, "content_sha": content}


def prune(store: Path) -> None:
    dailies = sorted({p.name.split(".tar.gz.age")[0] for p in store.glob("forx-daily-*.tar.gz.age*")})
    for stem in dailies[:-KEEP_DAILY]:
        for p in store.glob(stem + ".tar.gz.age*"):
            p.unlink()
    hist = sorted({p.name.split(".tar.gz.age")[0] for p in store.glob("forx-history-*.tar.gz.age*")})
    for stem in hist[:-1]:
        for p in store.glob(stem + ".tar.gz.age*"):
            p.unlink()


def _git(*args: str, cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def publish(store: Path, repo: Path = REPO_DIR, remote: str = REMOTE) -> dict:
    """One squashed orphan commit with exactly the kept archives, force-pushed to the BACKUP repo only."""
    if not remote.rstrip("/").endswith("/ForX-data-backup.git") or "ForX-RnD" in remote:
        raise RuntimeError(f"refusing to force-push to {remote}")
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").is_dir():
        _git("init", "-b", "main", cwd=repo)
        _git("remote", "add", "origin", remote, cwd=repo)
    url = _git("remote", "get-url", "origin", cwd=repo).stdout.strip()
    if url != remote:
        raise RuntimeError(f"backup repo remote is {url!r}, expected {remote!r}; refusing")
    for p in repo.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in sorted(store.glob("forx-*.tar.gz.age*")):
        shutil.copy2(p, repo / p.name)
    rec = (KEY_DIR / "age-recipient.txt")
    (repo / "README.md").write_text(
        "# ForX data backup (encrypted)\n\nage-encrypted tar.gz archives of ForX runtime data. Last 7 daily archives + "
        "latest history archive; history is squashed on every run.\n\nRestore: concatenate any .partNNN files in order, then "
        "`age -d -i age-key.txt -o out.tar.gz <archive>` and `tar -xzf out.tar.gz` (MANIFEST.json lists sha256 per file).\n\n"
        f"Recipient (public key): `{rec.read_text(encoding='utf-8').strip() if rec.is_file() else '?'}`\n", encoding="utf-8")
    for args in (("checkout", "-q", "--orphan", "snapshot"), ("add", "-A")):
        _git(*args, cwd=repo)
    c = _git("-c", "user.name=ForX backup", "-c", "user.email=forx-backup@localhost", "commit", "-q", "-m",
             f"backup snapshot {datetime.now(DHAKA).strftime('%Y-%m-%d %H:%M Asia/Dhaka')}", cwd=repo)
    if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
        raise RuntimeError("commit failed: " + c.stderr.strip()[:300])
    _git("branch", "-D", "main", cwd=repo)
    _git("branch", "-m", "main", cwd=repo)
    p = _git("push", "--force", "origin", "main", cwd=repo)
    ok = p.returncode == 0
    if ok:
        _git("reflog", "expire", "--expire=now", "--all", cwd=repo)
        _git("gc", "-q", "--prune=now", cwd=repo)
    return {"pushed": ok, "error": None if ok else (p.stderr.strip().splitlines() or ["?"])[-1][:300]}


def restore_test(store: Path, key: Path, root: Path = ROOT) -> dict:
    """Decrypt the newest archive of each set to a temp dir; check MANIFEST hashes and compare with the originals."""
    out = {}
    for set_name in SETS:
        stems = sorted({p.name.split(".tar.gz.age")[0] for p in store.glob(f"forx-{set_name}-*.tar.gz.age*")})
        if not stems:
            continue
        stem = stems[-1]
        with tempfile.TemporaryDirectory(prefix="forx_rt_") as td:
            enc = Path(td) / "a.age"
            with enc.open("wb") as fh:
                for p in sorted(store.glob(stem + ".tar.gz.age*")):
                    fh.write(p.read_bytes())
            tgz = Path(td) / "a.tar.gz"
            subprocess.run([age_exe(), "-d", "-i", str(key), "-o", str(tgz), str(enc)], check=True, capture_output=True)
            dst = Path(td) / "x"
            with tarfile.open(tgz, "r:gz") as tar:
                try:
                    tar.extractall(dst, filter="data")
                except TypeError:  # Python without tarfile extraction filters
                    tar.extractall(dst)  # noqa: S202 -- our own archive, manifest-verified below
            meta = json.loads((dst / "MANIFEST.json").read_text(encoding="utf-8"))
            bad = [r for r, v in meta["files"].items() if sha256(dst / r) != v["sha256"]]
            same = changed = missing = 0
            for r, v in meta["files"].items():
                orig = root / r
                if not orig.is_file():
                    missing += 1
                elif sha256(orig) == v["sha256"]:
                    same += 1
                else:
                    changed += 1
            out[set_name] = {"archive": stem, "files": len(meta["files"]), "manifest_mismatch": len(bad),
                             "identical_to_original_now": same, "original_changed_since": changed, "original_missing": missing}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--if-missed", action="store_true", help="skip if today's (Dhaka) archive was already published")
    ap.add_argument("--restore-test", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args(argv)
    stamp = datetime.now(DHAKA).strftime("%Y-%m-%d")
    STORE.mkdir(parents=True, exist_ok=True)
    state_f = STORE / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.is_file() else {}
    if a.restore_test:
        res = restore_test(STORE, KEY_DIR / "age-key.txt")
        log("restore test " + json.dumps(res))
        return 0 if res and all(v["manifest_mismatch"] == 0 for v in res.values()) else 1
    if a.if_missed and state.get("last_published_stamp") == stamp:
        log(f"skip: {stamp} already published")
        return 0
    try:
        d = build("daily", stamp, STORE)
        log(f"built daily {d['parts']} files={d['files']} bytes={d['bytes']}")
        hist_sha = hashlib.sha256(json.dumps(
            {r: [(ROOT / r).stat().st_size, int((ROOT / r).stat().st_mtime)] for r in collect(SETS["history"])},
            sort_keys=True).encode()).hexdigest()
        if hist_sha != state.get("history_stat_sha") or not list(STORE.glob("forx-history-*")):
            h = build("history", stamp, STORE)
            state["history_stat_sha"] = hist_sha
            log(f"built history {h['parts']} files={h['files']} bytes={h['bytes']}")
        else:
            log("history unchanged; keeping previous history archive")
        prune(STORE)
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR build: {type(exc).__name__}: {exc}")
        return 2
    state["last_built_stamp"] = stamp
    if not a.no_publish:
        try:
            res = publish(STORE)
        except Exception as exc:  # noqa: BLE001
            res = {"pushed": False, "error": f"{type(exc).__name__}: {exc}"}
        log("publish " + json.dumps(res))
        if res.get("pushed"):
            state["last_published_stamp"] = stamp
    state_f.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
