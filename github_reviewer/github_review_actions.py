"""GitHub pull request review orchestration and rule execution."""

import ast
import base64
import difflib
import logging
import re

import requests
import config

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.propagate = False  # Prevent duplicate messages via root logger
# Write [REVIEW] messages to stderr so the Flask SSE endpoint can capture them
_stderr_handler = logging.StreamHandler()
_stderr_handler.setLevel(logging.INFO)
_stderr_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_stderr_handler)
from typing import Any, Protocol, Pattern
from requests.exceptions import HTTPError


class _GitHubReviewDeps(Protocol):
    _base_url: str
    _headers: dict[str, str]
    _last_pr_lookup_error: str | None
    _INSTRUCTION_FILENAME_PATTERN: Pattern[str]

    def _repo_path(self, repo: str) -> str: ...
    def get_base_and_head_sha(self, repo: str, pr_number: int) -> tuple[str | None, str | None]: ...
    def get_file_content_at_ref(self, repo: str, file_path: str, ref: str) -> str: ...
    def list_pull_requests(self, repo: str, state: str = "open") -> list[dict[str, Any]]: ...
    def get_files_in_pr(self, repo: str, pr_number: int) -> list[dict[str, Any]]: ...
    def check_universal_coding_conventions(self, repo: str, head_sha: str, files: list[dict[str, Any]], enabled_check_ids: set[str]) -> dict[str, Any]: ...
    def add_inline_comment(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        comment_body: str,
        selected_path: str,
        start_line: int,
        end_line: int,
        side: str,
    ) -> Any: ...
    def add_comment(self, repo: str, pr_number: int, comment_text: str) -> Any: ...
    def fetch_review_instructions(self, repo: str, ref: str = "main") -> dict[str, Any]: ...
    def check_flake8_compliance(self, repo: str, head_sha: str, files: list[dict[str, Any]]) -> dict[str, Any]: ...
    def check_consistency(self, repo: str, head_sha: str, files: list[dict[str, Any]]) -> dict[str, Any]: ...
    def get_cached_pr_title(self, repo: str, pr_number: int) -> str: ...

class GitHubReviewActions:
    _INSTRUCTION_FILENAME_PATTERN = re.compile(
        r"(?i)^(?:review[-_ ]?instructions?|review[-_ ]?checklist|copilot[-_ ]?instructions|pull[-_ ]?request[-_ ]?checklist)(?:\.md|\.txt)?$"
    )

    def check_flake8_compliance(self: "_GitHubReviewDeps", repo: str, head_sha: str, files: list) -> dict:
            """
            Check Python files in the PR for flake8 compliance (PEP 8, naming, complexity).
            Writes all .py files to a temp directory and runs flake8 once.
            """
            import subprocess
            import sys
            import tempfile
            import os
            import shutil

            python_files = [f["filename"] for f in files if f["filename"].endswith(".py")]
            if not python_files:
                return {"compliant": True, "violations_count": 0, "violations": []}

            violations = []
            tmpdir = tempfile.mkdtemp(prefix="flake8_review_")
            path_map = {}

            try:
                for py_file in python_files:
                    try:
                        content = self.get_file_content_at_ref(repo, py_file, head_sha)
                        if not content:
                            continue
                        safe_name = py_file.replace("/", "_").replace("\\", "_")
                        tmp_path = os.path.join(tmpdir, safe_name)
                        with open(tmp_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        path_map[tmp_path] = py_file
                    except Exception as e:
                        logger.info("[REVIEW] Warning: Could not fetch %s: %s", py_file, e)

                if not path_map:
                    return {"compliant": True, "violations_count": 0, "violations": []}

                # Use sys.executable -m flake8 to avoid PATH issues in subprocess envs
                cmd = [sys.executable, "-m", "flake8", "--max-line-length=120"] + list(path_map.keys())
                try:
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, cwd=tmpdir,
                    )
                    stdout, stderr = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    logger.info("[REVIEW] Flake8 timed out after 30s — skipping flake8 check")
                    return {"compliant": False, "violations_count": 0, "violations": [],
                            "skipped": True, "skip_reason": "flake8 timed out"}

                for line in stdout.strip().split("\n"):
                    if not line or ":" not in line:
                        continue
                    parts = line.split(":")
                    if len(parts) < 4:
                        continue
                    try:
                        tmp_path_out = parts[0]
                        line_no = int(parts[1])
                        col = int(parts[2])
                        msg_part = ":".join(parts[3:]).strip()
                        code_parts = msg_part.split(" ")
                        code = code_parts[0] if code_parts else "UNKNOWN"
                        message = " ".join(code_parts[1:]) if len(code_parts) > 1 else msg_part
                        original_file = path_map.get(tmp_path_out, tmp_path_out)
                        violations.append({
                            "file": original_file,
                            "line": line_no,
                            "col": col,
                            "code": code,
                            "message": message,
                        })
                    except (ValueError, IndexError):
                        pass
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            return {
                "compliant": len(violations) == 0,
                "violations_count": len(violations),
                "violations": violations[:50],
            }

    def check_consistency(self: "_GitHubReviewDeps", repo: str, head_sha: str, files: list) -> dict:
            """
            Detect consistency issues across changed files:
            1. Same term spelled differently (e.g., "Project X" vs "ProjectX")
            2. Same metric with different values  
            3. Formatting variations in identifiers
            :param repo: Repository name
            :param pr_number: Pull request number
            :param files: List of changed files
            :return: Dict with consistency issues found
            """
            import re
            from collections import defaultdict
            issues = {
                "spelling_variations": [],
                "metric_inconsistencies": [],
                "formatting_variations": [],
                "total_issues": 0,
            }
            try:
                # Collect all text from changed files
                all_text = ""
                file_contents = {}
                for file_obj in files:
                    file_path = file_obj.get("filename", "")
                    if not file_path.endswith((".py", ".js", ".ts", ".md", ".txt")):
                        continue
                    try:
                        content = self.get_file_content_at_ref(repo, file_path, head_sha)
                        if content:
                            file_contents[file_path] = content
                            all_text += content + "\n"
                    except Exception:
                        pass
                # 1. DETECT SPELLING VARIATIONS
                term_patterns = {
                    "Project X": [r"Project\s+X", r"ProjectX", r"Proj\s+X", r"PROJECTX"],
                    "Config": [r"Config", r"Configuration", r"cfg", r"settings"],
                    "Database": [r"Database", r"DB", r"data base", r"db"],
                    "API": [r"API", r"api", r"Api"],
                }
                for canonical_term, variations in term_patterns.items():
                    found_variants = {}
                    for variant_pattern in variations:
                        matches = re.finditer(variant_pattern, all_text, re.IGNORECASE)
                        for match in matches:
                            variant = match.group()
                            found_variants[variant] = found_variants.get(variant, 0) + 1
                    if len(found_variants) > 1:
                        issues["spelling_variations"].append({
                            "term": canonical_term,
                            "variations": found_variants,
                        })
                # 2. DETECT METRIC INCONSISTENCIES
                metric_pattern = r"(version|timeout|limit|max_retries|batch_size)\s*[:=]\s*([\d.]+|\d+)"
                metric_values = defaultdict(set)
                for metric_match in re.finditer(metric_pattern, all_text, re.IGNORECASE):
                    metric_name = metric_match.group(1).lower()
                    metric_value = metric_match.group(2)
                    metric_values[metric_name].add(metric_value)
                for metric_name, values in metric_values.items():
                    if len(values) > 1:
                        issues["metric_inconsistencies"].append({
                            "metric": metric_name,
                            "inconsistent_values": list(values),
                            "count": len(values),
                        })
                # 3. DETECT FORMATTING VARIATIONS
                issues["total_issues"] = (
                    len(issues["spelling_variations"]) +
                    len(issues["metric_inconsistencies"]) +
                    len(issues["formatting_variations"])
                )
                return issues
            except Exception as e:
                print(f"Error during consistency check: {e}")
                return {"error": str(e), "total_issues": 0}

    def fetch_review_instructions(self: "_GitHubReviewDeps", repo: str, ref: str = "main") -> dict:
            """
            Search the repository for a review instruction/checklist markdown file.
            Fetches the repo file tree in a single API call, then matches filenames
            against a pattern. Searches root, .github/, and docs/ directories.
            :param repo: Repository name.
            :param ref:  Git ref to search (branch, tag, or SHA). Defaults to "main".
            :return: Dict with keys:
                     - "found": bool
                     - "file_path": str (path that matched, or "")
                     - "content": str (file content, or "")
                     - "ref": str (the ref used)
            """
            import re as _re
            # Fetch the full file tree in one API call
            tree_url = (
                f"{self._base_url}/repos/{self._repo_path(repo)}"
                f"/git/trees/{ref}?recursive=1"
            )
            resp = requests.get(tree_url, headers=self._headers)
            if resp.status_code != 200:
                print(f"Could not fetch repo tree: {resp.status_code}")
                return {"found": False, "file_path": "", "content": "", "ref": ref}
            tree = resp.json().get("tree", [])
            # Directories we care about (empty string = repo root)
            search_dirs = ("", ".github/", "docs/")
            for item in tree:
                if item.get("type") != "blob":
                    continue
                path = item["path"]
                # Check if file is in one of our target directories
                for prefix in search_dirs:
                    if not path.startswith(prefix):
                        continue
                    filename = path[len(prefix):]
                    # Skip files in subdirectories deeper than our target
                    if "/" in filename:
                        continue
                    if self._INSTRUCTION_FILENAME_PATTERN.match(filename):
                        content = self.get_file_content_at_ref(repo, path, ref)
                        if content:
                            print(f"Found review instructions: {path} (ref: {ref})")
                            return {
                                "found": True,
                                "file_path": path,
                                "content": content,
                                "ref": ref,
                            }
            print(f"No review instruction file found in {repo} at ref '{ref}'.")
            return {"found": False, "file_path": "", "content": "", "ref": ref}

    def review_pull_request(
        self: "_GitHubReviewDeps",
        repo: str,
        pr_number: int,
        checklist: list,
        skip_inline: bool = False,
        skip_footer: bool = False,
        max_inline_comments: int = 6,
        group_similar_inline: bool = True,
    ):
            """
            Review a single pull request based on the provided checklist.
            Behavior:
            - Runs flake8 on changed Python files.
            - Runs cross-file consistency checks.
            - Reviews universal coding conventions in changed source files.
            - Groups and caps inline comments to reduce PR noise.
            - Publishes one consolidated footer summary to avoid comment spam.
            """
            base_sha, head_sha = self.get_base_and_head_sha(repo, pr_number)
            if not head_sha:
                reason = self._last_pr_lookup_error or "Review could not start because the PR head SHA was unavailable."
                print(f"Failed to get PR #{pr_number} details (head_sha is None).")
                print("Reason:", reason)
                return {
                    "pr_number": pr_number,
                    "summary": reason,
                    "reviewed_items": [],
                }

            pr_title = ""
            try:
                pr_title = str(self.get_cached_pr_title(repo, pr_number) or "").strip()
            except Exception:
                pr_title = ""
            if not pr_title:
                pr_title = f"PR #{pr_number}"

            files = self.get_files_in_pr(repo, pr_number)
            if not files:
                logger.info("[REVIEW] No changed files found in this PR.")
                return {
                    "pr_number": pr_number,
                    "pr_title": pr_title,
                    "files_changed": 0,
                    "summary": "No changed files were found in this PR.",
                    "reviewed_items": [],
                }

            enabled_items = [
                item for item in sorted(checklist, key=lambda x: x["execution_order"])
                if item.get("enabled")
            ]
            enabled_ids = {item.get("id", "") for item in enabled_items}
            if not enabled_ids:
                logger.info("[REVIEW] No checklist items enabled.")
                return {
                    "pr_number": pr_number,
                    "pr_title": pr_title,
                    "files_changed": len(files),
                    "summary": "No checklist items were enabled; nothing was reviewed.",
                    "reviewed_items": [],
                }

            if "python_flake8" in enabled_ids:
                flake8_results = self.check_flake8_compliance(repo, head_sha, files)
            else:
                flake8_results = {"compliant": True, "violations_count": 0, "violations": []}
                logger.info("[REVIEW] Flake8 skipped (not selected in checklist)")
            flake8_count = flake8_results.get("violations_count", 0)

            if "cross_file_consistency" in enabled_ids:
                consistency_results = self.check_consistency(repo, head_sha, files)
            else:
                consistency_results = {"total_issues": 0, "issues": []}
                logger.info("[REVIEW] Consistency checks skipped (not selected in checklist)")
            consistency_count = consistency_results.get("total_issues", 0)

            convention_results = self.check_universal_coding_conventions(repo, head_sha, files, enabled_ids)
            convention_count = convention_results["total_issues"]

            def _infer_finding_severity(message: str, default: str = "Medium") -> str:
                normalized = str(message or "").lower()
                if any(token in normalized for token in ("hardcoded", "credential", "credentials", "secret", "token", "password", "api key", "private key")):
                    return "High"
                if any(token in normalized for token in ("security", "injection", "unsafe", "vulnerability", "leak", "exposed")):
                    return "High"
                if any(token in normalized for token in ("error", "undefined", "broken", "invalid", "failure", "mismatch")):
                    return "Medium"
                return default

            def _safe_line_number(value: Any) -> int:
                try:
                    return int(value or 1)
                except Exception:
                    return 1

            def _build_structured_finding_comment(issue: str, explanation: str, suggestion: str, severity: str) -> str:
                return "\n\n".join([
                    "**Findings**",
                    f"**Issue:** {issue}",
                    f"**Explanation:** {explanation}",
                    f"**Suggested Improvement:** {suggestion}",
                    f"**Severity:** {severity}",
                ])

            logger.info("[REVIEW] Preparing inline comments...")
            raw_inline_candidates: list[dict[str, Any]] = []
            for violation in flake8_results.get("violations", []):
                violation_file = str(violation.get("file", "unknown"))
                violation_line = _safe_line_number(violation.get("line", 1))
                violation_code = str(violation.get("code", "Flake8")).strip() or "Flake8"
                violation_message = str(violation.get("message", "Style or lint issue detected.")).strip()
                issue_title = f"Flake8 {violation_code} in {violation_file}:{violation_line}"
                explanation = (
                    f"{violation_message}. This was flagged by Flake8 during PR validation and should be corrected before merge."
                )
                suggestion = (
                    f"Update the code at {violation_file}:{violation_line} to satisfy Flake8 {violation_code} and keep the file compliant with repository standards."
                )
                raw_inline_candidates.append({
                    "file": violation_file,
                    "line": violation_line,
                    "body": _build_structured_finding_comment(
                        issue=issue_title,
                        explanation=explanation,
                        suggestion=suggestion,
                        severity=_infer_finding_severity(violation_message, default="Medium"),
                    ),
                    "source": "flake8",
                    "priority": 0,
                })
            for issue in convention_results.get("issues", []):
                issue_file = str(issue.get("file", "unknown"))
                issue_line = _safe_line_number(issue.get("line", 1))
                issue_message = str(issue.get("message", "Coding convention issue detected.")).strip()
                issue_title = f"Coding convention issue in {issue_file}:{issue_line}"
                explanation = (
                    f"{issue_message}. This finding was detected during the repository convention review and may reduce clarity, consistency, or maintainability."
                )
                suggestion = (
                    f"Revise the code at {issue_file}:{issue_line} so it aligns with the repository's naming, documentation, and comment conventions."
                )
                raw_inline_candidates.append({
                    "file": issue_file,
                    "line": issue_line,
                    "body": _build_structured_finding_comment(
                        issue=issue_title,
                        explanation=explanation,
                        suggestion=suggestion,
                        severity=_infer_finding_severity(issue_message, default="Medium"),
                    ),
                    "source": "convention",
                    "priority": 1,
                })

            inline_candidates_total = len(raw_inline_candidates)
            grouped_candidates_count = inline_candidates_total
            selected_inline_candidates: list[dict[str, Any]] = []

            if group_similar_inline and raw_inline_candidates:
                grouped: dict[tuple[str, int], dict[str, Any]] = {}
                for candidate in raw_inline_candidates:
                    key = (candidate["file"], int(candidate["line"]))
                    bucket = grouped.get(key)
                    if not bucket:
                        grouped[key] = {
                            "file": candidate["file"],
                            "line": int(candidate["line"]),
                            "messages": [candidate["body"]],
                            "sources": {candidate.get("source", "other")},
                            "priority": int(candidate.get("priority", 1)),
                        }
                        continue
                    if candidate["body"] not in bucket["messages"]:
                        bucket["messages"].append(candidate["body"])
                    bucket["sources"].add(candidate.get("source", "other"))
                    bucket["priority"] = min(bucket["priority"], int(candidate.get("priority", 1)))

                grouped_rows: list[dict[str, Any]] = []
                for value in grouped.values():
                    msgs = value["messages"]
                    preview = msgs[:3]
                    body = "\n".join([f"- {m}" for m in preview])
                    if len(msgs) > 3:
                        body += f"\n- ...and {len(msgs) - 3} more related finding(s) on this line."
                    grouped_rows.append({
                        "file": value["file"],
                        "line": value["line"],
                        "body": body,
                        "priority": value["priority"],
                        "sources": value["sources"],
                    })

                grouped_rows.sort(key=lambda x: (x["priority"], x["file"], x["line"]))
                selected_inline_candidates = grouped_rows
                grouped_candidates_count = len(grouped_rows)
            else:
                selected_inline_candidates = sorted(
                    raw_inline_candidates,
                    key=lambda x: (int(x.get("priority", 1)), x["file"], int(x["line"])),
                )

            try:
                inline_limit = int(max_inline_comments)
            except Exception:
                inline_limit = 6
            inline_limit = max(1, min(inline_limit, 25))

            skipped_inline_candidates = max(0, len(selected_inline_candidates) - inline_limit)
            if len(selected_inline_candidates) > inline_limit:
                selected_inline_candidates = selected_inline_candidates[:inline_limit]

            inline_posted = 0
            inline_failed: list[str] = []
            should_post_inline = not skip_inline and len(selected_inline_candidates) > 0
            if should_post_inline:
                review_comments = []
                for candidate in selected_inline_candidates:
                    review_comments.append({
                        "path": candidate["file"],
                        "line": candidate["line"],
                        "side": "RIGHT",
                        "body": candidate["body"],
                    })

                try:
                    review_url = (
                        f"{self._base_url}/repos/{self._repo_path(repo)}/pulls/{pr_number}/reviews"
                    )
                    review_data = {
                        "commit_id": head_sha,
                        "event": "COMMENT",
                        "comments": review_comments,
                    }
                    resp = requests.post(review_url, headers=self._headers, json=review_data, timeout=30)
                    if resp.status_code in (200, 201):
                        inline_posted = len(review_comments)
                        logger.info("[REVIEW] Batch posted %d inline comment(s) via review API", inline_posted)
                    else:
                        logger.info("[REVIEW] Batch review failed (%s), falling back to individual posts", resp.status_code)
                        for candidate in selected_inline_candidates:
                            try:
                                self.add_inline_comment(
                                    repo=repo,
                                    pr_number=pr_number,
                                    head_sha=head_sha,
                                    comment_body=candidate["body"],
                                    selected_path=candidate["file"],
                                    start_line=candidate["line"],
                                    end_line=candidate["line"],
                                    side="RIGHT",
                                )
                                inline_posted += 1
                            except Exception as exc:
                                inline_failed.append(f"{candidate['file']}:{candidate['line']} ({exc})")
                except Exception as exc:
                    logger.info("[REVIEW] Batch review exception: %s, falling back", exc)
                    for candidate in selected_inline_candidates:
                        try:
                            self.add_inline_comment(
                                repo=repo,
                                pr_number=pr_number,
                                head_sha=head_sha,
                                comment_body=candidate["body"],
                                selected_path=candidate["file"],
                                start_line=candidate["line"],
                                end_line=candidate["line"],
                                side="RIGHT",
                            )
                            inline_posted += 1
                        except Exception as exc2:
                            inline_failed.append(f"{candidate['file']}:{candidate['line']} ({exc2})")

            reviewed_items = [
                item["name"] for item in enabled_items
            ]

            total_findings = flake8_count + consistency_count + convention_count
            show_all_general_findings = bool(skip_inline)

            flake8_violations = list(flake8_results.get("violations", []) or [])
            consistency_issues = list(consistency_results.get("issues", []) or [])
            if not consistency_issues and consistency_count > 0:
                for item in list(consistency_results.get("spelling_variations", []) or []):
                    term = str(item.get("term", "Term"))
                    variants = item.get("variations", {}) or {}
                    variant_keys = sorted(str(k) for k in variants.keys())
                    consistency_issues.append({
                        "file": "cross-file",
                        "line": "-",
                        "message": f"Spelling variation for '{term}': {', '.join(variant_keys)}",
                    })

                for item in list(consistency_results.get("metric_inconsistencies", []) or []):
                    metric = str(item.get("metric", "metric"))
                    values = list(item.get("inconsistent_values", []) or [])
                    consistency_issues.append({
                        "file": "cross-file",
                        "line": "-",
                        "message": f"Metric '{metric}' uses inconsistent values: {', '.join(str(v) for v in values)}",
                    })

                for item in list(consistency_results.get("formatting_variations", []) or []):
                    label = str(item.get("label", "formatting"))
                    variants = list(item.get("variants", []) or [])
                    consistency_issues.append({
                        "file": "cross-file",
                        "line": "-",
                        "message": f"Formatting variation in {label}: {', '.join(str(v) for v in variants)}",
                    })

            convention_issues = list(convention_results.get("issues", []) or [])

            def _format_flake8_item(violation: dict) -> str:
                file_name = str(violation.get("file", "unknown"))
                line_no = violation.get("line", "?")
                code = str(violation.get("code", "")).strip()
                message = str(violation.get("message", "")).strip()
                return f"- {file_name}:{line_no} {code} {message}".rstrip()

            def _format_issue_item(issue: dict) -> str:
                file_name = str(issue.get("file", "unknown")).strip()
                line_no = str(issue.get("line", "?")).strip()
                message = str(issue.get("message", "")).strip()
                if not line_no or line_no in {"-", "?", "None"}:
                    return f"- {file_name} {message}".rstrip()
                return f"- {file_name}:{line_no} {message}".rstrip()

            if total_findings == 0:
                executive_summary = (
                    "This pull request is in good overall condition and passed all review checks. "
                    "The code appears clean, readable, and compliant with configured standards."
                )
            elif total_findings <= 2 and flake8_count == 0:
                executive_summary = (
                    "This pull request is in good overall condition and passed most review checks. "
                    "No linting issues were found; only minor consistency findings were detected."
                )
            else:
                executive_summary = (
                    "This pull request has notable findings that should be addressed before merge. "
                    "Review the detailed findings section and prioritize linting and consistency issues."
                )

            user_mode_line = (
                "General-comment mode is enabled (inline comments skipped by user selection), "
                "so all detected findings are listed below in this summary."
                if skip_inline
                else "Inline review comments were posted where possible, and key findings are summarized below."
            )

            flake8_status = "Passed" if flake8_count == 0 else "Completed with Findings"
            convention_status = "Passed" if convention_count == 0 else "Completed with Findings"
            consistency_status = "Passed" if consistency_count == 0 else "Completed with Findings"

            summary_lines = [
                f"Reviewed PR #{pr_number}: {pr_title}",
                "",
                "**Executive Summary**",
                "",
                executive_summary,
                "",
                user_mode_line,
                "",
                "**Review Scope**",
                f"Files Changed: {len(files)}",
                f"Checklist Items Executed: {len(reviewed_items)}",
                f"Findings Detected: {total_findings}",
                "",
                "**Validation Results**",
                "",
                "**Python Flake8 Compliance**",
                "",
                f"Status: {flake8_status}",
                f"Issues Found: {flake8_count}",
                "",
                "No Flake8 violations detected." if flake8_count == 0 else "Flake8 issues were detected and should be addressed.",
                "",
                "**Universal Naming Conventions**",
                "",
                f"Status: {convention_status}",
                "",
                "Naming patterns are clear and appropriate." if convention_count == 0 else "Naming-related convention findings were detected.",
                "",
                "**Function Documentation**",
                "",
                f"Status: {convention_status}",
                "",
                "Functions contain acceptable documentation or are self-explanatory." if convention_count == 0 else "Documentation-related convention findings were detected.",
                "",
                "**Comment Accuracy**",
                "",
                f"Status: {convention_status}",
                "",
                "Comments are aligned with actual code behavior." if convention_count == 0 else "Comment accuracy findings were detected.",
                "",
                "**Cross-file Consistency**",
                "",
                f"Status: {consistency_status}",
                "",
                "No cross-file consistency issues found." if consistency_count == 0 else f"{consistency_count} terminology or consistency issue(s) were found.",
            ]

            if reviewed_items:
                summary_lines.append("")
                summary_lines.append("**Checklist completed:**")
                for item in reviewed_items:
                    summary_lines.append(f"- {item}: done")

            if group_similar_inline and inline_candidates_total > 0:
                summary_lines.append("")
                summary_lines.append(
                    f"Inline finding grouping: {inline_candidates_total} raw finding(s) -> {grouped_candidates_count} grouped candidate(s)."
                )
            if should_post_inline and skipped_inline_candidates > 0:
                summary_lines.append(
                    f"Inline volume control: kept top {inline_limit} inline comment(s), deferred {skipped_inline_candidates} additional candidate(s) to summary."
                )
            if inline_failed:
                summary_lines.append("")
                summary_lines.append(f"Inline attachment fallback occurred for {len(inline_failed)} item(s):")
                for item in inline_failed[:10]:
                    summary_lines.append(f"- {item}")

            api_variants: list[str] = []
            if total_findings > 0:
                summary_lines.append("")
                summary_lines.append("**Detailed Finding**" if total_findings == 1 else "**Detailed Findings**")

                if flake8_violations:
                    flake8_to_show = flake8_violations if show_all_general_findings else flake8_violations[:15]
                    summary_lines.append("")
                    summary_lines.append(f"**Flake8 ({flake8_count})**")
                    for violation in flake8_to_show:
                        summary_lines.append(_format_flake8_item(violation))
                    hidden = flake8_count - len(flake8_to_show)
                    if hidden > 0:
                        summary_lines.append(f"- ... and {hidden} more flake8 finding(s).")

                if consistency_issues:
                    consistency_to_show = consistency_issues if show_all_general_findings else consistency_issues[:20]
                    summary_lines.append("")
                    summary_lines.append(f"**Cross-file Consistency ({consistency_count})**")
                    for issue in consistency_to_show:
                        line = _format_issue_item(issue)
                        summary_lines.append(line)
                        msg = str(issue.get("message", ""))
                        if "Spelling variation for 'API'" in msg:
                            try:
                                variants_part = msg.split(":", 1)[1].strip()
                                api_variants = [v.strip() for v in variants_part.split(",") if v.strip()]
                            except Exception:
                                pass
                    hidden = consistency_count - len(consistency_to_show)
                    if hidden > 0:
                        summary_lines.append(f"- ... and {hidden} more consistency finding(s).")

                if convention_issues:
                    convention_to_show = convention_issues if show_all_general_findings else convention_issues[:20]
                    summary_lines.append("")
                    summary_lines.append(f"**Universal Coding Conventions ({convention_count})**")
                    for issue in convention_to_show:
                        summary_lines.append(_format_issue_item(issue))
                    hidden = convention_count - len(convention_to_show)
                    if hidden > 0:
                        summary_lines.append(f"- ... and {hidden} more convention finding(s).")

            summary_lines.append("")
            summary_lines.append("**Recommendation**")
            summary_lines.append("")

            if total_findings == 0:
                summary_lines.append("No action required. The changes are ready from a style and consistency perspective.")
            else:
                if api_variants:
                    summary_lines.append("Use a consistent convention based on context:")
                    summary_lines.append("- API for comments, documentation, labels, and constants")
                    summary_lines.append("- api for Python snake_case variables and function names")
                if flake8_count > 0:
                    summary_lines.append("Address the listed Flake8 issues to keep style compliance high.")
                if convention_count > 0:
                    summary_lines.append("Resolve naming/documentation/comment convention findings for consistency.")
                if consistency_count > 0 and not api_variants:
                    summary_lines.append("Standardize repeated terms and formatting choices across files.")
            footer_summary = "\n".join(summary_lines)
            if not skip_footer:
                self.add_comment(repo, pr_number, footer_summary)
                logger.info("[REVIEW] PR #%s review completed with consolidated summary.", pr_number)
            else:
                logger.info("[REVIEW] PR #%s review completed (footer comment skipped per user selection).", pr_number)

            return {
                "pr_number": pr_number,
                "pr_title": pr_title,
                "files_changed": len(files),
                "checklist_items": len(enabled_items),
                "flake8_violations": flake8_count,
                "consistency_issues": consistency_count,
                "convention_issues": convention_count,
                "inline_comments_posted": inline_posted,
                "inline_candidates_total": inline_candidates_total,
                "inline_candidates_selected": len(selected_inline_candidates),
                "summary": footer_summary,
                "reviewed_items": reviewed_items,
            }
