"""Git new-side changes and bounded Tree-sitter mutation candidates."""
from __future__ import annotations

import re
import subprocess
from collections import deque
from pathlib import Path

import tree_sitter_python
from tree_sitter import Language, Parser

from cartograph.graph.models import SymbolGraph, SymbolKind
from cartograph.review.models import ChangedFunction, Mutation


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args], cwd=root,
        capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def files_to_copy(root: Path) -> list[str]:
    raw = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted({x.decode("utf-8") for x in raw.split(b"\0") if x})


def diff_lines(root: Path, base: str) -> tuple[dict[str, set[int]], str, list[str]]:
    """Include staged/unstaged/new files; deleted lines are explicitly incomplete."""
    changed = git(root, "diff", "--name-only", "-z", "--no-renames", base, "--")
    new = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    new_names = {x.decode("utf-8") for x in new.split(b"\0") if x}
    names = sorted(name for name in (
        {x.decode("utf-8") for x in changed.split(b"\0") if x} | new_names
    ) if ".cartograph" not in Path(name).parts)
    lines: dict[str, set[int]] = {}
    patches, warnings = [], []
    for name in names:
        lines[name] = set()
        path = root / name
        if name in new_names:
            if path.is_file() and not path.is_symlink():
                text = path.read_text(encoding="utf-8", errors="replace")
                patches.append(f"New file: {name}\n{text}")
                if path.suffix == ".py":
                    lines[name] = set(range(1, len(text.splitlines()) + 1))
                else:
                    warnings.append(f"Non-Python change not analyzed: {name}")
            else:
                warnings.append(f"New symlink or missing file not analyzed: {name}")
            continue
        patch = git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                    "--unified=0", base, "--", f":(literal){name}").decode(
                        "utf-8", errors="replace")
        patches.append(patch)
        if path.suffix != ".py":
            warnings.append(f"Non-Python change not analyzed: {name}")
            continue
        affected: set[int] = set()
        for match in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE):
            old_count = int(match[2]) if match[2] is not None else 1
            count = int(match[4]) if match[4] is not None else 1
            affected.update(range(int(match[3]), int(match[3]) + count))
            if old_count and not count:
                warnings.append(f"Deletion-only hunk has no new-side execution evidence: {name}")
        lines[name] = affected
    return lines, "\n".join(patches), warnings


def is_test_file(name: str) -> bool:
    path = Path(name)
    return ("tests" in path.parts or path.name == "conftest.py"
            or path.name.startswith("test_") or path.name.endswith("_test.py"))


def caller_paths(graph: SymbolGraph, target: str, limit: int = 30) -> list[list[str]]:
    reverse: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.target_id:
            reverse.setdefault(edge.target_id, set()).add(edge.source_id)
    result: list[list[str]] = []
    seen = {target}
    queue = deque([[target]])
    while queue and len(result) < limit:
        path = queue.popleft()
        for caller in sorted(reverse.get(path[0], set())):
            if caller not in seen:
                seen.add(caller)
                next_path = [caller, *path]
                result.append(next_path)
                queue.append(next_path)
                if len(result) == limit:
                    break
    return result


def changed_functions(
    graph: SymbolGraph, lines: dict[str, set[int]],
) -> tuple[list[ChangedFunction], list[str]]:
    assigned: dict[str, list[int]] = {}
    warnings = []
    for file, numbers in lines.items():
        if is_test_file(file):
            warnings.append(f"Test/configuration file changed, not mutated: {file}")
            continue
        for line in sorted(numbers):
            candidates = [s for s in graph.symbols.values() if s.file_path == file
                          and s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
                          and s.line_start <= line <= s.line_end]
            if candidates:
                symbol = min(candidates, key=lambda s: s.line_end - s.line_start)
                assigned.setdefault(symbol.id, []).append(line)
            else:
                warnings.append(f"Changed line outside an indexed function: {file}:{line}")
    return [ChangedFunction(symbol=graph.symbols[sid], changed_lines=numbers,
                            caller_paths=caller_paths(graph, sid))
            for sid, numbers in sorted(assigned.items())], warnings


_SWAPS = {"+": "-", "-": "+", "==": "!=", "!=": "==", "<": ">=", "<=": ">",
          ">": "<=", ">=": "<", "True": "False", "False": "True"}


def candidates(root: Path, functions: list[ChangedFunction]) -> list[Mutation]:
    parser = Parser(Language(tree_sitter_python.language()))
    result: list[Mutation] = []
    for file in sorted({f.symbol.file_path for f in functions}):
        data = (root / file).read_bytes()
        tree = parser.parse(data)
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            stack.extend(reversed(node.children))
            if node.type not in ("true", "false", "+", "-", "==", "!=", "<", "<=", ">", ">="):
                continue
            old = data[node.start_byte:node.end_byte].decode("utf-8")
            if old not in _SWAPS:
                continue
            if node.type not in ("true", "false") and (
                node.parent is None or node.parent.type not in (
                    "binary_operator", "comparison_operator",
                )
            ):
                continue
            line = node.start_point.row + 1
            owner = next((f for f in functions if f.symbol.file_path == file
                          and line in f.changed_lines), None)
            if owner is None:
                continue
            replacement = _SWAPS[old]
            mutated = data[:node.start_byte] + replacement.encode() + data[node.end_byte:]
            try:
                compile(mutated, file, "exec")
            except (SyntaxError, ValueError):
                continue
            result.append(Mutation(id=f"M{len(result)+1:03d}", symbol_id=owner.symbol.id,
                                   file=file, line=line, start_byte=node.start_byte,
                                   end_byte=node.end_byte, original=old, replacement=replacement))
    return result
