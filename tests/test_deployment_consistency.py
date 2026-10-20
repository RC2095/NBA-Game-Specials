"""
Static checks that deployment files stay consistent with the code.

Motivation: the same class of bug hit this project THREE separate times
-- a new module was imported by main.py but never added to the
Dockerfile's COPY line (database.py, then player_markets.py, then
team_markets.py). Each build would have succeeded and each container
would have crashed on startup with ModuleNotFoundError, and each time it
was only caught by someone manually remembering to re-check. These tests
make that whole class of bug fail in CI instead of in production.

They use only `ast` and file reads -- no FastAPI import needed -- so
they're fast and have no environment dependencies.
"""

import ast
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The module uvicorn is pointed at in the Dockerfile's CMD (main:app).
ENTRYPOINT_MODULE = "main"

# Third-party packages that are imported directly but legitimately NOT
# listed in requirements.txt because another listed package depends on
# them. Each entry needs a reason, so this doesn't become a dumping
# ground that quietly defeats the check.
KNOWN_TRANSITIVE_IMPORTS = {
    "starlette": "installed as a dependency of fastapi",
}


# ============================================================
# HELPERS (take text/paths as arguments so they can also be tested
# against deliberately-broken inputs below)
# ============================================================

def _local_module_names(root: pathlib.Path) -> set:
    """Stems of every .py file directly in the project root (not tests/)."""
    return {path.stem for path in root.glob("*.py")}


def _imported_top_level_names(source: str) -> set:
    """Top-level package names imported anywhere in a module's source."""
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level > 0 means a relative import (from . import x)
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def _required_local_modules(root: pathlib.Path, entrypoint: str) -> set:
    """
    Transitive closure of local modules reachable from the entrypoint --
    i.e. every module that must exist in the container for
    `uvicorn main:app` to start.
    """
    local = _local_module_names(root)
    required = set()
    queue = [entrypoint]
    while queue:
        module = queue.pop()
        if module in required:
            continue
        required.add(module)
        source = (root / f"{module}.py").read_text()
        for imported in _imported_top_level_names(source) & local:
            queue.append(imported)
    return required


def _copied_py_files(dockerfile_text: str) -> set:
    """Every .py filename named as a source in any COPY instruction."""
    copied = set()
    for raw_line in dockerfile_text.splitlines():
        line = raw_line.strip()
        if not line.upper().startswith("COPY "):
            continue
        tokens = [t for t in line.split()[1:] if not t.startswith("--")]
        sources = tokens[:-1]  # last token is the destination
        copied.update(token for token in sources if token.endswith(".py"))
    return copied


def _requirement_names(requirements_text: str) -> set:
    names = set()
    for raw_line in requirements_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


def _pytest_pythonpath(ini_text: str) -> list:
    """The entries of the `pythonpath` option in a pytest.ini's [pytest] section."""
    import configparser

    parser = configparser.ConfigParser()
    parser.read_string(ini_text)
    return parser.get("pytest", "pythonpath", fallback="").split()


# ============================================================
# DOCKERFILE COPY LINE
# ============================================================

def test_dockerfile_copies_every_module_the_app_needs_to_start():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()
    required = {f"{m}.py" for m in _required_local_modules(PROJECT_ROOT, ENTRYPOINT_MODULE)}
    copied = _copied_py_files(dockerfile)

    missing = required - copied
    assert not missing, (
        f"Dockerfile COPY is missing {sorted(missing)}. The container "
        "would build fine and then crash on startup with "
        "ModuleNotFoundError. Add them to the COPY line."
    )


def test_entrypoint_module_is_discovered_and_nontrivial():
    # Guards the checker itself: if import discovery silently broke and
    # returned only {'main'}, the test above would pass vacuously.
    required = _required_local_modules(PROJECT_ROOT, ENTRYPOINT_MODULE)
    assert ENTRYPOINT_MODULE in required
    assert {"database", "player_markets", "team_markets"} <= required


def test_checker_detects_a_missing_module_in_a_broken_dockerfile():
    """
    The checker must be able to FAIL. This feeds it the exact broken
    Dockerfile state this project actually had at one point (a COPY line
    that predates team_markets.py) and confirms the missing module is
    reported -- otherwise a bug in the checker itself could make the
    real test pass forever without ever checking anything.
    """
    broken_dockerfile = (
        "FROM python:3.12-slim\n"
        "COPY main.py database.py player_markets.py odds_provider.py .\n"
    )
    required = {f"{m}.py" for m in _required_local_modules(PROJECT_ROOT, ENTRYPOINT_MODULE)}
    missing = required - _copied_py_files(broken_dockerfile)

    # The historical omission must be reported. Deliberately NOT an
    # equality check against {"team_markets.py"}: this fixture is a
    # frozen snapshot, so any module added to the app later would also
    # show up as "missing" from it, and an exact-match assertion would
    # then fail forever for a reason unrelated to the checker working.
    assert "team_markets.py" in missing
    assert missing <= required


def test_copy_parser_handles_flags_and_multiple_copy_lines():
    dockerfile = (
        "COPY requirements.txt .\n"
        "COPY --chown=app:app main.py database.py .\n"
        "copy extra.py /app/\n"
    )
    assert _copied_py_files(dockerfile) == {"main.py", "database.py", "extra.py"}


# ============================================================
# REQUIREMENTS.TXT
# ============================================================

def test_requirements_lists_every_third_party_package_the_app_imports():
    local = _local_module_names(PROJECT_ROOT)
    stdlib = set(sys.stdlib_module_names)
    requirements = _requirement_names((PROJECT_ROOT / "requirements.txt").read_text())

    # Only the files that actually ship in the image.
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()
    shipped = [PROJECT_ROOT / name for name in _copied_py_files(dockerfile)]

    third_party = set()
    for path in shipped:
        third_party |= _imported_top_level_names(path.read_text())
    third_party -= stdlib
    third_party -= local

    unaccounted = {
        name
        for name in third_party
        if name.lower().replace("_", "-") not in requirements
        and name not in KNOWN_TRANSITIVE_IMPORTS
    }
    assert not unaccounted, (
        f"Imported but not in requirements.txt: {sorted(unaccounted)}. "
        "The app would work in a dev environment that happens to have "
        "them installed and fail in a clean container."
    )


def test_requirements_checker_detects_a_missing_package():
    # The checker must be able to FAIL (same reasoning as the Dockerfile
    # checker's self-test above).
    requirements = _requirement_names("pydantic>=2.0,<3.0\nuvicorn[standard]>=0.30\n")
    assert "fastapi" not in requirements
    assert {"pydantic", "uvicorn"} <= requirements


def test_requirement_name_parsing_handles_extras_and_specifiers():
    parsed = _requirement_names(
        "fastapi>=0.115,<1.0\n"
        "uvicorn[standard]>=0.30,<1.0\n"
        "# a comment\n"
        "\n"
        "Some_Package==1.0\n"
    )
    assert parsed == {"fastapi", "uvicorn", "some-package"}


def test_known_transitive_imports_all_have_a_reason():
    for name, reason in KNOWN_TRANSITIVE_IMPORTS.items():
        assert reason.strip(), f"KNOWN_TRANSITIVE_IMPORTS[{name!r}] needs a reason"


# ============================================================
# PYTEST CAN FIND THE APP'S MODULES
# ============================================================

def test_pytest_ini_puts_the_project_root_on_the_import_path():
    """
    Without `pythonpath = .`, plain `pytest` fails to collect every test
    file that imports the app (ModuleNotFoundError: No module named
    'main'), because pytest only adds tests/ to sys.path by default.
    `python -m pytest` happens to work (it also adds the current
    directory), so the problem is easy to miss locally and then turns CI
    red. This test deliberately imports nothing from the app, so it
    still runs and names the root cause when everything else can't.
    """
    ini = PROJECT_ROOT / "pytest.ini"
    assert ini.exists(), (
        "pytest.ini is missing: plain `pytest` will fail to import main, "
        "database, etc. Add `[pytest]` with `pythonpath = .`"
    )
    assert "." in _pytest_pythonpath(ini.read_text())


def test_pythonpath_parser_detects_a_missing_option():
    # The checker must be able to FAIL.
    assert _pytest_pythonpath("[pytest]\ntestpaths = tests\n") == []
    assert _pytest_pythonpath("[pytest]\npythonpath = . src\n") == [".", "src"]
