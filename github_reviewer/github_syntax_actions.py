"""GitHub naming/style and cross-language convention checks."""

import ast
import base64
import difflib
import re

import requests
import config
from requests.exceptions import HTTPError

class GitHubSyntaxActions:
    @staticmethod
    def _identifier_words(identifier: str) -> set[str]:
            """Split an identifier into lowercase lexical tokens for heuristic comparisons."""
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier.replace("_", " "))
            return {token.lower() for token in re.findall(r"[A-Za-z]+", normalized) if token}

    @staticmethod
    def _is_pascal_case(name: str) -> bool:
            """Return True when a symbol follows PascalCase."""
            return bool(re.fullmatch(r"[A-Z][A-Za-z0-9]*", name))

    @staticmethod
    def _is_camel_case(name: str) -> bool:
            """Return True when a symbol follows camelCase."""
            return bool(re.fullmatch(r"[a-z][A-Za-z0-9]*", name)) and "_" not in name

    @staticmethod
    def _is_snake_case(name: str) -> bool:
            """Return True when a symbol follows snake_case."""
            return bool(re.fullmatch(r"[a-z_][a-z0-9_]*", name))

    def _extract_leading_comment(self, lines: list[str], line_number: int) -> str:
            """Collect the closest leading JS/TS comment block immediately above a declaration."""
            index = max(0, line_number - 2)
            collected: list[str] = []
            while index >= 0:
                stripped = lines[index].strip()
                if not stripped:
                    if collected:
                        break
                    index -= 1
                    continue
                if stripped.startswith("//"):
                    collected.insert(0, stripped[2:].strip())
                    index -= 1
                    continue
                if stripped.endswith("*/"):
                    block_lines: list[str] = []
                    while index >= 0:
                        block = lines[index].strip()
                        cleaned = block.replace("/**", "").replace("/*", "").replace("*/", "").lstrip("*").strip()
                        if cleaned:
                            block_lines.insert(0, cleaned)
                        if block.startswith("/**") or block.startswith("/*"):
                            break
                        index -= 1
                    collected = block_lines + collected
                break
            return " ".join(part for part in collected if part).strip()

    def _comment_matches_identifier(self, comment_text: str, identifier: str) -> bool:
            """Heuristically decide whether a comment/docstring still matches a symbol name."""
            if not comment_text:
                return True
            comment_words = {word.lower() for word in re.findall(r"[A-Za-z]+", comment_text)}
            identifier_words = self._identifier_words(identifier)
            if not comment_words or not identifier_words:
                return True
            if comment_words & identifier_words:
                return True
            action_words = {
                "add", "build", "check", "classify", "create", "delete", "extract",
                "fetch", "find", "format", "get", "handle", "list", "load", "post",
                "render", "replace", "resolve", "review", "save", "send", "start",
                "stop", "update", "validate",
            }
            comment_actions = comment_words & action_words
            identifier_actions = identifier_words & action_words
            if comment_actions and identifier_actions:
                return bool(comment_actions & identifier_actions)
            return True

    def _check_python_conventions(
            self,
            file_path: str,
            content: str,
            check_naming: bool,
            check_comment_presence: bool,
            check_comment_accuracy: bool,
        ) -> list[dict]:
            """Inspect Python classes and functions for naming and docstring issues."""
            issues: list[dict] = []
            try:
                tree = ast.parse(content)
            except SyntaxError as exc:
                issues.append({
                    "file": file_path,
                    "line": max(1, getattr(exc, "lineno", 1) or 1),
                    "message": f"Unable to parse Python file for convention checks: {exc.msg}",
                    "category": "parse_error",
                })
                return issues
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and check_naming and not self._is_pascal_case(node.name):
                    issues.append({
                        "file": file_path,
                        "line": node.lineno,
                        "message": f"Class '{node.name}' should use PascalCase.",
                        "category": "naming",
                    })
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                is_dunder = node.name.startswith("__") and node.name.endswith("__")
                if check_naming and not is_dunder and not self._is_snake_case(node.name):
                    issues.append({
                        "file": file_path,
                        "line": node.lineno,
                        "message": f"Python function '{node.name}' should use snake_case.",
                        "category": "naming",
                    })
                docstring = ast.get_docstring(node)
                if check_comment_presence and not docstring:
                    issues.append({
                        "file": file_path,
                        "line": node.lineno,
                        "message": f"Function '{node.name}' is missing a docstring or leading comment.",
                        "category": "documentation",
                    })
                elif check_comment_accuracy and docstring and not self._comment_matches_identifier(docstring, node.name):
                    issues.append({
                        "file": file_path,
                        "line": node.lineno,
                        "message": f"The docstring for '{node.name}' may not match the function's actual purpose.",
                        "category": "comment_accuracy",
                    })
            return issues

    def _check_javascript_conventions(
            self,
            file_path: str,
            content: str,
            check_naming: bool,
            check_comment_presence: bool,
            check_comment_accuracy: bool,
        ) -> list[dict]:
            """Inspect JavaScript and TypeScript declarations for naming and comment issues."""
            issues: list[dict] = []
            lines = content.splitlines()
            class_pattern = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)")
            function_patterns = [
                re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
                re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\([^=]*\)\s*=>"),
                re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?function\s*\("),
                re.compile(r"^\s*(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;=]*\)\s*\{"),
            ]
            disallowed_method_names = {"if", "for", "while", "switch", "catch", "constructor"}
            for line_number, line in enumerate(lines, start=1):
                class_match = class_pattern.match(line)
                if class_match:
                    class_name = class_match.group(1)
                    if check_naming and not self._is_pascal_case(class_name):
                        issues.append({
                            "file": file_path,
                            "line": line_number,
                            "message": f"Class '{class_name}' should use PascalCase.",
                            "category": "naming",
                        })
                    continue
                for pattern in function_patterns:
                    match = pattern.match(line)
                    if not match:
                        continue
                    function_name = match.group(1)
                    if function_name in disallowed_method_names:
                        break
                    if check_naming and not self._is_camel_case(function_name):
                        issues.append({
                            "file": file_path,
                            "line": line_number,
                            "message": f"JavaScript/TypeScript function '{function_name}' should use camelCase.",
                            "category": "naming",
                        })
                    comment_text = self._extract_leading_comment(lines, line_number)
                    if check_comment_presence and not comment_text:
                        issues.append({
                            "file": file_path,
                            "line": line_number,
                            "message": f"Function '{function_name}' is missing a leading comment or JSDoc block.",
                            "category": "documentation",
                        })
                    elif check_comment_accuracy and comment_text and not self._comment_matches_identifier(comment_text, function_name):
                        issues.append({
                            "file": file_path,
                            "line": line_number,
                            "message": f"The leading comment for '{function_name}' may not match the implementation.",
                            "category": "comment_accuracy",
                        })
                    break
            return issues

    def check_universal_coding_conventions(
            self,
            repo: str,
            head_sha: str,
            files: list,
            enabled_check_ids: set[str],
        ) -> dict:
            """Review changed source files for naming, documentation, and comment-accuracy issues."""
            run_naming = not enabled_check_ids or bool(enabled_check_ids & {
                "universal_naming_conventions", "naming_conventions", "coding_conventions"
            })
            run_comment_presence = not enabled_check_ids or bool(enabled_check_ids & {
                "function_documentation", "documentation_comments", "coding_conventions"
            })
            run_comment_accuracy = not enabled_check_ids or bool(enabled_check_ids & {
                "comment_accuracy", "coding_conventions"
            })
            issues: list[dict] = []
            files_reviewed: list[str] = []
            source_suffixes = (".py", ".js", ".jsx", ".ts", ".tsx")
            for file_obj in files:
                file_path = file_obj.get("filename", "")
                if not file_path.endswith(source_suffixes):
                    continue
                try:
                    content = self.get_file_content_at_ref(repo, file_path, head_sha)
                except Exception:
                    continue
                if not content:
                    continue
                files_reviewed.append(file_path)
                if file_path.endswith(".py"):
                    issues.extend(
                        self._check_python_conventions(
                            file_path,
                            content,
                            run_naming,
                            run_comment_presence,
                            run_comment_accuracy,
                        )
                    )
                else:
                    issues.extend(
                        self._check_javascript_conventions(
                            file_path,
                            content,
                            run_naming,
                            run_comment_presence,
                            run_comment_accuracy,
                        )
                    )
            reviewed_items = []
            if run_naming:
                reviewed_items.append("class naming (PascalCase) and function naming by language")
            if run_comment_presence:
                reviewed_items.append("function comments/docstrings presence")
            if run_comment_accuracy:
                reviewed_items.append("comment-to-function accuracy")
            return {
                "issues": issues,
                "total_issues": len(issues),
                "files_reviewed": files_reviewed,
                "reviewed_items": reviewed_items,
            }
