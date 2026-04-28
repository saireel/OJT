import logging
from typing import Optional, Any, Dict, List, Tuple, cast
from fastmcp import FastMCP
from mcp_server.mcp_calls import (
    create_space,
    create_page,
    update_page,
    find_and_replace_in_page,
    get_page_content,
    get_page_content_by_sections,
    review_confluence_page,
    post_footer_comment,
    post_inline_comment,
    list_repositories,
    list_pull_requests,
    add_comment,
    get_files_in_pr,
    get_base_and_head_sha,
    get_file_content_at_ref,
    file_with_line_no_and_diff,
    add_file_level_comment,
    add_inline_comment,
    show_comments,
    reply_comment,
    review_pull_request,
    cleanup_old_bot_comments,
    summarize_pr_and_confluence,
    set_runtime_auth,
    check_doc_coverage,
    check_code_examples,
    check_api_signatures,
    check_config_documented,
    check_architecture_alignment,
    check_instructions_match,
    check_error_handling,
    check_deprecated_removed,
    check_terminology_consistent,
    check_pr_references,
    check_code_path_sections,
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

mcp = FastMCP("Confluence MCP Server")


@mcp.tool(description="Create a new Confluence space")
def tool_create_space(name: str, key: str, description: str) -> Dict[str, Any]:
    return create_space(name, key, description)


@mcp.tool(description="Create a new Confluence page")
def tool_create_page(title: str, space_key: str, content: str) -> Dict[str, Any]:
    return create_page(title, space_key, content)


@mcp.tool(description="Update an existing Confluence page")
def tool_update_page(page_id: str, title: str, content: str, version: int, message: str) -> Dict[str, Any]:
    return update_page(page_id, title, content, version, message)


@mcp.tool(description="Find and replace text in a Confluence page")
def tool_find_and_replace(page_id: str, find_text: str, replace_text: str, replace_all: bool = True) -> Dict[str, Any]:
    return find_and_replace_in_page(page_id, find_text, replace_text, replace_all)


@mcp.tool(description="Get content of a Confluence page")
def tool_get_page_content(page_id: str) -> Dict[str, Any]:
    return get_page_content(page_id)


@mcp.tool(description="Get content of a Confluence page by sections")
def tool_get_page_sections(page_id: str, chunk_size: int = 2500, max_sections: int = 50) -> Dict[str, Any]:
    return get_page_content_by_sections(page_id, chunk_size, max_sections)


@mcp.tool(description="Post a footer comment on a Confluence page")
def tool_post_footer_comment(page_id: str, comment: str) -> Dict[str, Any]:
    return post_footer_comment(page_id, comment)


@mcp.tool(description="Post an inline comment on a Confluence page")
def tool_post_inline_comment(page_id: str, comment: str, text_selection: str, match_index: Optional[int] = None) -> Dict[str, Any]:
    return post_inline_comment(page_id, comment, text_selection, match_index)


@mcp.tool(description="List GitHub repositories")
def tool_list_repositories() -> Any:
    return list_repositories()


@mcp.tool(description="List pull requests in a GitHub repository")
def tool_list_pull_requests(repo: str, state: str = "open") -> Any:
    return list_pull_requests(repo, state)


@mcp.tool(description="Add a comment to a GitHub pull request")
def tool_add_pr_comment(repo: str, pr_number: int, comment_text: str) -> Any:
    return add_comment(repo, pr_number, comment_text)


@mcp.tool(description="Get files in a GitHub pull request")
def tool_get_files_in_pr(repo: str, pr_number: int) -> Any:
    return get_files_in_pr(repo, pr_number)


@mcp.tool(description="Get base and head commit SHAs for a GitHub pull request")
def tool_get_base_and_head_sha(repo: str, pr_number: int) -> Any:
    return get_base_and_head_sha(repo, pr_number)


@mcp.tool(description="Get file content at a specific ref in GitHub")
def tool_get_file_content(repo: str, file_path: str, ref: str) -> Any:
    return get_file_content_at_ref(repo, file_path, ref)


@mcp.tool(description="Get file content with line numbers and diff in GitHub")
def tool_file_with_line_no_and_diff(repo: str, pr_number: int, file_path: str) -> Any:
    return file_with_line_no_and_diff(repo, pr_number, file_path)


@mcp.tool(description="Add a file-level comment to a GitHub pull request")
def tool_add_file_level_comment(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str) -> Any:
    return add_file_level_comment(repo, pr_number, head_sha, comment_body, selected_path)


@mcp.tool(description="Add an inline comment to a GitHub pull request")
def tool_add_inline_comment(repo: str, pr_number: int, head_sha: str, comment_body: str, selected_path: str, start_line: int, end_line: int, side: str) -> Any:
    return add_inline_comment(repo, pr_number, head_sha, comment_body, selected_path, start_line, end_line, side)


@mcp.tool(description="Show comments on a GitHub pull request")
def tool_show_comments(repo: str, pr_number: int) -> Any:
    return show_comments(repo, pr_number)


@mcp.tool(description="Reply to a comment on a GitHub pull request")
def tool_reply_comment(repo: str, pr_number: int, comment_id: int, reply_text: str) -> Any:
    return reply_comment(repo, pr_number, comment_id, reply_text)


@mcp.tool(description="Cleanup old bot comments on a GitHub pull request")
def tool_cleanup_old_bot_comments(repo: str, pr_number: int) -> Any:
    return cleanup_old_bot_comments(repo, pr_number)


@mcp.tool(description="Review a Confluence page for documentation quality")
def tool_review_confluence(page_input: str = "", page_id: str = "", checklist_page_id: str = "", skip_inline: bool = False, skip_footer: bool = False) -> Any:
    return review_confluence_page(page_input, page_id, checklist_page_id, skip_inline, skip_footer)


@mcp.tool(description="Review a GitHub pull request")
def tool_review_pull_request(
    repo: str,
    pr_number: int,
    checklist: list,
    skip_inline: bool = False,
    skip_footer: bool = False,
    max_inline_comments: int = 6,
    group_similar_inline: bool = True,
) -> Any:
    return review_pull_request(
        repo,
        pr_number,
        checklist,
        skip_inline,
        skip_footer,
        max_inline_comments,
        group_similar_inline,
    )


@mcp.tool(description="Set runtime authentication credentials")
def tool_set_runtime_auth(
    confluence_email: str = "",
    confluence_api_token: str = "",
    confluence_base_url: str = "",
    github_owner: str = "",
    github_token: str = "",
    github_base_url: str = "",
) -> Any:
    return set_runtime_auth(
        confluence_email=confluence_email,
        confluence_api_token=confluence_api_token,
        confluence_base_url=confluence_base_url,
        github_owner=github_owner,
        github_token=github_token,
        github_base_url=github_base_url,
    )


@mcp.tool(description="Summarize PR and Confluence changes")
def tool_summarize_pr_confluence(repo: str, pr_number: int, conf_page_id: str) -> Any:
    return summarize_pr_and_confluence(repo, pr_number, conf_page_id)


@mcp.tool(description="Check documentation coverage for PR changes")
def tool_check_doc_coverage(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_doc_coverage(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if code examples match PR changes")
def tool_check_code_examples(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_code_examples(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if API/function signatures are up to date")
def tool_check_api_signatures(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_api_signatures(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if config/env variables are documented")
def tool_check_config_documented(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_config_documented(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check architecture/design alignment with PR")
def tool_check_architecture_alignment(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_architecture_alignment(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if step-by-step instructions match implementation")
def tool_check_instructions_match(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_instructions_match(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if error handling & edge cases are documented")
def tool_check_error_handling(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_error_handling(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if deprecated features/APIs are removed from docs")
def tool_check_deprecated_removed(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_deprecated_removed(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check for consistent terminology between code and docs")
def tool_check_terminology(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_terminology_consistent(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check if PR/ticket references are present")
def tool_check_pr_references(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_pr_references(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="Check for missing sections in covered code paths")
def tool_check_code_path_sections(repo: str, head_sha: str, files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return check_code_path_sections(pr_content=files, pr_sha=head_sha, conf_content="", conf_page_id="", repo=repo)


@mcp.tool(description="""
Review both a GitHub PR and Confluence page for alignment and consistency.

Performs a combined review ensuring documentation and code changes are in sync.
Runs only the checks specified in the checklist.

Args:
- repo (str): Repository name in format "owner/repo".
- pr_number (int): Pull request number.
- conf_page_id (str): Confluence page ID.
- pr_checklist (list, optional): List of PR checks to run.
- conf_checklist (list, optional): List of Confluence checks to run.
- skip_inline (bool, optional): Skip inline comments (default False).
- skip_footer (bool, optional): Skip footer summary (default False).

Returns:
- dict: {"success": bool, "pr_results": {...}, "confluence_results": {...}, "combined_analysis": {...}}
""")
def review_combined_pr_and_confluence(repo: str, pr_number: int, conf_page_id: str, pr_checklist: Optional[List[str]] = None, conf_checklist: Optional[List[str]] = None, skip_inline: bool = False, skip_footer: bool = False) -> Dict[str, Any]:
    """Review both PR and Confluence page together, running only selected checks.
    
    Workflow:
    1. Fetch both PR and Confluence content upfront
    2. For each selected check, pass BOTH sources for cross-checking
    3. Aggregate and return results
    """
    try:
        logger.info("[REVIEW] === Starting combined PR + Confluence review: {0}#{1} + page {2} ===".format(repo, pr_number, conf_page_id))
        
        # STEP 1: Fetch PR content upfront using GitHub MCP helper tools
        sha_result = get_base_and_head_sha(repo, pr_number)
        if not isinstance(sha_result, dict) or not sha_result.get("success"):
            err = sha_result.get("error", "Unknown") if isinstance(sha_result, dict) else str(sha_result)
            raise Exception("Could not get PR commit info: {0}".format(err))

        sha_payload = sha_result.get("data")
        base_sha = ""
        head_sha = ""
        if isinstance(sha_payload, dict):
            sha_data: Dict[str, Any] = sha_payload
            base_sha = cast(str, sha_data.get("base_sha") or "")
            head_sha = cast(str, sha_data.get("head_sha") or "")
        elif isinstance(sha_payload, (list, tuple)) and len(sha_payload) >= 2:
            base_sha = cast(str, sha_payload[0] or "")
            head_sha = cast(str, sha_payload[1] or "")

        if not head_sha:
            raise Exception("Could not get PR commit info: missing head_sha")

        pr_files_result = get_files_in_pr(repo, pr_number)
        if not isinstance(pr_files_result, dict) or not pr_files_result.get("success"):
            err = pr_files_result.get("error", "Unknown") if isinstance(pr_files_result, dict) else str(pr_files_result)
            raise Exception("Could not get PR files: {0}".format(err))

        pr_files_data = pr_files_result.get("data")
        pr_files = pr_files_data if isinstance(pr_files_data, list) else []

        # Enrich with per-file diff context using MCP helper tool.
        # This makes combined checks operate on the same PR diff data path as PR review.
        pr_file_diffs: Dict[str, Any] = {}
        pr_file_contents: Dict[str, Any] = {}
        for prf in pr_files:
            file_path = ""
            if isinstance(prf, dict):
                file_path = cast(str, prf.get("filename") or prf.get("path") or "")
            elif isinstance(prf, str):
                file_path = prf
            if not file_path:
                continue
            diff_result = file_with_line_no_and_diff(repo, pr_number, file_path)
            if isinstance(diff_result, dict) and diff_result.get("success"):
                pr_file_diffs[file_path] = diff_result.get("data")
            content_result = get_file_content_at_ref(repo, file_path, head_sha)
            if isinstance(content_result, dict) and content_result.get("success"):
                pr_file_contents[file_path] = content_result.get("data")

        logger.info("[REVIEW] Fetched PR #{0}: base={1} head={2} files={3} diffs={4} contents={5}".format(
            pr_number,
            base_sha[:7] if base_sha else "",
            head_sha[:7],
            len(pr_files),
            len(pr_file_diffs),
            len(pr_file_contents),
        ))
        
        # STEP 2: Fetch Confluence content upfront
        try:
            conf_page_result = get_page_content(conf_page_id)
            conf_page_content = ""
            if isinstance(conf_page_result, dict):
                if conf_page_result.get("success"):
                    conf_data = conf_page_result.get("data")
                    if isinstance(conf_data, dict):
                        conf_page_content = str(
                            conf_data.get("content")
                            or conf_data.get("body")
                            or conf_data.get("storage")
                            or conf_data.get("text")
                            or conf_data
                        )
                    else:
                        conf_page_content = str(conf_data or "")
                else:
                    logger.warning("[REVIEW] Could not fetch Confluence page: {0}".format(conf_page_result.get("error", "Unknown")))
            else:
                conf_page_content = str(conf_page_result or "")

            logger.info("[REVIEW] Fetched Confluence page {0} ({1} chars)".format(conf_page_id, len(conf_page_content)))
        except Exception as e:
            logger.warning("[REVIEW] Could not fetch Confluence page: {0}".format(str(e)))
            conf_page_content = ""
        
        # STEP 3: Merge checklists and prepare unified check context.
        requested_checks: List[str] = []
        if pr_checklist:
            requested_checks.extend(pr_checklist)
        if conf_checklist:
            requested_checks.extend(conf_checklist)

        if not requested_checks:
            return {
                "success": False,
                "error": "No checks selected",
                "results": {}
            }

        # STEP 4: Map check identifiers to functions.
        # Frontend may send canonical IDs or human labels with small punctuation/spacing variations.
        check_mapping = {
            "doc_coverage": check_doc_coverage,
            "code_examples": check_code_examples,
            "api_signatures": check_api_signatures,
            "config_documented": check_config_documented,
            "architecture_alignment": check_architecture_alignment,
            "instructions_match": check_instructions_match,
            "error_handling": check_error_handling,
            "deprecated_removed": check_deprecated_removed,
            "terminology_consistent": check_terminology_consistent,
            "pr_references": check_pr_references,
            "code_path_sections": check_code_path_sections,
        }

        check_aliases = {
            "documentation coverage for new / changed code": "doc_coverage",
            "documentation coverage for new/changed code": "doc_coverage",
            "code examples match pr changes": "code_examples",
            "api / function signatures up to date": "api_signatures",
            "api/function signatures up to date": "api_signatures",
            "config / env variables documented": "config_documented",
            "config/env variables documented": "config_documented",
            "architecture / design alignment with pr": "architecture_alignment",
            "architecture/design alignment with pr": "architecture_alignment",
            "step-by-step instructions match implementation": "instructions_match",
            "error handling & edge cases documented": "error_handling",
            "deprecated features / apis removed from docs": "deprecated_removed",
            "deprecated features/apis removed from docs": "deprecated_removed",
            "consistent terminology between code and docs": "terminology_consistent",
            "links to pr / ticket references present": "pr_references",
            "links to pr/ticket references present": "pr_references",
            "missing sections for covered code paths": "code_path_sections",
        }

        def _normalize_check_id(raw_name: str) -> str:
            raw = str(raw_name or "").strip()
            if not raw:
                return ""
            direct = raw.lower()
            if direct in check_mapping:
                return direct
            return check_aliases.get(direct, "")

        # Normalize and de-duplicate selections so overlapping PR/Confluence checks
        # do not run twice or inflate summary counts.
        normalized_pairs: List[Tuple[str, str]] = []
        seen_check_ids = set()
        for requested_name in requested_checks:
            check_id = _normalize_check_id(requested_name)
            if not check_id or check_id in seen_check_ids:
                continue
            seen_check_ids.add(check_id)
            normalized_pairs.append((requested_name, check_id))

        if not normalized_pairs:
            return {
                "success": False,
                "error": "No supported checks selected",
                "results": {}
            }

        # STEP 5: Execute each check with BOTH PR and Confluence data
        results = {}
        logger.info("[REVIEW] Running {0} unique combined checks with both PR and Confluence content...".format(len(normalized_pairs)))

        # Define action map and helper functions for per-check detailed findings
        action_map = {
            "doc_coverage": "Add/expand Confluence sections for newly added or changed symbols in this PR.",
            "code_examples": "Update examples in Confluence so snippets match the current implementation and signatures.",
            "api_signatures": "Align documented function/class signatures with the latest PR code.",
            "config_documented": "Document new config/env variables with defaults, allowed values, and usage context.",
            "architecture_alignment": "Document design rationale or adjust implementation to match documented architecture.",
            "instructions_match": "Update step-by-step docs so setup/usage steps match the current code path.",
            "error_handling": "Add error and edge-case behavior notes to docs for changed code paths.",
            "deprecated_removed": "Remove deprecated APIs/features from docs or annotate migration guidance.",
            "terminology_consistent": "Normalize terms between code and docs to avoid naming drift.",
            "pr_references": "Add PR/ticket references in docs for traceability.",
            "code_path_sections": "Add missing sections for newly introduced files/modules/code paths.",
        }

        def _normalize_check_key(name: str) -> str:
            return str(name or "").strip().lower().replace(" ", "_")

        def _format_title(name: str) -> str:
            key = _normalize_check_key(name)
            title_map = {
                "doc_coverage": "Documentation Coverage",
                "code_examples": "Code Examples",
                "api_signatures": "API Signatures",
                "config_documented": "Configuration Documentation",
                "architecture_alignment": "Architecture Alignment",
                "instructions_match": "Instructions Match",
                "error_handling": "Error Handling",
                "deprecated_removed": "Deprecated Removed",
                "terminology_consistent": "Terminology Consistency",
                "pr_references": "PR References",
                "code_path_sections": "Code Path Sections",
            }
            return title_map.get(key, str(name or "check"))

        # Initialize detailed findings list
        detailed_findings: List[Dict[str, str]] = []
        passed_checks: List[str] = []

        for requested_name, check_id in normalized_pairs:
            logger.info("[REVIEW] >>> Running combined check: {0}".format(check_id))
            try:
                check_func = check_mapping[check_id]

                # Call check with BOTH PR and Confluence data
                result = check_func(
                    pr_content=pr_files,
                    pr_sha=head_sha,
                    conf_content=conf_page_content,
                    conf_page_id=conf_page_id,
                    repo=repo
                )
                results[check_id] = result
                logger.info("[REVIEW] <<< Finished check: {0}".format(check_id))

                key = _normalize_check_key(check_id)
                compliant = bool(isinstance(result, dict) and result.get("compliant", True))
                if not compliant or (isinstance(result, dict) and result.get("error")):
                    reason = "Flagged as non-compliant."
                    evidence = ""
                    if isinstance(result, dict):
                        err = str(result.get("error") or "").strip()
                        if err:
                            reason = "Check execution error: {0}".format(err)
                            evidence = "The check failed before producing normal metrics."
                        elif key == "doc_coverage":
                            cov = int(result.get("coverage_percentage") or 0)
                            pr_syms = int(result.get("pr_symbols") or 0)
                            doc_syms = int(result.get("documented_symbols") or 0)
                            threshold = 80
                            missing_syms = max(pr_syms - doc_syms, 0)
                            reason = (
                                "Documentation coverage is too low: only {0} of {1} changed code items are described in the docs ({2}% coverage, target {3}%)."
                            ).format(doc_syms, pr_syms, cov, threshold)
                            evidence = (
                                "Plain meaning: the PR changed {1} symbols, but the docs currently cover only {0} of them, so {2} changed items still have no matching documentation. "
                                "Metrics: coverage_percentage={3}%, documented_symbols={0}, pr_symbols={1}, missing_symbols={2}."
                            ).format(doc_syms, pr_syms, missing_syms, cov)
                        elif key == "api_signatures":
                            total = int(result.get("functions_in_pr") or 0)
                            documented = int(result.get("functions_documented") or 0)
                            missing = max(total - documented, 0)
                            reason = "API signature docs are incomplete: only {0} of {1} changed functions/classes have matching documentation.".format(documented, total)
                            evidence = (
                                "Plain meaning: {2} changed APIs have parameter lists, return values, defaults, or names in code that are not fully reflected in the docs. "
                                "Metrics: functions_in_pr={1}, functions_documented={0}, missing_signatures={2}."
                            ).format(documented, total, missing)
                        elif key == "config_documented":
                            total = int(result.get("new_config_vars") or 0)
                            documented = int(result.get("documented_vars") or 0)
                            missing = max(total - documented, 0)
                            reason = "Configuration docs are incomplete: only {0} of {1} config or env variables found in the changed code are documented well enough to use.".format(documented, total)
                            evidence = (
                                "Plain meaning: {2} settings appear in the changed code without enough documentation for someone to configure them safely. "
                                "Metrics: new_config_vars={1}, documented_vars={0}, missing_vars={2}."
                            ).format(documented, total, missing)
                        elif key == "code_examples":
                            in_docs = int(result.get("examples_in_docs") or 0)
                            matched = int(result.get("examples_matched") or 0)
                            mismatched = max(in_docs - matched, 0)
                            reason = "Code examples are out of date: only {0} of {1} reviewed examples still match the current implementation.".format(matched, in_docs)
                            evidence = (
                                "Plain meaning: {2} examples in the docs would likely mislead a reader because imports, signatures, or outputs no longer match the code. "
                                "Metrics: examples_in_docs={1}, examples_matched={0}, examples_needing_update={2}."
                            ).format(matched, in_docs, mismatched)
                        else:
                            findings = result.get("findings")
                            if isinstance(findings, list) and findings:
                                reason = str(findings[0])
                                if len(findings) > 1:
                                    reason += " (+{0} more findings)".format(len(findings) - 1)
                                evidence = "Total findings returned: {0}.".format(len(findings))

                    action = action_map.get(key, "Review this check result and update docs/code so they are aligned.")
                    if key == "doc_coverage":
                        action = "Start with the highest-impact missing items from the changed PR files. For each missing symbol, add what it does, its inputs/outputs, a small usage example, and any important edge cases or failure behavior."
                    elif key == "api_signatures":
                        action = "For each changed public function/class, align the docs to the code exactly: parameter names, parameter types, defaults, return values, and raised errors. Prioritize APIs that are user-facing or already referenced elsewhere in the page."
                    elif key == "config_documented":
                        action = "For each undocumented setting, add the variable name, what it controls, expected type, default value, allowed values if limited, whether it is required, and what changes at runtime when it is set incorrectly."
                    elif key == "code_examples":
                        action = "Update each stale example so the import path, function signature, arguments, and expected output match the current code, then verify the snippet still works before keeping it in the page."

                    finding_entry = {
                        "check": _format_title(check_id),
                        "status": "failed",
                        "why": reason,
                        "action": action,
                        "evidence": evidence,
                    }
                    detailed_findings.append(finding_entry)

                    finding_summary = "{0} | Why: {1} | Action: {2}".format(
                        finding_entry["check"],
                        reason[:120] + "..." if len(reason) > 120 else reason,
                        action[:120] + "..." if len(action) > 120 else action
                    )
                    if evidence:
                        finding_summary += " | Evidence: {0}".format(evidence[:120] + "..." if len(evidence) > 120 else evidence)
                    logger.info("[REVIEW] FINDING: {0}".format(finding_summary))
                else:
                    passed_title = _format_title(check_id)
                    passed_checks.append(passed_title)
                    logger.info("[REVIEW] PASS: {0} | No issues found in this check.".format(passed_title))
            except Exception as e:
                logger.error("[REVIEW] Check {0} failed: {1}".format(requested_name, str(e)))
                results[check_id] = {"error": str(e), "compliant": False}
                detailed_findings.append({
                    "check": _format_title(check_id),
                    "status": "failed",
                    "why": "Check execution error: {0}".format(str(e)),
                    "action": "Fix the reported runtime/tooling error and rerun this check.",
                    "evidence": "No compliance metrics produced because the check raised an exception.",
                })

        posted_summary_targets: List[str] = []
        github_inline_posted = 0
        confluence_inline_posted = 0
        posting_errors: List[str] = []

        summary_lines = [
            "Combined review completed for {0}#{1} and Confluence page {2}.".format(repo, pr_number, conf_page_id),
            "Checks run: {0}".format(len(results)),
            "Non-compliant checks: {0}".format(len(detailed_findings)),
            "Checks with no issues found: {0}".format(len(passed_checks)),
            "How to read the numbers: counts such as documented_symbols=63 or missing_vars=145 are item counts inside a check. They do not mean 63 or 145 separate review findings.",
        ]
        if detailed_findings:
            summary_lines.append("")
            summary_lines.append("Detailed findings:")
            for item in detailed_findings[:10]:
                summary_lines.append("- {0}".format(item["check"]))
                summary_lines.append("  Why flagged: {0}".format(item["why"]))
                if item.get("evidence"):
                    summary_lines.append("  Evidence / metric meaning: {0}".format(item["evidence"]))
                summary_lines.append("  What to do (specific): {0}".format(item["action"]))
                summary_lines.append("")

        if passed_checks:
            summary_lines.append("")
            summary_lines.append("Checks where no issues were found:")
            for chk in passed_checks:
                summary_lines.append("- {0}: no issues found".format(chk))

        summary_text = "\n".join(summary_lines)

        if not skip_footer:
            gh_footer = add_comment(repo, pr_number, summary_text)
            if isinstance(gh_footer, dict) and gh_footer.get("success"):
                posted_summary_targets.append("github")
            else:
                posting_errors.append("github_footer")

            conf_footer = post_footer_comment(conf_page_id, summary_text)
            if isinstance(conf_footer, dict) and conf_footer.get("success"):
                posted_summary_targets.append("confluence")
            else:
                posting_errors.append("confluence_footer")

        if not skip_inline and detailed_findings:
            pr_paths: List[str] = []
            for prf in pr_files:
                if isinstance(prf, dict):
                    fp = cast(str, prf.get("filename") or prf.get("path") or "")
                else:
                    fp = str(prf or "")
                if fp:
                    pr_paths.append(fp)

            if pr_paths:
                for idx, finding in enumerate(detailed_findings[:6]):
                    target_path = pr_paths[idx % len(pr_paths)]
                    body = "Combined review finding\n\nCheck: {0}\nWhy flagged: {1}\nWhat to do: {2}".format(
                        finding["check"],
                        finding["why"],
                        finding["action"],
                    )
                    if finding.get("evidence"):
                        body += "\nEvidence: {0}".format(finding["evidence"])
                    res = add_file_level_comment(repo, pr_number, head_sha, body, target_path)
                    if isinstance(res, dict) and res.get("success"):
                        github_inline_posted += 1
                    else:
                        posting_errors.append("github_inline:{0}".format(target_path))

            anchor = ""
            for line in str(conf_page_content or "").splitlines():
                cand = line.strip()
                if len(cand) >= 20:
                    anchor = cand[:180]
                    break
            if not anchor:
                anchor = str(conf_page_content or "")[:180].strip()

            if anchor:
                for finding in detailed_findings[:6]:
                    body = "Check: {0}\nWhy flagged: {1}\nWhat to do: {2}".format(
                        finding["check"],
                        finding["why"],
                        finding["action"],
                    )
                    if finding.get("evidence"):
                        body += "\nEvidence: {0}".format(finding["evidence"])
                    res = post_inline_comment(conf_page_id, body, anchor)
                    if isinstance(res, dict) and res.get("success"):
                        confluence_inline_posted += 1
                    else:
                        posting_errors.append("confluence_inline")

        # STEP 7: Compile and return results
        return {
            "success": True,
            "pr_number": pr_number,
            "pr_repo": repo,
            "conf_page_id": conf_page_id,
            "results": results,
            "summary": "Combined review completed with {0} checks".format(len(results)),
            "footer_posted": bool(posted_summary_targets),
            "footer_targets": posted_summary_targets,
            "comments_posted": github_inline_posted + confluence_inline_posted,
            "github_inline_posted": github_inline_posted,
            "confluence_inline_posted": confluence_inline_posted,
            "posting_errors": posting_errors,
            "detailed_findings": detailed_findings,
            "passed_checks": passed_checks,
        }
        
    except Exception as e:
        logger.error("[REVIEW] === Combined review FAILED: {0} ===".format(str(e)), exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "results": {}
        }


if __name__ == "__main__":
    mcp.run()
