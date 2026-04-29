import json
import re
import time

try:
    from .mcp_runtime import TOOL_REGISTRY, call_llm
    from .review_logic import (_get_cached_pr_checklist, _make_review_coalesce_key, _run_review_with_coalescing, _step_budget_for_request, _build_checklist_from_panel)
except ImportError:
    from mcp_runtime import TOOL_REGISTRY, call_llm
    from review_logic import (_get_cached_pr_checklist, _make_review_coalesce_key, _run_review_with_coalescing, _step_budget_for_request, _build_checklist_from_panel)
import threading
import queue

AGENT_SYSTEM_PROMPT = """You are an autonomous AI agent called MunnAI.

YOUR CORE RULES:
1. Always read and consider the user's ENTIRE prompt and conversation history first.
2. Think step by step before acting.
3. When given an instructions page and a target page, ALWAYS fetch the instructions page first before doing anything to the target page. Never assume what it says.
4. Only produce FINAL_ANSWER when the ENTIRE task is fully complete — not partially.
5. If a task requires applying an action to MULTIPLE items (e.g. every occurrence of a word, every section, every row), you MUST continue calling tools until ALL items are covered. Track completed vs remaining work in every THOUGHT.
6. If the user's intent is ambiguous, ask a clarifying question using FINAL_ANSWER.
7. Do NOT auto-review pages unless the instructions or user explicitly ask for it.
8. When an instructions page defines specific output requirements, derive a task contract from it and satisfy that exact contract. Do not substitute a generic template.
9. **For PR reviews: ENSURE that arguments are complete for tools to avoid losing a step then ALWAYS fetch get_files_in_pr and get_base_and_head_sha FIRST before posting any inline comments. These are prerequisites for inline commenting.**
10. For inline comments, try batch posting and try to optimize the use of tools so you don't necessarily do it one by one.

RESPONSE FORMAT — you must ALWAYS use one of these two formats:

Format 1 — when you need to use a tool:
THOUGHT: <your reasoning, what you have done so far, what still needs to be done>
TOOL_CALL: <tool_name>
ARGS: <json_arguments>

Format 2 — when the task is fully complete OR you need to ask for clarification:
THOUGHT: <your reasoning confirming task is complete>
FINAL_ANSWER: <your response to the user>

Before writing FINAL_ANSWER, always verify:
- "Have I completed ALL required actions?"
- "Are there remaining occurrences, items, or steps I missed?"
- "Does my output satisfy every requirement from the instructions?"
Only write FINAL_ANSWER when the answer to all three is YES.

---

AVAILABLE TOOLS:

1. review_confluence
   - Review a Confluence page for documentation quality.

2. review_pull_request
   - Review a GitHub pull request for code quality.

3. get_page_content
   - Get the full content of a Confluence page.

4. post_footer_comment
   - Post a footer/summary comment on a Confluence page.

5. post_inline_comment
   - Post an inline comment on specific text in a Confluence page.

6. create_space
   - Create a new Confluence space.

7. create_page
   - Create a new Confluence page.

8. update_page
   - Update a Confluence page's content.

9. find_and_replace
   - Find and replace text in a Confluence page.

10. add_pr_comment
   - Add a comment to a GitHub PR.

11. get_files_in_pr
   - Get the list of files changed in a GitHub PR.

12. file_with_line_no_and_diff
   - Get file content with line numbers and diff info.

13. get_base_and_head_sha
   - Get the base and head commit SHAs for a PR.

14. get_file_content
   - Get file content at a specific ref.

15. add_file_level_comment
   - Add a file-level comment to a PR.

16. add_inline_comment
   - Add an inline comment on a specific line in a PR.

17. show_comments
   - Get all comments on a PR.

18. reply_comment
   - Reply to a comment on a PR.

19. cleanup_old_bot_comments
   - Cleanup old bot comments on a PR.

20. list_repositories
   - List all repositories.

21. list_pull_requests
   - List pull requests in a repository.

22. summarize_pr_confluence
   - Summarize changes between PR and Confluence.

23. review_pull_request
   - Review a GitHub PR with coding standards checklist.

24. set_runtime_auth
   - Set runtime authentication credentials for GitHub and Confluence.



25. get_page_content_by_sections
   - Get content of a Confluence page by sections (chunked).

26. check_doc_coverage
   - Check documentation coverage for PR changes.

27. check_code_examples
   - Check if code examples match PR changes.

28. check_api_signatures
   - Check if API/function signatures are up to date.

29. check_config_documented
   - Check if config/env variables are documented.

30. check_architecture_alignment
   - Check architecture/design alignment with PR.

31. check_instructions_match
   - Check if step-by-step instructions match implementation.

32. check_error_handling
   - Check if error handling & edge cases are documented.

33. check_deprecated_removed
   - Check if deprecated features/APIs are removed from docs.

34. check_terminology
   - Check for consistent terminology between code and docs.

35. check_pr_references
   - Check if PR / ticket references are present.

36. check_code_path_sections
   - Check for missing sections for covered code paths.

---
   - Set runtime authentication credentials for GitHub and Confluence.


---

COMBINED REVIEW WORKFLOW
When the user asks for a combined review: review a confluence page with a github pull request:
    1. Fetch both PR and Confluence content upfront
    2. For each selected check, pass BOTH sources for cross-checking
    3. Aggregate and return results
---

PR REVIEW WORKFLOW:
When the user asks to review a GitHub pull request:
1. IMMEDIATELY call review_pull_request with the detected PR info and coding checklist.
2. Do NOT ask follow-up questions. Execute the review immediately.
3. If the user provides explicit review instructions, extend the checklist to cover every required check before calling the tool.
4. Write FINAL_ANSWER confirming the review was posted with both inline AND summary comments.

The review MUST cover ONLY the checklist items the user selected. Always use the user's chosen checks, not a default set.
For each checklist item, understand what it means and check the code thoroughly for it:

CHECKLIST ITEM DEFINITIONS:
- PascalCase Class Names: All class definitions should use PascalCase (e.g., MyClass, UserManager). Python classes especially.
- camelCase Function Names: Functions and methods should use camelCase (e.g., getUserData, calculateTotal).
- snake_case Python Functions: Python functions should use snake_case (e.g., get_user_data, calculate_total).
- Naming conventions: Check consistency across the codebase for variables, classes, functions, and constants.
- Hardcoded Secrets / Credentials: Look for hardcoded API keys, passwords, tokens, database credentials in strings or variables.
- Input Validation: Check that all user inputs are validated before use (length checks, type checks, range checks, etc).
- Proper Error Handling: Check that functions have try/except blocks, error messages are clear, and errors are properly logged.
- Comment Accuracy: Verify that code comments are accurate and match what the code actually does. Remove misleading comments.
- Consistent formatting and indentation: Check for consistent indentation, spacing, line breaks across all files.
- Code structure: Identify overly long/complex functions, missing abstractions, excessive nesting.
- Cross-file consistency: Verify naming patterns, design patterns, and behavior are consistent across related modules.
- Security patterns: Check for SQL injection risks, XSS vulnerabilities, missing input validation, insecure data handling.
- Documentation: Verify public functions have docstrings explaining parameters, return values, and exceptions.
- DRY violations: Look for duplicated code that should be extracted into a shared function.
- Unused code: Find variables, imports, functions that are defined but never used.

If no specific checklist is provided, default to:
- Naming conventions
- Input Validation
- Proper Error Handling
- Documentation
- Security patterns

When posting inline comments, always use this format:
  Issue: <what is wrong>
  Rule: <which convention or standard it violates>
  Fix: <specific recommended change>

---

CONTEXT-AWARE FLAGGING — avoid false positives:
1. Never flag a term based on the word alone. Always read the full sentence it appears in.
2. Before flagging any term, ask:
   - "Is this term commonly used in the subject area of this document?"
   - "Does the sentence make sense with this term?"
   - "Would removing or replacing it improve clarity, or lose meaning?"
   If the term fits the context, do NOT flag it.
3. If user feedback says a term is NOT noise in a given context, do not flag similar usage again.
4. When you DO flag a term, always explain WHY it seems out of place in that specific context.

---

AI-DRIVEN VERIFICATION:
You are responsible for verifying your own work. The system does NOT enforce hard-coded checks.

- OCCURRENCE TRACKING: Count occurrences yourself and track how many you have acted on. Do not stop until all are covered.
- INSTRUCTION COMPLIANCE: Read instructions, derive required actions, and verify each is met before finishing.
- GRAMMAR-ONLY vs FULL REVIEW: If the user asks for grammar-only, limit scope using your own judgment.
- FOOTER STRUCTURE: If instructions require a footer with specific sections, reason about what is needed from the instruction text and include all required sections.
- READABILITY ANALYSIS: If instructions ask for readability metrics (e.g. Flesch Reading Ease, Flesch-Kincaid Grade Level), compute or estimate them yourself and include them in your output.
"""

def format_result(data) -> str:
    """Returns the data as a string, pretty-printing dicts/lists as formatted JSON."""
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)

def _build_deterministic_execution_summary(user_msg: str, scratchpad: list) -> str:
    """Build a non-LLM summary based only on actual tool outputs."""
    total_calls = 0
    success_calls = 0
    failed_calls = 0
    inline_success = 0
    inline_failed = 0
    attempted_comments = []  # Track unposted comments
    last_errors: list[str] = []
    
    for entry in scratchpad:
        tool = entry.get("tool")
        if tool == "verifier":
            continue
        total_calls += 1
        raw = entry.get("raw_result")
        input_args = entry.get("input", {})
        
        if isinstance(raw, dict) and raw.get("success") is True:
            success_calls += 1
            if tool in ["add_inline_comment", "mcp_confluence_tool_add_inline_comment"]:
                inline_success += 1
        else:
            failed_calls += 1
            if tool in ["add_inline_comment", "mcp_confluence_tool_add_inline_comment"]:
                inline_failed += 1
                # Track attempted but failed inline comments
                attempted_comments.append({
                    "file": input_args.get("selected_path", "unknown file"),
                    "line_start": input_args.get("start_line", "?"),
                    "line_end": input_args.get("end_line", "?"),
                    "comment": input_args.get("comment_body", "")[:100],
                    "reason": raw.get("error", "Unknown error") if isinstance(raw, dict) else str(raw)[:100]
                })
            if isinstance(raw, dict):
                last_errors.append(raw.get("error", "Unknown error"))
            out = str(entry.get("output", ""))
            if "TOOL ERROR:" in out:
                last_errors.append(out.split("TOOL ERROR:")[-1][:100])
    
    lines = []
    lines.append("Execution summary (from actual tool results):")
    lines.append(f"- Request: {user_msg}")
    lines.append(f"- Tool calls attempted: {total_calls}")
    lines.append(f"- Successful tool calls: {success_calls}")
    lines.append(f"- Failed tool calls: {failed_calls}")
    lines.append(f"- Inline comments successfully posted: {inline_success}")
    lines.append(f"- Inline comment attempts failed: {inline_failed}")
    
    if inline_success == 0 and inline_failed > 0:
        lines.append("- Status: No inline comments were posted successfully. See below for attempted comments.")
    elif inline_failed > 0:
        lines.append(f"- Status: {inline_success} inline comments posted; {inline_failed} attempts failed. See below for failed attempts.")
    else:
        lines.append("- Status: Some inline comments were posted; verify page for full coverage.")
    
    # Include unposted comments in summary
    if attempted_comments:
        lines.append("")
        lines.append("Unposted inline comments (due to tool failures):")
        for i, comment in enumerate(attempted_comments, 1):
            lines.append(f"  {i}. File: {comment['file']} | Lines {comment['line_start']}-{comment['line_end']}")
            lines.append(f"     Comment: {comment['comment']}")
            lines.append(f"     Reason: {comment['reason']}")
    
    if last_errors:
        lines.append("")
        lines.append("Recent errors:")
        for err in last_errors[-3:]:
            lines.append(f"  * {err}")
    
    lines.append("- Note: This summary is deterministic and does not rely on LLM-generated claims.")
    return "\n".join(lines)

def _build_agent_prompt(
    system_prompt: str,
    link_context: str,
    history_context: str,
    user_msg: str,
    scratchpad: list,
    step: int,
) -> str:
    """Build the full prompt for the LLM at each agent step."""
    scratchpad_text = ""
    if scratchpad:
        scratchpad_text = "\n\n--- AGENT SCRATCHPAD (tools called so far) ---\n"
        for i, entry in enumerate(scratchpad):
            scratchpad_text += f"\n[Step {i+1}] Tool: {entry['tool']}\n"
            scratchpad_text += f"Input: {json.dumps(entry['input'], default=str)}\n"
            output_str = str(entry["output"])
            if len(output_str) > 4000:
                output_str = output_str[:4000] + "\n... (truncated for brevity)"
            scratchpad_text += f"Observation: {output_str}\n"
        scratchpad_text += "--- END SCRATCHPAD ---\n"
    verification_reminder = ""
    if scratchpad:
        verification_reminder = (
            "\n\nVERIFICATION REQUIRED: Review your scratchpad above. "
            "Have you completed ALL required actions? Are there any remaining occurrences, items, or steps? "
            "If the task is NOT fully done, continue with more tool calls. "
            "Only write FINAL_ANSWER when everything is complete."
        )
    return (
        system_prompt + "\n\n"
        + link_context
        + history_context
        + scratchpad_text
        + f"\nUser request: {user_msg}\n"
        + verification_reminder
        + f"\n\n[Agent step {step + 1}] What do you do next?"
    )

def _emit_progress(
    progress_callback,
    message: str,
    *,
    level: str = "info",
    phase: str = "",
    step: int | None = None,
    total_steps: int | None = None,
    tool: str = "",
) -> None:
    if not progress_callback:
        return
    payload = {"message": str(message)}
    if level:
        payload["level"] = level
    if phase:
        payload["phase"] = phase
    if isinstance(step, int):
        payload["step"] = str(step)
    if isinstance(total_steps, int):
        payload["total_steps"] = str(total_steps)
    if tool:
        payload["tool"] = tool
    progress_callback(payload)

def run_agent(user_msg: str, history: list, link_context: str, request_meta: dict | None = None, progress_callback=None) -> str:
    """
    ReAct + Verify-Then-Continue agent loop.
    LLM -> Tool -> Observation -> Verify -> LLM -> ... -> FINAL_ANSWER
    """
    scratchpad: list = []
    history_context = ""
    if history:
        recent = history[-20:]
        parts = []
        for entry in recent:
            role = "User" if entry.get("role") == "user" else "Assistant"
            parts.append(f"{role}: {entry.get('text', '')}")
        history_context = "Conversation history:\n" + "\n".join(parts) + "\n\n"
    request_meta = request_meta or {}
    page_ids = list(request_meta.get("page_ids") or [])
    prs = list(request_meta.get("prs") or [])
    review_type = str(request_meta.get("review_type") or "").strip().lower()
    doc_type = str(request_meta.get("doc_type") or "").strip()
    checklist_input = request_meta.get("checklist") or []
    outputs = list(request_meta.get("outputs") or [])
    confluence_checklist_page_id = str(request_meta.get("confluence_checklist_page_id") or "").strip()

    if not page_ids:
        page_ids = re.findall(r'page_id = "(\d+)"', link_context)
    if not prs:
        pr_matches = re.findall(r"PR: ([^/]+)/([^#]+)#(\d+)", link_context)
        prs = [
            {"owner": owner, "repo": repo, "pr_number": int(pr_num)}
            for owner, repo, pr_num in pr_matches
        ]

    if checklist_input and isinstance(checklist_input, list) and checklist_input and isinstance(checklist_input[0], dict):
        pr_checklist = checklist_input
    elif checklist_input:
        pr_checklist = _build_checklist_from_panel([str(item) for item in checklist_input])
    else:
        pr_checklist = _get_cached_pr_checklist()

    wants_inline = any("inline" in str(item).lower() for item in outputs) if outputs else True
    wants_combined_review = bool(
        page_ids
        and prs
        and (
            review_type in {"document and code", "code and document", "combined", "both", "document+code", "code+document"}
            or "document and code" in user_msg.lower()
            or "code and document" in user_msg.lower()
            or "documentation and code" in user_msg.lower()
            or doc_type
            or checklist_input
            or outputs
        )
    )

    def _render_tool_summary(result: object, default_message: str) -> tuple[bool, str, list[str]]:
        if not isinstance(result, dict):
            return False, str(result) if result is not None else default_message, []
        if not result.get("success"):
            return False, result.get("error", default_message), []
        data = result.get("data", {})
        if not isinstance(data, dict):
            return True, str(data) if data else default_message, []
        summary = str(data.get("summary", "")).strip() or default_message
        reviewed = data.get("reviewed_items", []) or []
        reviewed = [str(item) for item in reviewed if str(item).strip()]
        return True, summary, reviewed

    # OPTIMIZATION: Detect combined document/code reviews early and run both tools directly.
    if wants_combined_review:
        print("[AGENT] Detected combined document/code review request - calling Confluence and PR tools directly", flush=True)
        _emit_progress(progress_callback, "Detected combined document/code review request.", phase="detect")
        pr = prs[0]
        page_id = str(page_ids[0])
        repo_full = f"{pr['owner']}/{pr['repo']}"
        skip_inline = not wants_inline

        # Run both reviews in parallel using threads (non-blocking)
        result_queue = queue.Queue()
        confluence_result = None
        pr_result = None
        
        def _run_confluence_review():
            try:
                _emit_progress(progress_callback, "Running Confluence review...", phase="tool", tool="review_confluence")
                result = TOOL_REGISTRY["review_confluence"]({
                    "page_id": page_id,
                    "checklist_page_id": confluence_checklist_page_id,
                    "skip_inline": skip_inline,
                    "skip_footer": False,
                })
                _emit_progress(progress_callback, "Confluence review completed.", phase="tool", level="success", tool="review_confluence")
                result_queue.put(("confluence", result))
            except Exception as e:
                print(f"[AGENT] Confluence review error: {e}", flush=True)
                _emit_progress(progress_callback, f"Confluence review failed: {e}", phase="tool", level="warning", tool="review_confluence")
                result_queue.put(("confluence", {"success": False, "error": str(e)}))
        
        def _run_pr_review():
            try:
                _emit_progress(progress_callback, "Running pull request review...", phase="tool", tool="review_pull_request")
                result = TOOL_REGISTRY["review_pull_request"]({
                    "repo": repo_full,
                    "pr_number": int(pr["pr_number"]),
                    "checklist": pr_checklist,
                    "skip_inline": skip_inline,
                    "skip_footer": False,
                })
                _emit_progress(progress_callback, "Pull request review completed.", phase="tool", level="success", tool="review_pull_request")
                result_queue.put(("pr", result))
            except Exception as e:
                print(f"[AGENT] PR review error: {e}", flush=True)
                _emit_progress(progress_callback, f"Pull request review failed: {e}", phase="tool", level="warning", tool="review_pull_request")
                result_queue.put(("pr", {"success": False, "error": str(e)}))
        
        # Start both threads
        confluence_thread = threading.Thread(target=_run_confluence_review, daemon=True)
        pr_thread = threading.Thread(target=_run_pr_review, daemon=True)
        confluence_thread.start()
        pr_thread.start()
        
        # Wait for both to complete
        print("[AGENT] Running Confluence and PR reviews in parallel...", flush=True)
        _emit_progress(progress_callback, "Running Confluence and PR reviews in parallel.", phase="tool")
        confluence_thread.join()
        pr_thread.join()
        
        # Collect results from queue
        while not result_queue.empty():
            review_type, result = result_queue.get_nowait()
            if review_type == "confluence":
                confluence_result = result
            elif review_type == "pr":
                pr_result = result

        confluence_ok, confluence_summary, confluence_reviewed = _render_tool_summary(
            confluence_result,
            f"Review comments and a footer summary have been posted to Confluence page {page_id}.",
        )
        pr_ok, pr_summary, pr_reviewed = _render_tool_summary(
            pr_result,
            f"Review comments and a footer summary have been posted to PR {repo_full}#{pr['pr_number']}.",
        )

        lines = [
            f"Completed combined document/code review for {repo_full}#{pr['pr_number']} and Confluence page {page_id}.",
        ]
        if review_type:
            lines.append(f"Review type: {review_type}")
        elif doc_type:
            lines.append(f"Document type: {doc_type}")
        if checklist_input:
            lines.append(f"Checklist items: {', '.join(str(item) for item in checklist_input)}")
        if outputs:
            lines.append(f"Expected outputs: {', '.join(str(item) for item in outputs)}")
        lines.append("")
        lines.append(f"Confluence page {page_id}:")
        lines.append(f"- Status: {'success' if confluence_ok else 'failed'}")
        if confluence_reviewed:
            lines.append(f"- Reviewed: {', '.join(confluence_reviewed)}")
        lines.append(f"- {confluence_summary}")
        lines.append("")
        lines.append(f"GitHub PR {repo_full}#{pr['pr_number']}:")
        lines.append(f"- Status: {'success' if pr_ok else 'failed'}")
        if pr_reviewed:
            lines.append(f"- Reviewed: {', '.join(pr_reviewed)}")
        lines.append(f"- {pr_summary}")
        lines.append("")
        lines.append("Footer summaries were posted to both the Confluence page and the PR.")
        return "\n".join(lines)

    # OPTIMIZATION: Detect PR reviews early and call tool directly (skip LLM)
   # is_pr_review = "PR:" in link_context and any(term in user_msg.lower() for term in ["review", "check", "audit", "inspect"])
   # if is_pr_review:
        print("[AGENT] Detected PR review request - calling review_pull_request directly", flush=True)
        _emit_progress(progress_callback, "Detected PR review request.", phase="detect")
        pr_matches = re.findall(r"PR: ([^/]+)/([^#]+)#(\d+)", link_context)
        if pr_matches:
            owner, repo, pr_num = pr_matches[0]
            repo_full = f"{owner}/{repo}"
            checklist = pr_checklist  # Use user-selected checklist, not default
            review_key = _make_review_coalesce_key(
                repo_full,
                int(pr_num),
                checklist=checklist,
            )

            def _invoke_direct_review():
                return TOOL_REGISTRY["review_pull_request"]({
                    "repo": repo_full,
                    "pr_number": int(pr_num),
                    "checklist": checklist,
                })

            _emit_progress(progress_callback, f"Running PR review for {repo_full}#{pr_num}.", phase="tool", tool="review_pull_request")
            started_at = time.time()
            result, reused_inflight, waited_s = _run_review_with_coalescing(review_key, _invoke_direct_review)
            elapsed_ms = int((time.time() - started_at) * 1000)
            elapsed_s = elapsed_ms / 1000
            if isinstance(result, dict) and result.get("success"):
                _emit_progress(progress_callback, "PR review completed.", phase="complete", level="success", tool="review_pull_request")
                data = result.get("data", {})
                summary = data.get("summary", "") if isinstance(data, dict) else str(data)
                reviewed = data.get("reviewed_items", []) if isinstance(data, dict) else []
                lines = [
                    f"Completed PR review for {repo_full}#{pr_num} in {elapsed_s:.1f}s.",
                ]
                if reused_inflight:
                    lines.append(f"Reused an in-flight review result (waited {waited_s:.1f}s).")
                if reviewed:
                    lines.append(f"Reviewed: {', '.join(reviewed)}")
       #         if summary:
      #              lines.append(summary)
     #           else:
   ##                 lines.append("Review comments (inline + summary) have been posted to the PR.")
  #              return "\n".join(lines)
 #           else:
#                error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
    #            _emit_progress(progress_callback, f"PR review failed: {error}", phase="complete", level="error", tool="review_pull_request")
 #               return f"PR review for {repo_full}#{pr_num} failed: {error}"
    
    step_budget = _step_budget_for_request(user_msg)
    for step in range(step_budget):
        print(f"[AGENT] Step {step + 1}/{step_budget}", flush=True)
        _emit_progress(
            progress_callback,
            f"Analyzing request (step {step + 1}/{step_budget}).",
            phase="llm",
            step=step + 1,
            total_steps=step_budget,
        )
        prompt = _build_agent_prompt(
            AGENT_SYSTEM_PROMPT,
            link_context,
            history_context,
            user_msg,
            scratchpad,
            step,
        )
        response, err = call_llm(prompt)
        print(f"[AGENT] LLM response (step {step + 1}): {response[:600]}", flush=True)
        _emit_progress(
            progress_callback,
            f"Model response received for step {step + 1}.",
            phase="llm",
            step=step + 1,
            total_steps=step_budget,
        )
        if err:
            return f"Sorry, I encountered an error: {err}"
        if not response.strip():
            return "Sorry, I received an empty response. Please try again."
        # --- Check for FINAL_ANSWER ---
        final_match = re.search(r"FINAL_ANSWER:\s*(.+)", response, re.DOTALL)
        if final_match:
            final_answer = final_match.group(1).strip()
            print(f"[AGENT] Final answer at step {step + 1}", flush=True)
            _emit_progress(progress_callback, "Final answer ready.", phase="complete", level="success", step=step + 1, total_steps=step_budget)
            return final_answer
        # --- Check for TOOL_CALL ---
        tool_match = re.search(r"TOOL_CALL:\s*(.+?)\s*(?:\n|$)", response)
        if not tool_match:
            # No TOOL_CALL and no FINAL_ANSWER — strip THOUGHT prefix and return
            cleaned = re.sub(r"^THOUGHT:.*?\n", "", response, count=1, flags=re.DOTALL).strip()
            return cleaned if cleaned else response.strip()
        tool_name = tool_match.group(1).strip()
        # --- Parse ARGS ---
        args_match = re.search(r"ARGS:\s*(\{.+?\})\s*(?:\n|$)", response, re.DOTALL)
        args: dict = {}
        if args_match:
            try:
                args = json.loads(args_match.group(1))
            except json.JSONDecodeError as e:
                observation = f"ERROR: Could not parse ARGS as JSON: {e}. Raw: {args_match.group(1)}"
                scratchpad.append({"tool": tool_name, "input": args_match.group(1), "output": observation})
                continue
        # --- Validate tool ---
        if tool_name not in TOOL_REGISTRY:
            observation = f"ERROR: Unknown tool '{tool_name}'. Available: {', '.join(TOOL_REGISTRY.keys())}"
            scratchpad.append({"tool": tool_name, "input": args, "output": observation})
            continue
        print(f"[AGENT] Calling tool: {tool_name} | args: {json.dumps(args, default=str)[:300]}", flush=True)
        _tool_label = tool_name.replace("_", " ")
        _emit_progress(
            progress_callback,
            f"Calling tool: {_tool_label}.",
            phase="tool",
            step=step + 1,
            total_steps=step_budget,
            tool=tool_name,
        )
        # --- Execute tool ---
        if tool_name == "post_confluence_inline_comment" and args.get("page_id") and args.get("text_selection"):
            # Prevent repeated comments on match_index=0 by auto-advancing match_index.
            page_id = str(args.get("page_id"))
            text_selection = str(args.get("text_selection"))
            # Count how many successful inline comments were already posted for this selection
            already_done = sum(
                1 for entry in scratchpad
                if entry.get("tool") == "post_confluence_inline_comment"
                and str(entry.get("input", {}).get("page_id", "")) == page_id
                and str(entry.get("input", {}).get("text_selection", "")).strip().lower() == text_selection.strip().lower()
                and isinstance(entry.get("raw_result"), dict) and entry.get("raw_result", {}).get("success") is True
            )
            first_args = dict(args)
            if "match_index" not in first_args:
                first_args["match_index"] = already_done
            batch_limit = 30
            batch_results = []
            first_result = TOOL_REGISTRY[tool_name](first_args)
            print(f"[AGENT] Tool result: {str(first_result)[:400]}", flush=True)
            batch_results.append((first_args, first_result))
            total_occurrences = None
            if isinstance(first_result, dict) and first_result.get("success"):
                data = first_result.get("data", {})
                if isinstance(data, dict):
                    total_occurrences = data.get("occurrences_found")
            if isinstance(total_occurrences, int):
                start_idx = int(first_args.get("match_index", 0))
                end_exclusive = min(total_occurrences, start_idx + batch_limit)
                for idx in range(start_idx + 1, end_exclusive):
                    next_args = dict(args)
                    next_args["match_index"] = idx
                    next_result = TOOL_REGISTRY[tool_name](next_args)
                    print(f"[AGENT] Tool result (idx={idx}): {str(next_result)[:220]}", flush=True)
                    batch_results.append((next_args, next_result))
            for call_args, call_result in batch_results:
                if isinstance(call_result, dict):
                    if call_result.get("success"):
                        observation = format_result(call_result.get("data", {}))
                    else:
                        observation = f"TOOL ERROR: {call_result.get('error', 'Unknown error')}"
                else:
                    observation = format_result(call_result)
                scratchpad.append({
                    "tool": tool_name,
                    "input": call_args,
                    "output": observation,
                    "raw_result": call_result,
                })
            if isinstance(total_occurrences, int):
                # Count how many successful inline comments have been posted so far
                done_now = sum(
                    1 for entry in scratchpad
                    if entry.get("tool") == "post_confluence_inline_comment"
                    and str(entry.get("input", {}).get("page_id", "")) == page_id
                    and str(entry.get("input", {}).get("text_selection", "")).strip().lower() == text_selection.strip().lower()
                    and isinstance(entry.get("raw_result"), dict) and entry.get("raw_result", {}).get("success") is True
                )
                remaining = max(total_occurrences - done_now, 0)
                scratchpad.append({
                    "tool": "verifier",
                    "input": {"page_id": page_id, "text_selection": text_selection},
                    "output": f"Batch progress: total_occurrences={total_occurrences}, completed={done_now}, remaining={remaining}. Continue if remaining > 0.",
                    "raw_result": {"success": True},
                })
                _emit_progress(
                    progress_callback,
                    f"Inline comment batch progress: {done_now}/{total_occurrences} completed.",
                    phase="tool",
                    level="success" if remaining == 0 else "info",
                    step=step + 1,
                    total_steps=step_budget,
                    tool=tool_name,
                )
            continue
        result = TOOL_REGISTRY[tool_name](args)
        print(f"[AGENT] Tool result: {str(result)[:400]}", flush=True)
        # --- Format observation ---
        if isinstance(result, dict):
            if result.get("success"):
                observation = format_result(result.get("data", {}))
            else:
                observation = f"TOOL ERROR: {result.get('error', 'Unknown error')}"
        else:
            observation = format_result(result)
        scratchpad.append({
            "tool": tool_name,
            "input": args,
            "output": observation,
            "raw_result": result,
        })
        if isinstance(result, dict) and result.get("success"):
            _emit_progress(
                progress_callback,
                f"Tool completed: {_tool_label}.",
                phase="tool",
                level="success",
                step=step + 1,
                total_steps=step_budget,
                tool=tool_name,
            )
        else:
            _emit_progress(
                progress_callback,
                f"Tool attempt failed: {_tool_label}. Continuing...",
                phase="tool",
                level="warning",
                step=step + 1,
                total_steps=step_budget,
                tool=tool_name,
            )
        # --- MAX_STEPS reached: return deterministic execution report ---
    print(f"[AGENT] Max steps ({step_budget}) reached. Building final summary.", flush=True)
    _emit_progress(progress_callback, f"Reached max steps ({step_budget}); generating final summary.", phase="complete")
    
    # Build deterministic summary first (contains all the raw data)
    deterministic_summary = ""
    if scratchpad:
        deterministic_summary = _build_deterministic_execution_summary(user_msg, scratchpad)
    else:
        deterministic_summary = "No tool actions were executed before reaching the step limit."
    
    # Now ask LLM to generate a user-friendly summary
    print(f"[AGENT] Asking LLM to generate user-friendly final summary...", flush=True)
    _emit_progress(progress_callback, "Generating user-friendly summary with AI...", phase="complete")
    
    llm_summary_prompt = f"""Based on this execution summary from a review task, generate a professional, user-friendly summary highlighting:
    - What was completed successfully
    - Specify What issues were found
    - Explain the issues
    - What improvements to be done based from the issues found
    - Next steps if needed to be taken for the author
    - Any unposted items and why they failed
    

    Keep it concise but informative. Format with markdown headers and bullet points.

    Raw execution data:
    ---
    {deterministic_summary}
    ---

    Generate a polished summary:"""
    
    llm_summary, llm_err = call_llm(llm_summary_prompt)
    user_friendly_summary = llm_summary if llm_summary and not llm_err else deterministic_summary
    
    # POST SUMMARY AS FOOTER COMMENT (always, even on max steps)
    try:
        if page_ids:
            # Post to Confluence page
            page_id = str(page_ids[0])
            _emit_progress(progress_callback, f"Posting summary to Confluence page {page_id}...", phase="complete")
            footer_result = TOOL_REGISTRY["post_footer_comment"]({
                "page_id": page_id,
                "comment": user_friendly_summary,
            })
            if isinstance(footer_result, dict) and footer_result.get("success"):
                _emit_progress(progress_callback, "Summary posted to Confluence page.", phase="complete", level="success")
        
        if prs:
            # Post to GitHub PR
            pr = prs[0]
            repo_full = f"{pr['owner']}/{pr['repo']}"
            _emit_progress(progress_callback, f"Posting summary to PR {repo_full}#{pr['pr_number']}...", phase="complete")
            pr_result = TOOL_REGISTRY["add_pr_comment"]({
                "repo": repo_full,
                "pr_number": int(pr["pr_number"]),
                "comment_text": f"## Review Summary\n\n{user_friendly_summary}",
            })
            if isinstance(pr_result, dict) and pr_result.get("success"):
                _emit_progress(progress_callback, "Summary posted to PR.", phase="complete", level="success")
    except Exception as e:
        print(f"[AGENT] Error posting summary: {e}", flush=True)
    
    return user_friendly_summary

def _clean_review_line(line: str) -> str | None:
    """Clean a raw [REVIEW] stderr line for user-friendly SSE display.

    Returns cleaned text or None to suppress the line entirely.
    """
    # Skip duplicate INFO:/DEBUG: prefixed lines (they repeat the [REVIEW] message)
    if line.lstrip().startswith(("INFO:", "DEBUG:")):
        return None
    # Extract the message after [REVIEW]
    idx = line.find("[REVIEW]")
    if idx < 0:
        return None
    msg = line[idx + len("[REVIEW]"):].strip()
    if not msg:
        return None
    # Suppress noisy per-comment lines
    if msg.startswith("Inline comment posted"):
        return None  # batched into a counter instead
    if msg.startswith("Inline anchor failed"):
        return None
    # Clean up common prefixes for readability
    if msg.startswith(">>>"):
        msg = msg.replace(">>>", "▶", 1)  # ▶
    if msg.startswith("<<<"):
        msg = msg.replace("<<<", "✅", 1)  # ✅
    return f"  {msg}"

