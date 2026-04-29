"""GitHub pull request review orchestration and rule execution."""

import ast
import base64
import difflib
import logging
import re

import requests

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
    def check_consistency(self, repo: str, head_sha: str, files: list[dict[str, Any]]) -> dict[str, Any]: ...
    def get_cached_pr_title(self, repo: str, pr_number: int) -> str: ...

class GitHubReviewActions:
    _INSTRUCTION_FILENAME_PATTERN = re.compile(
        r"(?i)^(?:review[-_ ]?instructions?|review[-_ ]?checklist|copilot[-_ ]?instructions|pull[-_ ]?request[-_ ]?checklist)(?:\.md|\.txt)?$"
    )

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
        - Runs cross-file consistency checks.
        - Reviews universal coding conventions in changed source files.
        - Groups and caps inline comments to reduce PR noise.
        - Does NOT create or post a consolidated footer summary; the AI/consumer will generate that.
        """
        base_sha, head_sha = self.get_base_and_head_sha(repo, pr_number)
        if not head_sha:
            reason = self._last_pr_lookup_error or "Review could not start because the PR head SHA was unavailable."
            logger.warning("Failed to get PR #%s details (head_sha is None): %s", pr_number, reason)
            return {"pr_number": pr_number, "summary": reason, "reviewed_items": []}

        try:
            pr_title = str(self.get_cached_pr_title(repo, pr_number) or "").strip() or f"PR #{pr_number}"
        except Exception:
            pr_title = f"PR #{pr_number}"

        files = self.get_files_in_pr(repo, pr_number)
        if not files:
            logger.info("[REVIEW] No changed files found in PR #%s.", pr_number)
            return {
                "pr_number": pr_number,
                "pr_title": pr_title,
                "files_changed": 0,
                "summary": "No changed files were found in this PR.",
                "reviewed_items": [],
            }

        enabled_items = [item for item in sorted(checklist, key=lambda x: x["execution_order"]) if item.get("enabled")]
        enabled_ids = {item.get("id", "") for item in enabled_items}
        if not enabled_ids:
            logger.info("[REVIEW] No checklist items enabled for PR #%s.", pr_number)
            return {
                "pr_number": pr_number,
                "pr_title": pr_title,
                "files_changed": len(files),
                "summary": "No checklist items were enabled; nothing was reviewed.",
                "reviewed_items": [],
            }

        # Consistency checks (if enabled)
        if "cross_file_consistency" in enabled_ids:
            try:
                consistency_results = self.check_consistency(repo, head_sha, files)
            except Exception as e:
                logger.exception("Consistency check failed for PR #%s: %s", pr_number, e)
                consistency_results = {"total_issues": 0, "issues": []}
        else:
            consistency_results = {"total_issues": 0, "issues": []}
            logger.debug("[REVIEW] Consistency checks skipped for PR #%s (not selected in checklist)", pr_number)
        consistency_count = int(consistency_results.get("total_issues", 0))

        # Convention checks
        try:
            convention_results = self.check_universal_coding_conventions(repo, head_sha, files, enabled_ids)
            convention_count = int(convention_results.get("total_issues", 0))
        except Exception as e:
            logger.exception("Convention check failed for PR #%s: %s", pr_number, e)
            convention_results = {"total_issues": 0, "issues": []}
            convention_count = 0

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
            return "\n\n".join(
                [
                    "**Findings**",
                    f"**Issue:** {issue}",
                    f"**Explanation:** {explanation}",
                    f"**Suggested Improvement:** {suggestion}",
                    f"**Severity:** {severity}",
                ]
            )

        # Prepare inline candidates from convention issues
        logger.info("[REVIEW] Preparing inline comments for PR #%s...", pr_number)
        raw_inline_candidates: list[dict[str, Any]] = []
        for issue in convention_results.get("issues", []) or []:
            issue_file = str(issue.get("file", "unknown"))
            issue_line = _safe_line_number(issue.get("line", 1))
            issue_message = str(issue.get("message", "Coding convention issue detected.")).strip()
            issue_title = f"Coding convention issue in {issue_file}:{issue_line}"
            explanation = (
                f"{issue_message}. This finding was detected during the repository convention review and may reduce clarity, consistency, or maintainability."
            )
            suggestion = f"Revise the code at {issue_file}:{issue_line} so it aligns with repository naming, documentation, and comment conventions."
            raw_inline_candidates.append(
                {
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
                }
            )

        inline_candidates_total = len(raw_inline_candidates)

        # Group similar inline findings by file+line
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
                else:
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
                grouped_rows.append(
                    {
                        "file": value["file"],
                        "line": value["line"],
                        "body": body,
                        "priority": value["priority"],
                        "sources": value["sources"],
                    }
                )

            grouped_rows.sort(key=lambda x: (x["priority"], x["file"], x["line"]))
            selected_inline_candidates = grouped_rows
            grouped_candidates_count = len(grouped_rows)
        else:
            selected_inline_candidates = sorted(
                raw_inline_candidates,
                key=lambda x: (int(x.get("priority", 1)), x["file"], int(x["line"])),
            )
            grouped_candidates_count = len(selected_inline_candidates)

        # Enforce inline limits
        try:
            inline_limit = int(max_inline_comments)
        except Exception:
            inline_limit = 6
        inline_limit = max(1, min(inline_limit, 25))

        skipped_inline_candidates = max(0, len(selected_inline_candidates) - inline_limit)
        if len(selected_inline_candidates) > inline_limit:
            selected_inline_candidates = selected_inline_candidates[:inline_limit]

        # Post inline comments (batch then fallback to individual)
        inline_posted = 0
        inline_failed: list[str] = []
        should_post_inline = not skip_inline and len(selected_inline_candidates) > 0
        if should_post_inline:
            review_comments = []
            for candidate in selected_inline_candidates:
                review_comments.append(
                    {
                        "path": candidate["file"],
                        "line": candidate["line"],
                        "side": "RIGHT",
                        "body": candidate["body"],
                    }
                )

            try:
                review_url = f"{self._base_url}/repos/{self._repo_path(repo)}/pulls/{pr_number}/reviews"
                review_data = {"commit_id": head_sha, "event": "COMMENT", "comments": review_comments}
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

        reviewed_items = [item["name"] for item in enabled_items]

        # Normalize issues for return
        consistency_issues = list(consistency_results.get("issues", []) or [])
        if not consistency_issues and consistency_count > 0:
            for item in list(consistency_results.get("spelling_variations", []) or []):
                term = str(item.get("term", "Term"))
                variants = item.get("variations", {}) or {}
                variant_keys = sorted(str(k) for k in variants.keys())
                consistency_issues.append(
                    {"file": "cross-file", "line": "-", "message": f"Spelling variation for '{term}': {', '.join(variant_keys)}"}
                )
            for item in list(consistency_results.get("metric_inconsistencies", []) or []):
                metric = str(item.get("metric", "metric"))
                values = list(item.get("inconsistent_values", []) or [])
                consistency_issues.append(
                    {"file": "cross-file", "line": "-", "message": f"Metric '{metric}' uses inconsistent values: {', '.join(str(v) for v in values)}"}
                )
            for item in list(consistency_results.get("formatting_variations", []) or []):
                label = str(item.get("label", "formatting"))
                variants = list(item.get("variants", []) or [])
                consistency_issues.append({"file": "cross-file", "line": "-", "message": f"Formatting variation in {label}: {', '.join(str(v) for v in variants)}"})

        convention_issues = list(convention_results.get("issues", []) or [])

        total_findings = consistency_count + convention_count

        logger.info("[REVIEW] PR #%s review data prepared (footer posting skipped). Findings: %d", pr_number, total_findings)

        # Return structured data for AI/consumer to build summary
        return {
            "pr_number": pr_number,
            "pr_title": pr_title,
            "files_changed": len(files),
            "checklist_items": len(enabled_items),
            "consistency_issues_count": consistency_count,
            "convention_issues_count": convention_count,
            "total_findings": total_findings,
            "inline_comments_posted": inline_posted,
            "inline_candidates_total": inline_candidates_total,
            "inline_candidates_selected": len(selected_inline_candidates),
            "inline_candidates_skipped": skipped_inline_candidates,
            "inline_failed": inline_failed,
            "consistency_issues": consistency_issues,
            "convention_issues": convention_issues,
            "reviewed_items": reviewed_items,
            # signal that footer/comment generation is expected to be handled externally
            "footer_posted": False,
            "footer_skipped_by_user": skip_footer,
        }