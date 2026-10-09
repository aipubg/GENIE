#!/usr/bin/env python3
"""Lightweight AST check for duplicate dictionary literal keys in backend source.

Returns exit code 1 if any duplicate keys are found, 0 otherwise.
"""

import ast
import sys
from pathlib import Path


class DictKeyVisitor(ast.NodeVisitor):
    def __init__(self):
        self.errors: list[str] = []

    def visit_Dict(self, node: ast.Dict) -> None:
        seen: set[str] = set()
        for k in node.keys:
            if k is None:
                continue  # **spread
            key_repr = ""
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                key_repr = k.value
            elif isinstance(k, ast.Str):  # Python <3.8 compat
                key_repr = k.s
            elif isinstance(k, ast.Constant) and isinstance(k.value, (int, float, bool, type(None))):
                key_repr = repr(k.value)
            else:
                continue  # dynamic keys are not checkable
            if key_repr in seen:
                self.errors.append(f"  Duplicate key {key_repr!r}")
            seen.add(key_repr)
        self.generic_visit(node)


def check_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        return [f"  Syntax error: {e}"]
    visitor = DictKeyVisitor()
    visitor.visit(tree)
    return visitor.errors


def main() -> int:
    roots = [Path("core"), Path("models"), Path("director"), Path("voice"), Path("memory")]
    total_errors = 0
    for root in roots:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            errs = check_file(py_file)
            if errs:
                print(f"{py_file}:")
                for e in errs:
                    print(e)
                total_errors += len(errs)
    if total_errors:
        print(f"\nFAIL: {total_errors} duplicate dict key(s) found.")
        return 1
    print("OK: no duplicate dict literal keys found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
