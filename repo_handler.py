import os
import tempfile
import subprocess
import shutil

def process_repository(repo_url: str) -> str:
    """Clone repository and return merged code text."""
    tmpdir = tempfile.mkdtemp()
    collected = []
    try:
        subprocess.run(["git", "clone", "--depth", "1", repo_url, tmpdir], check=True, capture_output=True)
        for root, _, files in os.walk(tmpdir):
            for fname in files:
                fpath = os.path.join(root, fname)
                if os.path.getsize(fpath) > 200_000:
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        collected.append(f.read())
                except Exception:
                    continue
        return "\n".join(collected)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
