#!/usr/bin/env python3
"""Package source changes for WSL, excluding credentials, caches and runtime state."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "localstack/.state/transfer/micro-test-local-laptop.zip"

APPLY = r'''#!/usr/bin/env python3
import argparse, hashlib, json, shutil
from pathlib import Path

base = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description="Apply lab source overlay after validating every destination; never overwrite unrelated edits")
parser.add_argument("--repo", required=True)
parser.add_argument("--apply", action="store_true", help="Without this flag, only check")
args = parser.parse_args()
repo = Path(args.repo).resolve()
if not (repo / ".git").exists(): raise SystemExit("Destination must be an existing Git checkout")
manifest = json.loads((base / "manifest.json").read_text())
pending, conflicts = [], []
for entry in manifest["files"]:
    path = Path(entry["path"])
    if path.is_absolute() or ".." in path.parts: raise SystemExit("Invalid manifest path")
    source = base / "overlay" / path
    target = repo / path
    if not target.resolve().is_relative_to(repo): raise SystemExit("Destination escapes repository: " + str(path))
    incoming = hashlib.sha256(source.read_bytes()).hexdigest()
    if incoming != entry["sha256"]: raise SystemExit("Bundle checksum mismatch: " + str(path))
    if target.exists():
        current = hashlib.sha256(target.read_bytes()).hexdigest()
        if current == incoming: continue
        if current != entry["baseSha256"]:
            conflicts.append(str(path)); continue
    elif entry["baseSha256"] is not None:
        conflicts.append(str(path)); continue
    pending.append((source, target))
if conflicts:
    raise SystemExit("Existing edits preserved. Use a clean checkout at " + manifest["baseCommit"] + ":\n" + "\n".join(conflicts))
print("CHECK OK:", len(pending), "files to apply; base", manifest["baseCommit"])
if args.apply:
    for source, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    print("APPLIED. Follow localstack/business/RUNBOOK.md inside WSL.")
'''


def git(*args):
    return subprocess.check_output(["git", "-C", str(REPO), *args])


def main():
    tracked = git("diff", "--name-only", "HEAD", "--", "src", "localstack").decode().splitlines()
    new = git("ls-files", "--others", "--exclude-standard", "--", "src", "localstack").decode().splitlines()
    files = sorted(set(tracked + new))
    entries = []
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            relative = Path(name)
            if any(part in {".state", ".venv", "__pycache__", ".git"} for part in relative.parts):
                raise ValueError("Runtime state must never enter source bundle")
            path = REPO / relative
            if path.is_symlink() or not path.is_file(): raise ValueError("Only regular source files supported: " + name)
            content = path.read_bytes()
            original = git("show", "HEAD:" + name) if name in tracked else None
            entries.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(),
                            "baseSha256": hashlib.sha256(original).hexdigest() if original is not None else None})
            archive.writestr("overlay/" + relative.as_posix(), content)
        manifest = {"baseCommit": git("rev-parse", "HEAD").decode().strip(), "files": entries,
                    "excludes": [".state", ".venv", "tokens", "kubeconfigs", "Docker/SQL state", "upstream source caches"]}
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        archive.writestr("apply.py", APPLY)
        archive.writestr("LEEME.txt", """Cambios locales para laptop/WSL2. No incluye credenciales ni estado del laboratorio.
Descomprime ZIP en carpeta aparte; no lo extraigas encima de tu repositorio.
Desde WSL ejecuta primero:
  python3 /ruta/al/bundle/apply.py --repo /ruta/al/repositorio
Si CHECK OK:
  python3 /ruta/al/bundle/apply.py --repo /ruta/al/repositorio --apply
Si detecta diferencias, conserva tu copia y usa checkout limpio del commit en manifest.json.
Luego sigue localstack/business/RUNBOOK.md para compras e Istio.
Karpenter con workers Docker reales: localstack/karpenter/README.md; cluster adicional.
""")
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    OUTPUT.with_suffix(".zip.sha256").write_text(digest + "  " + OUTPUT.name + "\n")
    print(json.dumps({"zip": str(OUTPUT), "sha256": digest, "files": len(entries)}, indent=2))


if __name__ == "__main__": main()
