"""Build code.zip for submission:  python code/package_zip.py

Includes README.md and everything under code/ (source, prompts, evaluation reports); excludes the dataset, the
generated cache/index (derived from the dataset), virtual environments, __pycache__, nested archives and secrets.
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", "cache", "dataset"}
SKIP_SUFFIX = {".pyc", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".sqlite"}
SKIP_NAMES = {".env", ".DS_Store", "predicted_output.csv"}


def main():
    out = ROOT / "code.zip"
    if out.exists():
        out.unlink()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "README.md", "README.md")
        for path in sorted((ROOT / "code").rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or set(rel.parts) & SKIP_DIRS or path.suffix in SKIP_SUFFIX or path.name in SKIP_NAMES:
                continue
            z.write(path, str(rel))
            n += 1
    print(f"wrote {out} with {n + 1} files")


if __name__ == "__main__":
    main()
