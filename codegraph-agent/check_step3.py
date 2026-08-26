"""Step 3 smoke test. Run FROM PROJECT ROOT: python check_step3.py
Uses the already-cloned psf/requests and shallow-clones pallets/flask ONCE
(reused afterwards). Exit code 0 = green."""

import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []


def check(label, fn):
    try:
        fn()
        print(f"  PASS  {label}")
    except Exception:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
        traceback.print_exc()


# ---------------- synthetic project ----------------

SYN_SRC = '''\
"""Synthetic module docstring."""
import os


def top_level(a, b):
    """Add two numbers."""

    def inner():
        return a + b

    return inner()


@somedecorator
class Alpha:
    """Alpha does alpha things."""

    attr = 1

    def method_one(self):
        return 1

    async def method_two(self):
        return 2


async def async_top():
    pass
'''

BROKEN_SRC = "def broken(:\n    pass\n"


def make_synth_project(tmp: Path) -> None:
    (tmp / "good.py").write_text(SYN_SRC, encoding="utf-8")
    (tmp / "broken.py").write_text(BROKEN_SRC, encoding="utf-8")
    (tmp / "empty.py").write_text("", encoding="utf-8")
    for junk in ("__pycache__/junk.py", ".git/hook.py", "venv/lib.py"):
        p = tmp / junk
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")


def line_of(src: str, prefix: str) -> int:
    for i, ln in enumerate(src.splitlines(), 1):
        if ln.strip().startswith(prefix):
            return i
    raise AssertionError(f"prefix {prefix!r} not found in source")


def synth_structure_and_lines():
    from core.graph.models import NodeType
    from adapters.parsing.python_parser import PythonParser

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_synth_project(tmp)
        symbols = PythonParser().parse(str(tmp / "good.py"))

        # FILE symbol comes first, carries module docstring
        assert symbols[0].type == NodeType.FILE
        assert symbols[0].docstring == "Synthetic module docstring."
        assert symbols[0].name == "good.py"

        def one(name, typ, parent=None):
            hits = [s for s in symbols
                    if s.name == name and s.type == typ
                    and s.parent_name == parent]
            assert len(hits) == 1, f"{name}/{typ}/{parent}: {len(hits)} hits"
            return hits[0]

        top = one("top_level", NodeType.FUNCTION)
        assert top.docstring == "Add two numbers."
        assert top.start_line == line_of(SYN_SRC, "def top_level")

        one("inner", NodeType.FUNCTION, parent="top_level")      # nested def
        one("async_top", NodeType.FUNCTION)                       # async top-level
        one("method_one", NodeType.METHOD, parent="Alpha")
        one("method_two", NodeType.METHOD, parent="Alpha")        # async method

        alpha = one("Alpha", NodeType.CLASS)
        # decorator line EXCLUDED: start lands on `class`, not `@somedecorator`
        assert alpha.start_line == line_of(SYN_SRC, "class Alpha")
        syn_lines = SYN_SRC.splitlines()
        assert syn_lines[alpha.start_line - 1].strip().startswith("class")

        # round-trip: every span slices back to exactly its source_text
        for s in symbols:
            if s.type == NodeType.FILE:
                assert s.source_text == "\n".join(syn_lines)
            else:
                assert "\n".join(syn_lines[s.start_line - 1:s.end_line]) \
                    == s.source_text, f"{s.name} span mismatch"

def synth_walk_skips_junk_and_survives_broken():
    from adapters.parsing.python_parser import PythonParser

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_synth_project(tmp)
        walked = dict(PythonParser().walk_repo(tmp))
        assert set(walked) == {"good.py", "broken.py", "empty.py"}, set(walked)
        assert walked["broken.py"] == []                     # syntax error -> []
        empty = walked["empty.py"]
        assert len(empty) == 1 and empty[0].name == "empty.py"
        assert all(not r.startswith(("__pycache__", ".git", "venv"))
                   for r in walked)


def parser_satisfies_port():
    from core.retrieval.interfaces import Parser
    from adapters.parsing.python_parser import PythonParser
    assert isinstance(PythonParser(), Parser)


# ---------------- real repos ----------------

def verify_every_span(repo_root: Path, walked: dict) -> None:
    """Universal accuracy proof: each symbol's start_line sits on a
    def/async def/class keyword line and its span reproduces source_text."""
    for rel, symbols in walked.items():
        lines = (repo_root / rel).read_text(encoding="utf-8").splitlines()
        for s in symbols:
            if s.type.value == "file":
                assert "\n".join(lines) == s.source_text
                continue
            kw = lines[s.start_line - 1].strip()
            assert kw.startswith(("def ", "async def ", "class ")), \
                f"{rel}:{s.start_line} {s.name!r} lands on {kw!r}"
            assert "\n".join(lines[s.start_line - 1:s.end_line]) == s.source_text, \
                f"{rel}:{s.start_line}-{s.end_line} {s.name!r} span mismatch"


def collect(parser, repo_root: Path):
    walked = dict(parser.walk_repo(repo_root))
    flat = [s for symbols in walked.values() for s in symbols]
    return walked, flat


def requests_clean_repo():
    from core.graph.models import NodeType
    from adapters.parsing.python_parser import PythonParser
    from adapters.vcs.github_scanner import GitHubScanner

    root = Path(GitHubScanner().clone("psf/requests"))
    walked, flat = collect(PythonParser(), root)

    n_files = sum(1 for syms in walked.values() if syms)
    assert n_files >= 30, f"only {n_files} files parsed"
    assert len(flat) >= 600, f"only {len(flat)} symbols"

    session = [s for s in flat if s.type == NodeType.CLASS
               and s.name == "Session" and "requests/sessions.py" in s.file_path]
    assert len(session) == 1, f"expected exactly one real Session, got {len(session)}"

    send = [s for s in flat if s.type == NodeType.METHOD and s.name == "send"
            and s.parent_name == "Session"
            and "requests/sessions.py" in s.file_path]
    assert len(send) == 1, f"Session.send not found: {len(send)}"
    req = [s for s in flat if s.type == NodeType.METHOD and s.name == "request"
           and s.parent_name == "Session"
           and "requests/sessions.py" in s.file_path]
    assert len(req) == 1 and req[0].docstring, "Session.request docstring missing"

    assert sum(1 for s in flat if s.docstring) > 100, "too few docstrings captured"

    verify_every_span(root, walked)
    print(f"        requests: {n_files} files, {len(flat)} symbols, all spans verified")


def flask_messy_repo():
    from core.graph.models import NodeType
    from adapters.parsing.python_parser import PythonParser
    from adapters.vcs.github_scanner import GitHubScanner

    root = Path(GitHubScanner().clone("pallets/flask"))
    walked, flat = collect(PythonParser(), root)

    n_files = sum(1 for syms in walked.values() if syms)
    assert n_files >= 40, f"only {n_files} files parsed"
    assert len(flat) >= 600, f"only {len(flat)} symbols"

    flask_cls = [s for s in flat if s.type == NodeType.CLASS
                 and s.name == "Flask" and "flask/app.py" in s.file_path]
    assert len(flask_cls) == 1, f"real Flask class not found: {len(flask_cls)}"
    wsgi = [s for s in flat if s.type == NodeType.METHOD and s.name == "wsgi_app"
            and s.parent_name == "Flask" and "flask/app.py" in s.file_path]
    assert len(wsgi) == 1, "Flask.wsgi_app not found"

    # decorator-heavy repo: still zero spans starting on a decorator line
    verify_every_span(root, walked)
    print(f"        flask: {n_files} files, {len(flat)} symbols, all spans verified")


def main() -> int:
    print("== CodeGraph Agent - step 3 smoke test ==")
    check("parser satisfies Parser port", parser_satisfies_port)
    check("synthetic: structure, parents, docstrings, exact line numbers",
          synth_structure_and_lines)
    check("synthetic walk: junk dirs skipped, broken->[], empty handled",
          synth_walk_skips_junk_and_survives_broken)
    check("REAL requests: walk + Session.send + universal span verification",
          requests_clean_repo)
    check("REAL flask (messy): walk + Flask.wsgi_app + universal span verification",
          flask_messy_repo)
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print("\nAll green. Step 3 done. Next: step 4 "
          "(call resolution -> nodes+edges -> SQLite via SqliteGraphStore).")
    return 0


if __name__ == "__main__":
    sys.exit(main())