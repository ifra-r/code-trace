"""Step 2 smoke test. Run FROM PROJECT ROOT: python check_step2.py
Real-network test: shallow-clones psf/requests ONCE into .codegraph/repos/,
reuses it afterwards. Exit code 0 = green."""

import sys
import time
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


def scanner_clone_and_reuse():
    from adapters.vcs.github_scanner import GitHubScanner

    s = GitHubScanner()
    path = s.clone("https://github.com/psf/requests")
    p = Path(path)
    assert p.is_dir(), f"clone dir missing: {p}"
    py_files = list(p.rglob("*.py"))
    assert len(py_files) > 10, f"expected many .py files, found {len(py_files)}"
    assert any(f.name == "sessions.py" for f in py_files), "sessions.py not found"
    t0 = time.time()
    assert s.clone("https://github.com/psf/requests") == path
    assert time.time() - t0 < 2, "second clone() re-cloned instead of reusing"


def scanner_rejects_garbage():
    from adapters.vcs.github_scanner import GitHubScanner

    try:
        GitHubScanner().clone("not a url at all")
    except ValueError:
        pass
    else:
        raise AssertionError("garbage URL must raise ValueError")


def api_health():
    from api.app import app
    from fastapi.testclient import TestClient

    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def api_clone_roundtrip():
    from api.app import app
    from fastapi.testclient import TestClient

    r = TestClient(app).post("/clone", json={"url": "https://github.com/psf/requests"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "requests" and Path(body["local_path"]).is_dir()


def api_bad_url_is_400():
    from api.app import app
    from fastapi.testclient import TestClient

    r = TestClient(app).post("/clone", json={"url": "definitely not a url"})
    assert r.status_code == 400, f"expected 400, got {r.status_code}"


def api_index_locked_as_501():
    from api.app import app
    from fastapi.testclient import TestClient

    r = TestClient(app).post("/index", json={"url": "https://github.com/psf/requests"})
    assert r.status_code == 501, f"expected 501 stub, got {r.status_code}"


def main() -> int:
    print("== CodeGraph Agent - step 2 smoke test ==")
    check("GitHubScanner.clone: real shallow clone of psf/requests", scanner_clone_and_reuse)
    check("GitHubScanner: garbage URL raises ValueError (no network)", scanner_rejects_garbage)
    check("API GET /health -> 200", api_health)
    check("API POST /clone -> 200 {name, local_path}", api_clone_roundtrip)
    check("API POST /clone bad url -> 400", api_bad_url_is_400)
    check("API POST /index locked as 501 until steps 3-5", api_index_locked_as_501)
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED: {FAILURES}")
        return 1
    print("\nAll green. Step 2 done. Next: step 3 (PythonParser — AST symbol extraction).")
    return 0


if __name__ == "__main__":
    sys.exit(main())