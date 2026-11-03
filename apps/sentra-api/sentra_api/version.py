import re
import subprocess
from pathlib import Path


BUILD_SHA_FILE = "BUILD_SHA"
SHA_PATTERN = re.compile(r"[0-9a-f]{40,64}")


def git_version(repository: Path | None = None) -> str:
    """Return the deployed commit SHA once during app construction, never per request.

    A CF bundle has no .git, so prepare_cf_bundle.ps1 writes BUILD_SHA at the bundle root.
    That explicit build metadata wins; a git checkout is the fallback.
    """
    repository = repository or Path(__file__).resolve().parents[3]
    try:
        recorded = (repository / BUILD_SHA_FILE).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        recorded = ""
    if SHA_PATTERN.fullmatch(recorded):
        return recorded
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repository.as_posix()}", "rev-parse", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
