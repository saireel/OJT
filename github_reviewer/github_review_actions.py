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
        side: str = "RIGHT",
    ) -> dict[str, Any]: ...
    def add_comment(self, repo: str, pr_number: int, comment_text: str) -> dict[str, Any]: ...
    def fetch_review_instructions(self, repo: str, ref: str = "main") -> dict[str, Any]: ...
    def check_flake8_compliance(self, repo: str, head_sha: str, files: list[dict[str, Any]]) -> dict[str, Any]: ...
    def check_consistency(self, repo: str, head_sha: str, files: list[dict[str, Any]]) -> dict[str, Any]: ...

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

    def review_pull_request(self: "_GitHubReviewDeps", repo: str, pr_number: int, checklist: list, skip_inline: bool = False, skip_footer: bool = False):
            """
            Review a single pull request based on the provided checklist.
            Behavior:
            - Runs flake8 on changed Python files.
            - Runs cross-file consistency checks.
            - Reviews universal coding conventions in changed source files.
            - Posts inline comments for a small set of findings.
            - Publishes one consolidated footer summary to avoid comment spam.
            :param repo: Repository name
            :param pr_number: Pull request number (from the link)
            :param checklist: List of review rules (dict) with fields like:
                            id, name, description, enabled, execution_order
            :return: Structured review result for the web interface and footer summary.
            """
            head_sha = self.get_base_and_head_sha(repo, pr_number)[1]
            if not head_sha:
                reason = self._last_pr_lookup_error or "Review could not start because the PR head SHA was unavailable."
                print(f"Failed to get PR #{pr_number} details (head_sha is None).")
                print("Reason:", reason)
                return {
                    "pr_number": pr_number,
                    "summary": reason,
                    "reviewed_items": [],
                }
            # Fetch PR title directly instead of listing all PRs
            try:
                _pr_url = f"{self._base_url}/repos/{self._repo_path(repo)}/pulls/{pr_number}"
                _pr_resp = requests.get(_pr_url, headers=self._headers, timeout=15)
                pr_title = _pr_resp.json().get("title", "Unknown PR") if _pr_resp.status_code == 200 else "Unknown PR"
            except Exception:
                pr_title = "Unknown PR"
            logger.info("[REVIEW] Reviewing PR #%s: %s", pr_number, pr_title)
            instructions = self.fetch_review_instructions(repo, ref=head_sha)
            if instructions["found"]:
                logger.info("[REVIEW] Using review instructions from: %s", instructions["file_path"])
            else:
                logger.info("[REVIEW] No review instruction file found; using default review behavior.")
            files = self.get_files_in_pr(repo, pr_number)
            # Pre-fetch all file contents to warm the cache (avoids repeated API calls in checks)
            logger.info("[REVIEW] Pre-fetching %d file(s) to cache...", len(files))
            for _f in (files or []):
                _fname = _f.get("filename", "")
                if _fname:
                    try:
                        self.get_file_content_at_ref(repo, _fname, head_sha)
                    except Exception:
                        pass
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
                if item.get("enabled", True)
            ]
            enabled_ids = {str(item.get("id", "")) for item in enabled_items}
            if "python_flake8" in enabled_ids:
                logger.info("[REVIEW] Running flake8 compliance checks...")
                flake8_results = self.check_flake8_compliance(repo, head_sha, files)
                logger.info("[REVIEW] Flake8 complete: %d violation(s)", len(flake8_results.get("violations", [])))
            else:
                logger.info("[REVIEW] Flake8 skipped (not selected in checklist)")
                flake8_results = {"compliant": True, "violations_count": 0, "violations": []}
            flake8_count = flake8_results["violations_count"]
            if "cross_file_consistency" in enabled_ids:
                logger.info("[REVIEW] Running cross-file consistency checks...")
                consistency_results = self.check_consistency(repo, head_sha, files)
                logger.info("[REVIEW] Consistency check complete: %d issue(s)", consistency_results.get("total_issues", 0))
            else:
                logger.info("[REVIEW] Consistency checks skipped (not selected in checklist)")
                consistency_results = {"total_issues": 0, "issues": []}
            consistency_count = consistency_results.get("total_issues", 0)
            convention_results = self.check_universal_coding_conventions(repo, head_sha, files, enabled_ids)
            convention_count = convention_results["total_issues"]
            logger.info("[REVIEW] Preparing inline comments...")
            inline_candidates: list[dict] = []
            for violation in flake8_results["violations"]:
                inline_candidates.append({
                    "file": violation["file"],
                    "line": violation["line"],
                    "body": (
                        f"Flake8 {violation['code']}: {violation['message']} "
                        f"(line {violation['line']}, col {violation['col']})."
                    ),
                })
            for issue in convention_results["issues"]:
                inline_candidates.append({
                    "file": issue["file"],
                    "line": issue["line"],
                    "body": f"Coding convention: {issue['message']}",
                })
            inline_posted = 0
            inline_failed = []
            should_post_inline = not skip_inline and 0 < len(inline_candidates) <= 10
            if should_post_inline:
                # Batch all inline comments into a single GitHub review
                review_comments = []
                for candidate in inline_candidates:
                    review_comments.append({
                        "path": candidate["file"],
                        "line": candidate["line"],
                        "side": "RIGHT",
                        "body": candidate["body"],
                    })
                try:
                    review_url = (
                        f"{self._base_url}/repos/{self._repo_path(repo)}"
                        f"/pulls/{pr_number}/reviews"
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
                        for candidate in inline_candidates:
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
                    for candidate in inline_candidates:
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
                "changed files in the PR",
                "flake8 compliance for changed Python files",
                "cross-file terminology and metric consistency",
            ]
            reviewed_items.extend(convention_results["reviewed_items"])
            summary_lines = [
                "**Automated PR Review Summary**",
                f"- PR: #{pr_number} - {pr_title}",
                f"- Files changed: {len(files)}",
                f"- Checklist checks executed: {len(enabled_items)}",
                f"- Reviewed: {'; '.join(reviewed_items)}",
            ]
            if instructions["found"]:
                summary_lines.append(
                    f"- Review instructions: `{instructions['file_path']}` (ref: {instructions['ref'][:7]})"
                )
            if flake8_count == 0:
                summary_lines.append("- Flake8: Approved (0 issues).")
            else:
                flake8_line = f"- Flake8: {flake8_count} issue(s) found."
                if should_post_inline:
                    flake8_line += f" Included in {inline_posted} inline comment(s) across all checks."
                summary_lines.append(flake8_line)
            if consistency_count == 0:
                summary_lines.append("- Cross-file consistency: No issues found.")
            else:
                summary_lines.append(f"- Cross-file consistency: {consistency_count} issue(s) found.")
            if convention_count == 0:
                summary_lines.append("- Universal coding conventions: Approved (0 issues).")
            else:
                convention_line = f"- Universal coding conventions: {convention_count} issue(s) found."
                if should_post_inline:
                    convention_line += " Findings were posted inline when possible."
                summary_lines.append(convention_line)
            if inline_failed:
                summary_lines.append(
                    f"- Inline fallback needed for {len(inline_failed)} issue(s) that could not be attached to diff lines."
                )
                for item in inline_failed[:5]:
                    summary_lines.append(f"  - {item}")
            if not should_post_inline and inline_candidates:
                summary_lines.append(
                    f"- Inline comments were skipped because {len(inline_candidates)} findings exceeded the 10-comment threshold."
                )
            if flake8_count > 10:
                summary_lines.append("- Top flake8 findings:")
                for violation in flake8_results["violations"][:10]:
                    summary_lines.append(
                        f"  - {violation['file']}:{violation['line']}:{violation['col']} "
                        f"{violation['code']} {violation['message']}"
                    )
                summary_lines.append(f"  ... and {flake8_count - 10} more")
            if convention_count > 0 and not should_post_inline:
                summary_lines.append("- Top coding convention findings:")
                for issue in convention_results["issues"][:10]:
                    summary_lines.append(f"  - {issue['file']}:{issue['line']} {issue['message']}")
                if convention_count > 10:
                    summary_lines.append(f"  ... and {convention_count - 10} more")
            logger.info("[REVIEW] Building review summary...")
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
                "reviewed_items": reviewed_items,
                "summary": footer_summary,
            }
