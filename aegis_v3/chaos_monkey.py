"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
aegis_v3/chaos_monkey.py — Chaos Engineering Fault Injector
==============================================================================

The Chaos Monkey intentionally injects bugs into the target application's
source code to prove that Aegis V3's resilience pipeline is battle-tested.
Instead of waiting for infrastructure to crash, we actively inject defects 
under controlled conditions to observe the system's self-healing capabilities.

Bug Categories:
  1. ARITHMETIC     — Flips a binary operator (+ <-> -, * <-> /)
  2. RETURN_NONE    — Strips the return value from a return statement
  3. OFF_BY_ONE     — Mutates a range() stop argument by subtracting 1
  4. WRONG_KEY      — Corrupts a dict key string (appends '_CORRUPTED')
  5. ZERO_DIVISION  — Forces a division by zero by replacing the denominator with 0
  6. SYNTAX_ERROR   — Deliberately appends invalid syntax tokens

All mutations (except SYNTAX_ERROR) are done via Python's AST module to ensure
syntactic validity. The original code is saved for safe recovery and rollback.
==============================================================================
"""
from __future__ import annotations

import ast
import datetime
import logging
import os
import random
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("aegis.chaos_monkey")

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BugType(str, Enum):
    ARITHMETIC    = "ARITHMETIC"
    RETURN_NONE   = "RETURN_NONE"
    OFF_BY_ONE    = "OFF_BY_ONE"
    WRONG_KEY     = "WRONG_KEY"
    ZERO_DIVISION = "ZERO_DIVISION"
    SYNTAX_ERROR  = "SYNTAX_ERROR"


class ChaosEvent(BaseModel):
    """Immutable record of an injected chaos mutation."""
    chaos_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    target_file: str
    bug_type: BugType
    original_content: str
    mutated_content: str
    injection_line: int
    injection_description: str
    injected_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")


# ---------------------------------------------------------------------------
# AST Mutation Visitors
# ---------------------------------------------------------------------------

class ArithmeticMutator(ast.NodeTransformer):
    """Flips one binary arithmetic operator in the AST."""

    FLIP_MAP = {
        ast.Add: ast.Sub,
        ast.Sub: ast.Add,
        ast.Mult: ast.Div,
        ast.Div: ast.Mult,
    }

    def __init__(self) -> None:
        super().__init__()
        self.mutation_applied = False
        self.mutation_line = -1
        self.description = ""
        self._candidates: list[ast.BinOp] = []

    def visit_BinOp(self, node: ast.BinOp) -> ast.BinOp:
        if type(node.op) in self.FLIP_MAP:
            self._candidates.append(node)
        self.generic_visit(node)
        return node

    def apply(self, tree: ast.AST) -> bool:
        self.visit(tree)
        if not self._candidates:
            return False

        target = random.choice(self._candidates)
        original_op = type(target.op).__name__
        new_op_cls = self.FLIP_MAP[type(target.op)]
        target.op = new_op_cls()

        self.mutation_applied = True
        self.mutation_line = target.lineno
        self.description = (
            f"Arithmetic operator flipped: {original_op} -> {new_op_cls.__name__} "
            f"at line {self.mutation_line}"
        )
        return True


class ReturnNoneMutator(ast.NodeTransformer):
    """Removes the return value from one return statement (makes it return None)."""

    def __init__(self) -> None:
        super().__init__()
        self.mutation_applied = False
        self.mutation_line = -1
        self.description = ""
        self._candidates: list[ast.Return] = []

    def visit_Return(self, node: ast.Return) -> ast.Return:
        if node.value is not None:
            self._candidates.append(node)
        return node

    def apply(self, tree: ast.AST) -> bool:
        self.visit(tree)
        if not self._candidates:
            return False

        target = random.choice(self._candidates)
        self.mutation_line = target.lineno
        original = ast.unparse(target.value) if target.value else "None"
        target.value = None

        self.mutation_applied = True
        self.description = (
            f"Return value stripped at line {self.mutation_line}: "
            f"`return {original}` -> `return`"
        )
        return True


class OffByOneMutator(ast.NodeTransformer):
    """Decrements the stop argument of range() calls by 1."""

    def __init__(self) -> None:
        super().__init__()
        self.mutation_applied = False
        self.mutation_line = -1
        self.description = ""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if (
            not self.mutation_applied
            and isinstance(node.func, ast.Name)
            and node.func.id == "range"
            and len(node.args) >= 1
        ):
            stop_idx = len(node.args) - 1
            stop_node = node.args[stop_idx]
            original = ast.unparse(stop_node)

            node.args[stop_idx] = ast.BinOp(
                left=stop_node,
                op=ast.Sub(),
                right=ast.Constant(value=1),
            )
            ast.fix_missing_locations(node)

            self.mutation_applied = True
            self.mutation_line = node.lineno
            self.description = (
                f"Off-by-one injected at line {self.mutation_line}: "
                f"range(..., {original}) -> range(..., {original} - 1)"
            )

        self.generic_visit(node)
        return node


class WrongKeyMutator(ast.NodeTransformer):
    """Corrupts one dictionary string key by appending '_CORRUPTED'."""

    def __init__(self) -> None:
        super().__init__()
        self.mutation_applied = False
        self.mutation_line = -1
        self.description = ""
        self._candidates: list[ast.Subscript] = []

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        if (
            isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and len(node.slice.value) > 0
        ):
            self._candidates.append(node)
        self.generic_visit(node)
        return node

    def apply(self, tree: ast.AST) -> bool:
        self.visit(tree)
        if not self._candidates:
            return False

        target = random.choice(self._candidates)
        original_key = target.slice.value
        target.slice.value = original_key + "_CORRUPTED"

        self.mutation_applied = True
        self.mutation_line = target.lineno
        self.description = (
            f"Dict key corrupted at line {self.mutation_line}: "
            f'"{original_key}" -> "{original_key}_CORRUPTED"'
        )
        return True


class ZeroDivisionMutator(ast.NodeTransformer):
    """Forces a division by zero by replacing a non-zero denominator with 0."""

    def __init__(self) -> None:
        super().__init__()
        self.mutation_applied = False
        self.mutation_line = -1
        self.description = ""

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        if (
            not self.mutation_applied
            and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod))
        ):
            original_right = ast.unparse(node.right)
            node.right = ast.Constant(value=0)
            ast.fix_missing_locations(node)

            self.mutation_applied = True
            self.mutation_line = node.lineno
            self.description = (
                f"Zero-division injected at line {self.mutation_line}: "
                f"denominator `{original_right}` replaced with `0`"
            )

        self.generic_visit(node)
        return node


# ---------------------------------------------------------------------------
# ChaosMonkey Engine
# ---------------------------------------------------------------------------

class ChaosMonkey:
    """Orchestrates the chaos engineering injection cycle for Aegis V3."""

    def __init__(self, repo_root: str) -> None:
        self.repo_root = os.path.abspath(repo_root)
        self._active_events: list[ChaosEvent] = []
        
        self.skip_dirs = {
            "__pycache__", ".git", ".chroma_db", ".venv", "venv",
            "aegis_v2", "aegis_v3", "tests", ".pytest_cache"
        }
        self.skip_files = {
            "main.py", "run_aegis_v2.py", "run_aegis_v3.py", "conftest.py",
            "test_dummy_app.py", "test_payment.py", "test_cart.py", "test_auth.py",
            "setup.py"
        }

    def inject_bug(
        self,
        target_dir: str,
        preferred_bug_type: Optional[BugType] = None,
    ) -> Optional[ChaosEvent]:
        """
        Select a random eligible file and inject a random bug mutation.
        """
        abs_dir = os.path.join(self.repo_root, target_dir)
        candidates = self._find_eligible_files(abs_dir)

        if not candidates:
            logger.error(f"No eligible Python files found in '{abs_dir}' for chaos injection.")
            return None

        target_file = random.choice(candidates)
        rel_path = os.path.relpath(target_file, self.repo_root).replace("\\", "/")
        logger.warning(f"🐒 CHAOS MONKEY targeting: {rel_path}")

        try:
            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                original_content = f.read()
            ast.parse(original_content)
        except Exception as e:
            logger.error(f"Failed to read/parse eligible file {target_file}: {e}")
            return None

        bug_types = (
            [preferred_bug_type] if preferred_bug_type
            else random.sample(list(BugType), len(list(BugType)))
        )

        for bug_type in bug_types:
            result = self._apply_mutation(original_content, bug_type)
            if result is not None:
                mutated_content, injection_line, description = result

                # Write mutation to disk
                with open(target_file, "w", encoding="utf-8") as f:
                    f.write(mutated_content)

                event = ChaosEvent(
                    target_file=rel_path,
                    bug_type=bug_type,
                    original_content=original_content,
                    mutated_content=mutated_content,
                    injection_line=injection_line,
                    injection_description=description,
                )
                self._active_events.append(event)
                logger.warning(f"🐒 CHAOS INJECTED [{bug_type.value}]: {description} in {rel_path}")
                return event

        logger.error(f"Failed to apply any bug mutations to {rel_path}")
        return None

    def restore(self, event: ChaosEvent) -> bool:
        """Restore mutated file to original state."""
        abs_path = os.path.join(self.repo_root, event.target_file)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(event.original_content)
            if event in self._active_events:
                self._active_events.remove(event)
            logger.info(f"🐒 Restored original content for {event.target_file}")
            return True
        except Exception as exc:
            logger.error(f"Failed to restore {event.target_file}: {exc}")
            return False

    def _find_eligible_files(self, abs_dir: str) -> list[str]:
        eligible = []
        for root, dirs, files in os.walk(abs_dir):
            dirs[:] = [d for d in dirs if d not in self.skip_dirs]
            for file in files:
                if file in self.skip_files or not file.endswith(".py"):
                    continue
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    ast.parse(content)
                    if len(content.strip()) > 100:
                        eligible.append(filepath)
                except Exception:
                    pass
        return eligible

    def _apply_mutation(
        self,
        content: str,
        bug_type: BugType,
    ) -> Optional[Tuple[str, int, str]]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return None

        if bug_type == BugType.ARITHMETIC:
            mutator = ArithmeticMutator()
            if not mutator.apply(tree):
                return None
            return ast.unparse(tree), mutator.mutation_line, mutator.description

        elif bug_type == BugType.RETURN_NONE:
            mutator = ReturnNoneMutator()
            if not mutator.apply(tree):
                return None
            return ast.unparse(tree), mutator.mutation_line, mutator.description

        elif bug_type == BugType.OFF_BY_ONE:
            mutator = OffByOneMutator()
            mutator.visit(tree)
            if not mutator.mutation_applied:
                return None
            return ast.unparse(tree), mutator.mutation_line, mutator.description

        elif bug_type == BugType.WRONG_KEY:
            mutator = WrongKeyMutator()
            if not mutator.apply(tree):
                return None
            return ast.unparse(tree), mutator.mutation_line, mutator.description

        elif bug_type == BugType.ZERO_DIVISION:
            mutator = ZeroDivisionMutator()
            mutator.visit(tree)
            if not mutator.mutation_applied:
                return None
            return ast.unparse(tree), mutator.mutation_line, mutator.description

        elif bug_type == BugType.SYNTAX_ERROR:
            lines = content.splitlines()
            if len(lines) < 3:
                return None
            mid = len(lines) // 2
            target_line = lines[mid]
            if not target_line.strip() or target_line.strip().startswith("#"):
                return None
            lines[mid] = target_line + " @@@SYNTAX_ERROR@@@"
            mutated = "\n".join(lines)
            return mutated, mid + 1, f"Syntax error injected at line {mid + 1}"

        return None
