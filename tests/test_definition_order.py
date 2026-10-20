"""
Static check for names used before they are defined at module level.

Motivation: this exact mistake happened twice while building main.py --
a Pydantic model referenced a type (ThresholdPlayerInput, then
GameStatus) that was defined further down the file. `python -m
py_compile` passes on that code, because it only checks syntax; the
failure (NameError) happens at import time, when the class body runs and
Pydantic evaluates the annotation. In this project's development
environment there was no way to import main.py to notice, so both
instances were caught only by manually comparing line numbers.

This checker walks each module's top-level statements in order and
flags any name that is evaluated at definition time (class bases,
class-body annotations and defaults, decorators, function
annotations/defaults, module-level expressions) before the statement
that defines it. Code inside function bodies is deliberately ignored:
it runs at call time, when every module-level name already exists.

Uses only `ast` -- no FastAPI import needed.
"""

import ast
import builtins
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

MODULES_TO_CHECK = [
    "main",
    "database",
    "player_markets",
    "team_markets",
]


# ============================================================
# CHECKER
# ============================================================

def _loaded_names(expr_nodes) -> set:
    """
    Names read (Load context) anywhere inside the given expression
    nodes, excluding names the expressions bind for themselves
    (comprehension variables, lambda parameters).
    """
    loaded, bound = set(), set()
    for root in expr_nodes:
        if root is None:
            continue
        for node in ast.walk(root):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    loaded.add(node.id)
                else:
                    bound.add(node.id)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
    return loaded - bound


def _function_header_exprs(fn) -> list:
    """The parts of a def evaluated when the def statement runs."""
    args = fn.args
    exprs = list(fn.decorator_list)
    all_args = args.posonlyargs + args.args + args.kwonlyargs
    if args.vararg:
        all_args.append(args.vararg)
    if args.kwarg:
        all_args.append(args.kwarg)
    exprs += [a.annotation for a in all_args]
    exprs += list(args.defaults) + list(args.kw_defaults)
    exprs.append(fn.returns)
    return exprs


def _class_definition_time_exprs(cls) -> tuple:
    """
    (expressions evaluated when the class statement runs, names the
    class body defines for itself).
    """
    exprs = list(cls.bases) + [kw.value for kw in cls.keywords] + list(cls.decorator_list)
    local_names = set()
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign):
            exprs += [stmt.annotation, stmt.value]
            if isinstance(stmt.target, ast.Name):
                local_names.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            exprs.append(stmt.value)
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    local_names.add(target.id)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exprs += _function_header_exprs(stmt)
            local_names.add(stmt.name)
        elif isinstance(stmt, ast.Expr):
            exprs.append(stmt.value)  # docstrings etc.
    return exprs, local_names


def _names_defined_by(stmt) -> set:
    """Module-level names a statement makes available to later ones."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.Import):
        return {(a.asname or a.name).split(".")[0] for a in stmt.names}
    if isinstance(stmt, ast.ImportFrom):
        return {a.asname or a.name for a in stmt.names}
    names = set()
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            for node in ast.walk(target):
                if isinstance(node, ast.Name):
                    names.add(node.id)
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        if isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
    return names


def find_use_before_definition(source: str) -> list:
    """
    Returns a sorted list of (line, name, context) for every name that
    is read at definition time before the module-level statement that
    defines it.
    """
    tree = ast.parse(source)
    defined = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
    problems = []

    def check(exprs, line, context, extra_local=frozenset()):
        for name in _loaded_names(exprs):
            if name not in defined and name not in extra_local:
                problems.append((line, name, context))

    def visit(statements):
        for stmt in statements:
            if isinstance(stmt, ast.ClassDef):
                exprs, local_names = _class_definition_time_exprs(stmt)
                check(exprs, stmt.lineno, f"class {stmt.name}", local_names)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                check(_function_header_exprs(stmt), stmt.lineno, f"def {stmt.name}")
            elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                # e.g. `if __name__ == "__main__":` -- the test expression
                # runs now, and the body statements run in order.
                header = getattr(stmt, "test", None) or getattr(stmt, "iter", None)
                if header is not None:
                    check([header], stmt.lineno, "compound statement")
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(stmt, field, []))
                for handler in getattr(stmt, "handlers", []):
                    visit(handler.body)
                continue
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                pass
            else:
                check([stmt], stmt.lineno, "module-level statement")
            defined.update(_names_defined_by(stmt))

    visit(tree.body)
    return sorted(set(problems))


# ============================================================
# THE REAL MODULES
# ============================================================

def _check_module(name: str) -> list:
    return find_use_before_definition((PROJECT_ROOT / f"{name}.py").read_text())


def test_no_module_uses_a_name_before_defining_it():
    failures = {}
    for module in MODULES_TO_CHECK:
        problems = _check_module(module)
        if problems:
            failures[module] = problems

    assert not failures, (
        "Names used at definition time before being defined (this would "
        "raise NameError at import, which py_compile cannot detect):\n"
        + "\n".join(
            f"  {module}.py line {line}: '{name}' used in {context}"
            for module, problems in failures.items()
            for line, name, context in problems
        )
    )


def test_checker_actually_inspects_the_real_modules():
    # Guards against the checker silently checking nothing: main.py must
    # contain plenty of classes and decorated routes for this to mean
    # anything.
    tree = ast.parse((PROJECT_ROOT / "main.py").read_text())
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert len(classes) > 30
    assert len(functions) > 30


# ============================================================
# SELF-TESTS: the checker must be able to FAIL
# ============================================================

def test_detects_annotation_used_before_class_defined():
    source = (
        "class Outer:\n"
        "    inner: Inner\n"
        "\n"
        "class Inner:\n"
        "    pass\n"
    )
    assert [(p[1]) for p in find_use_before_definition(source)] == ["Inner"]


def test_detects_the_exact_historical_bug_pattern():
    """The real mistake: a type alias defined AFTER the model using it."""
    source = (
        "from typing import Literal\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class GameStatusUpdateRequest(BaseModel):\n"
        "    status: GameStatus\n"
        "\n"
        'GameStatus = Literal["scheduled", "postponed", "canceled"]\n'
    )
    problems = find_use_before_definition(source)
    assert [(p[1], p[2]) for p in problems] == [
        ("GameStatus", "class GameStatusUpdateRequest")
    ]


def test_detects_default_value_referencing_later_definition():
    source = (
        "def route(x: int = DEFAULT):\n"
        "    return x\n"
        "\n"
        "DEFAULT = 5\n"
    )
    assert [p[1] for p in find_use_before_definition(source)] == ["DEFAULT"]


def test_detects_decorator_using_later_definition():
    source = (
        "@register\n"
        "def handler():\n"
        "    pass\n"
        "\n"
        "def register(fn):\n"
        "    return fn\n"
    )
    assert [p[1] for p in find_use_before_definition(source)] == ["register"]


def test_detects_module_level_expression_using_later_definition():
    source = "VALUE = HELPER + 1\nHELPER = 2\n"
    assert [p[1] for p in find_use_before_definition(source)] == ["HELPER"]


def test_correct_ordering_produces_no_problems():
    source = (
        "from typing import Literal, Optional\n"
        "\n"
        'Status = Literal["a", "b"]\n'
        "\n"
        "class Model:\n"
        "    status: Optional[Status] = None\n"
        "    other: 'Model2' = None\n"
        "\n"
        "def route(s: Status, m: Model):\n"
        "    return Later()\n"  # function BODY: runs at call time, fine
        "\n"
        "class Later:\n"
        "    pass\n"
    )
    assert find_use_before_definition(source) == []


def test_function_body_references_to_later_names_are_allowed():
    source = (
        "def early():\n"
        "    return late()\n"
        "\n"
        "def late():\n"
        "    return 1\n"
    )
    assert find_use_before_definition(source) == []


def test_class_body_can_reference_its_own_earlier_attributes():
    source = (
        "class Config:\n"
        "    BASE = 10\n"
        "    DOUBLE = BASE * 2\n"
    )
    assert find_use_before_definition(source) == []


def test_comprehension_and_lambda_variables_are_not_false_positives():
    source = (
        "VALUES = [x * 2 for x in range(3)]\n"
        "KEY = lambda item: item[0]\n"
    )
    assert find_use_before_definition(source) == []


def test_name_used_in_main_guard_block_is_checked_in_order():
    source = (
        'if __name__ == "__main__":\n'
        "    run(app)\n"
        "\n"
        "app = 1\n"
        "def run(a):\n"
        "    pass\n"
    )
    flagged = {p[1] for p in find_use_before_definition(source)}
    assert flagged == {"run", "app"}


def test_real_main_py_with_the_historical_bug_reintroduced_is_flagged():
    """
    Take the REAL main.py source and move `GameStatus`'s definition to
    the end of the file, recreating the bug as it actually occurred --
    the checker must flag it, proving it works on the real file's
    structure and not just on toy examples.
    """
    source = (PROJECT_ROOT / "main.py").read_text()
    definition = 'GameStatus = Literal["scheduled", "postponed", "canceled"]\n'
    assert definition in source, "test needs updating: GameStatus definition moved"

    mutated = source.replace(definition, "") + "\n" + definition

    flagged = {p[1] for p in find_use_before_definition(mutated)}
    assert "GameStatus" in flagged
