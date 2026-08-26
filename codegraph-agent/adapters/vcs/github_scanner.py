"""GitHub repo source — implements the RepoSource port. Implemented in step 2."""

from core.retrieval.interfaces import RepoSource


class GitHubScanner:
    """Will clone a public GitHub URL into .codegraph/repos/<name> via git CLI."""

    def clone(self, url: str) -> str:
        raise NotImplementedError("implemented in build step 2")
    