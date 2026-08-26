"""GitHub repo source — implements the RepoSource port (build step 2).

Clones public GitHub URLs (shallow, default branch) into
<project-root>/.codegraph/repos/<name>. Accepts full URLs
(https://github.com/owner/repo[.git]) or 'owner/repo' shorthand.
Already-cloned repos are reused as-is; delete .codegraph/repos/<name>
to force a fresh clone.
"""

import re
import shutil
import subprocess
from pathlib import Path

from core.retrieval.interfaces import RepoSource

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REPOS_DIR = _PROJECT_ROOT / ".codegraph" / "repos"

_URL_RE = re.compile(
    r"(?:https?://(?:www\.)?)?github\.com/(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?\Z"
)
_SHORT_RE = re.compile(r"^(?P<owner>[\w.-]+)/(?P<name>[\w.-]+)$")


class GitHubScanner:
    """Clones a public GitHub repo into .codegraph/repos/<name> via git CLI."""

    def __init__(self, repos_dir: str | Path = _DEFAULT_REPOS_DIR) -> None:
        self.repos_dir = Path(repos_dir)

    def clone(self, url: str) -> str:
        owner, name = self._parse(url)
        dest = self.repos_dir / name
        if dest.exists():  # idempotent: reuse existing clone
            return str(dest)
        if shutil.which("git") is None:
            raise RuntimeError("git CLI not found on PATH")
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "clone", "--depth", "1",
             f"https://github.com/{owner}/{name}", str(dest)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0 or not dest.exists():
            raise RuntimeError(f"git clone failed: {proc.stderr.strip()}")
        return str(dest)

    @staticmethod
    def _parse(raw: str) -> tuple[str, str]:
        url = raw.strip()
        m = _SHORT_RE.match(url)
        if m:
            return m["owner"], m["name"].removesuffix(".git")
        m = _URL_RE.search(url)
        if not m:
            raise ValueError(f"cannot parse GitHub URL: {raw!r}")
        return m["owner"], m["name"]