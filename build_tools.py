import fnmatch
import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import config

ARCHIVE_EXT = {".zip", ".apk", ".jar", ".aar", ".war", ".whl", ".ipa", ".xpi", ".crx", ".nupkg", ".vsix"}


def load_checklist() -> list[dict]:
    return json.loads(config.CHECKLIST_PATH.read_text(encoding="utf-8"))


def _hash_path(p: Path) -> str:
    h = hashlib.sha256()
    if p.is_file():
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    else:
        for f in sorted(p.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(p)).encode())
                h.update(str(f.stat().st_size).encode())
    return h.hexdigest()[:12]


def _build_dir(build_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", build_id or ""):
        raise ValueError("Unknown build_id")
    d = config.BUILDS_DIR / build_id
    if not d.exists():
        raise ValueError("Build not opened yet. Call open_build first.")
    return d


def _safe_join(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise ValueError("Path is outside the build")
    return target


def _extract_zip(src: Path, dest: Path) -> None:
    total = 0
    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
        if len(infos) > config.MAX_FILES:
            raise ValueError(f"Too many files (>{config.MAX_FILES})")
        for info in infos:
            total += info.file_size
            if total > config.MAX_UPLOAD_MB * 4 * 1024 * 1024:
                raise ValueError("Build is too large when unpacked")
            target = _safe_join(dest, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as s, target.open("wb") as t:
                shutil.copyfileobj(s, t)


def open_build(path: str) -> dict:
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise ValueError(f"Not found: {path}")
    build_id = _hash_path(src)
    dest = config.BUILDS_DIR / build_id
    if not dest.exists():
        tmp = dest.with_suffix(".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            if src.is_dir():
                shutil.copytree(src, tmp, dirs_exist_ok=True)
            elif src.suffix.lower() in ARCHIVE_EXT or zipfile.is_zipfile(src):
                _extract_zip(src, tmp)
            else:
                shutil.copy2(src, tmp / src.name)
            tmp.rename(dest)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    files = [f for f in dest.rglob("*") if f.is_file()]
    return {
        "build_id": build_id,
        "name": src.name,
        "files": len(files),
        "size_mb": round(sum(f.stat().st_size for f in files) / 1e6, 2),
    }


def _is_text(f: Path) -> bool:
    if f.stat().st_size > config.MAX_TEXT_FILE_MB * 1024 * 1024:
        return False
    with f.open("rb") as fh:
        return b"\x00" not in fh.read(4096)


def _rel(base: Path, f: Path) -> str:
    return f.relative_to(base).as_posix()


def list_files(build_id: str, limit: int = 200) -> list[dict]:
    base = _build_dir(build_id)
    out = [{"path": _rel(base, f), "kb": round(f.stat().st_size / 1024, 1)}
           for f in sorted(base.rglob("*")) if f.is_file()]
    return out[: max(1, min(limit, 1000))]


def read_file(build_id: str, path: str, start_line: int = 1, max_lines: int = 120) -> str:
    base = _build_dir(build_id)
    f = _safe_join(base, path)
    if not f.is_file():
        raise ValueError(f"No such file: {path}")
    if not _is_text(f):
        return f"[binary or very large file, {f.stat().st_size} bytes]"
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    chunk = lines[start - 1: start - 1 + max(1, min(max_lines, 400))]
    return "\n".join(f"{start + i}: {line[:300]}" for i, line in enumerate(chunk))


def search_text(build_id: str, text: str, limit: int = 30) -> list[dict]:
    base = _build_dir(build_id)
    needle = text.lower()
    hits = []
    for f in sorted(base.rglob("*")):
        if not f.is_file() or not _is_text(f):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if needle in line.lower():
                hits.append({"file": _rel(base, f), "line": n, "text": line.strip()[:200]})
                if len(hits) >= limit:
                    return hits
    return hits


def run_checklist(build_id: str) -> list[dict]:
    base = _build_dir(build_id)
    checks = load_checklist()
    compiled = {c["id"]: [re.compile(p) for p in c.get("patterns", [])] for c in checks}
    counts = {c["id"]: 0 for c in checks}
    issues = []

    def add(check, file, line, evidence):
        if counts[check["id"]] >= config.MAX_HITS_PER_CHECK:
            return
        counts[check["id"]] += 1
        issues.append({
            "check_id": check["id"], "title": check["title"], "priority": check["priority"],
            "why": check["why"], "fix": check["fix"], "file": file, "line": line,
            "evidence": evidence, "source": "checklist",
        })

    files = [f for f in sorted(base.rglob("*")) if f.is_file()]
    for f in files:
        rel = _rel(base, f)
        for c in checks:
            if c.get("files") and any(fnmatch.fnmatch(f.name, g) or fnmatch.fnmatch(rel, g) for g in c["files"]):
                add(c, rel, None, rel)
            if c.get("max_mb") and f.stat().st_size > c["max_mb"] * 1024 * 1024:
                add(c, rel, None, rel)
        if not _is_text(f):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for c in checks:
                for rx in compiled[c["id"]]:
                    m = rx.search(line)
                    if m:
                        add(c, rel, n, m.group(0)[:200])
                        break
    return issues


def evidence_exists(build_id: str, file: str, evidence: str) -> bool:
    try:
        base = _build_dir(build_id)
        f = _safe_join(base, file)
    except ValueError:
        return False
    if not f.is_file() or not evidence:
        return False
    if evidence == file:
        return True
    if not _is_text(f):
        return False
    return evidence.strip() in f.read_text(encoding="utf-8", errors="replace")


def _db():
    con = sqlite3.connect(config.DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, created TEXT, build_id TEXT, name TEXT, verdict TEXT, issues INTEGER, report TEXT)")
    return con


def save_report(build_id: str, name: str, verdict: str, issues: int, report_md: str) -> dict:
    with _db() as con:
        cur = con.execute("INSERT INTO reports(created, build_id, name, verdict, issues, report) VALUES (?,?,?,?,?,?)",
                          (datetime.now(timezone.utc).isoformat(timespec="seconds"), build_id, name, verdict, issues, report_md))
        return {"report_id": cur.lastrowid}


def get_history(limit: int = 20) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT id, created, name, verdict, issues FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(("id", "created", "name", "verdict", "issues"), r)) for r in rows]


def get_report(report_id: int) -> str:
    with _db() as con:
        row = con.execute("SELECT report FROM reports WHERE id=?", (report_id,)).fetchone()
    return row[0] if row else ""
