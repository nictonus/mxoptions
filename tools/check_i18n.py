#!/usr/bin/env python3
"""Check the Ukrainian catalogue against the source: strings without a translation, catalogue
entries nothing uses, and duplicated keys. Exits non-zero if a translation is missing.

    python3 tools/check_i18n.py
"""

import ast
import collections
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def literal_strings(tree):
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def main():
    catalogue_path = os.path.join(ROOT, "mxoptions", "i18n_uk.py")
    catalogue_tree = ast.parse(open(catalogue_path, encoding="utf-8").read())
    table = next(node.value for node in ast.walk(catalogue_tree)
                 if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "STRINGS")
    keys = [key.value for key in table.keys if isinstance(key, ast.Constant)]

    marked, mentioned = set(), set()
    for path in glob.glob(os.path.join(ROOT, "mxoptions", "**", "*.py"), recursive=True):
        if path == catalogue_path:
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        mentioned |= literal_strings(tree)
        for node in ast.walk(tree):  # _("literal")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                marked.add(node.args[0].value)

    missing = sorted(marked - set(keys))
    # labels kept in tables are translated with _(variable), so "used" means: appears anywhere as a literal
    unused = sorted(set(keys) - mentioned)
    duplicated = sorted(key for key, count in collections.Counter(keys).items() if count > 1)
    for title, items in (("missing a translation", missing), ("not used by any source file", unused),
                         ("defined more than once", duplicated)):
        print(f"{title}: {len(items)}")
        for item in items:
            print("   ", repr(item))
    return 1 if missing or duplicated else 0


if __name__ == "__main__":
    sys.exit(main())
