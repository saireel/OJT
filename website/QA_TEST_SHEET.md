# Manual QA Sheet - MunnAI Web App

Execution Date: __________
Tester: __________
Environment (OS/Browser): __________
Backend URL: __________

---

## Legend

- **Status**: Pass / Fail / Blocked / N/A
- **Evidence**: screenshot path, log snippet, request ID, or brief note

---

## Column Fill Guidelines

### Actual Result
Write what *actually* happened when the step was executed.
Include specific output, messages, or behavior — especially where it differs from expected.
- Example: "Returned HTTP 200 with success=true and non-empty response."
- Example: "SSE emitted 4 progress events then 1 done event."
- Example: "Modal opened but the close button did not respond."

### Status
Single verdict — pick one:

| Value | Meaning |
|---|---|
| **Pass** | Actual result matches expected result |
| **Fail** | Actual result does not match expected result |
| **Blocked** | Could not run — missing dependency, server down, or missing token |
| **N/A** | Not applicable in this test run or environment |

### Evidence
Traceable proof supporting the status. Examples:
- Screenshot filename: `auth-clear-2026-04-21.png`
- Response body snippet: `{"success": true, "response": "..."}`
- Browser console error line copied verbatim
- Network tab entry showing endpoint + HTTP status + timestamp
- Video/screen recording filename

---

## How To Execute A Test Step

Use this rule when reading the **Steps / Input** column:
- If the step mentions the chat box, button clicks, modal actions, or page behavior, run it in the UI.
- If the step says **API-only**, run it with Postman, Thunder Client, curl, or the browser Network tab replay tool.
- You do **not** change the page URL for tests like chat validation. You usually stay on the same page and change the input or action.

## Test Cases

| ID | Area | Preconditions | Steps / Input | Expected Result | Actual Result | Status | Evidence |
|---|---|---|---|---|---|---|---|
| UI-01 | Page load | App running | Open `/` | Home page renders with no blocking error |  |  |  |
| UI-02 | Guide page | App running | Open `/quick-user-guide` | Guide page renders correctly |  |  |  |
| UI-03 | Modal smoke | Home page open | Open and close PR review, Confluence review, and quick review modals | All modals open/close with no UI break |  |  |  |
| UI-04 | Checklist controls | PR/Confluence panel open | Use Select All and Deselect All | All checklist items toggle correctly |  |  |  |
| UI-05 | Auth settings | Home page open | Open Auth Settings, switch view/edit, save/cancel/clear | Controls behave correctly and values persist per design |  |  |  |
| CHAT-01 | `/api/chat` validation | App running | In the chat box, leave the message empty and click Send. If the UI blocks empty sends, mark this as UI validation and verify the backend behavior separately as API-only. | 400 with `error` payload |  |  |  |
| CHAT-02 | `/api/chat` normal | App running | In the chat box, type a normal message such as `Summarize what this tool can do` and click Send. | 200 success with non-empty response |  |  |  |
| CHAT-03 | Fast greeting | App running | In the chat box, type `hi` and click Send. | Immediate smalltalk response from fast path |  |  |  |
| CHAT-04 | PR link context | App running | In the chat box, paste a message that includes a GitHub PR URL and click Send. | Response succeeds and link context does not break output |  |  |  |
| CHAT-05 | Confluence link context | App running | In the chat box, paste a message that includes a Confluence page URL and click Send. | Response succeeds and link context is handled |  |  |  |
| STREAM-01 | `/api/chat-stream` validation | App running | UI: try to send an empty chat message and confirm validation. API-only: send an empty `prompt` to `/api/chat-stream` and inspect the SSE response. | SSE error event returned |  |  |  |
| STREAM-02 | `/api/chat-stream` happy path | App running | In the chat box, send a normal message and watch the streamed response appear progressively. | Progress events then done event |  |  |  |
| STREAM-03 | Frontend fallback | App running | Simulate `/api/chat-stream` failure and send prompt in UI | UI falls back to `/api/chat` and still returns answer |  |  |  |
| STREAM-04 | Stream stability | App running | Run multiple streamed chats consecutively | No duplicated bubbles, no stale spinner, no console errors |  |  |  |
| FB-01 | Feedback submit | App running | Use the feedback control in the UI, or API-only send a valid feedback payload to `/api/feedback`. | Success true response |  |  |  |
| FB-02 | Feedback invalid rating | App running | API-only: send a feedback payload with an invalid rating value. | Error or safe validation handling, no crash |  |  |  |
| FB-03 | Feedback concurrency | App running | Submit feedback rapidly from 2 tabs | No corruption; entries remain readable |  |  |  |
| PR-01 | Checklist endpoint | App running | GET `/api/pr-checklist` | 200 with non-empty checklist list |  |  |  |
| PR-02 | Parse valid PR URL | App running | Paste a valid GitHub PR link into the PR input field and click Parse PR. API-only alternative: call `/api/parse-pr` directly. | owner, repo, pr_number parsed correctly |  |  |  |
| PR-03 | Parse invalid PR URL | App running | Paste an invalid PR link into the PR input field and click Parse PR. API-only alternative: call `/api/parse-pr` directly with a bad URL. | 400 Invalid PR URL format |  |  |  |
| PR-04 | Parse edge URL | App running | Paste a valid PR URL that includes query params or a trailing slash into the PR input field and click Parse PR. | Parse still succeeds if format is valid |  |  |  |
| PRR-01 | Review stream validation | App running | Start a PR review without providing a PR link or prompt content. If the UI prevents this, verify the backend behavior separately as API-only. | 400 error |  |  |  |
| PRR-02 | Review stream no auth | No GitHub creds configured | Enter a valid GitHub PR link in the PR review flow and start the review. | SSE error about GitHub credentials |  |  |  |
| PRR-03 | Review stream happy path | Valid GitHub creds | Enter a valid GitHub PR link in the PR review flow and start the review. | Progress milestones and done summary emitted |  |  |  |
| PRR-04 | Changed files summary | Valid GitHub creds | Run PR review on PR with multiple changed files | File count and per-file progress lines displayed |  |  |  |
| PRR-05 | Large PR handling | Valid GitHub creds | Run PR review on large PR | High-confidence mode message appears; review completes |  |  |  |
| PRR-06 | Duplicate request coalescing | Valid GitHub creds | Trigger same review request in parallel | One in-flight run reused; reuse message appears |  |  |  |
| PRR-07 | Inline limits/grouping | Valid GitHub creds | Review PR with many findings | Inline posted count respects cap/grouping config |  |  |  |
| PRR-08 | Tool failure handling | Valid GitHub creds | Simulate MCP/tool exception during review | SSE error returned, no server crash |  |  |  |
| CON-01 | Confluence stream validation | App running | Start a Confluence review without entering a page ID or link. If the UI prevents this, verify the backend behavior separately as API-only. | 400 error |  |  |  |
| CON-02 | Confluence stream no auth | No Confluence creds configured | Enter a page ID in the Confluence review flow and start the review. | SSE error about Confluence credentials |  |  |  |
| CON-03 | Confluence with page_id | Valid Confluence creds | Enter a valid page ID in the Confluence review field and start the review. | Page fetch succeeds and review starts |  |  |  |
| CON-04 | Confluence with URL | Valid Confluence creds | Paste a valid Confluence page URL into the Confluence review field and start the review. | Page ID extraction works; review proceeds |  |  |  |
| CON-05 | Invalid Confluence input | Valid Confluence creds | Paste an invalid Confluence page input and start the review. | SSE error for page ID extraction/fetch |  |  |  |
| CON-06 | Confluence summary integrity | Valid Confluence creds | Complete review run | Done summary includes issues, inline count, footer status |  |  |  |
| CON-07 | Inline failure reporting | Valid Confluence creds | Simulate inline comment post failures | Failure count appears in progress/final summary |  |  |  |
| AUTH-01 | GitHub-only flow | GitHub creds set only | Run PR review and Confluence review | PR works; Confluence fails with auth message |  |  |  |
| AUTH-02 | Confluence-only flow | Confluence creds set only | Run Confluence review and PR review | Confluence works; PR fails with auth message |  |  |  |
| AUTH-03 | Clear credentials | Existing creds set | Clear credentials then run both reviews | Both fail fast with clear credential errors |  |  |  |
| AUTH-04 | Token masking | Existing creds set | Inspect UI displayed auth values/log output | Secrets are masked in UI output per design |  |  |  |
| UX-01 | Chat bubble rendering | Home page open | Send multiple user/assistant messages | Bubbles render in order with markdown formatting |  |  |  |
| UX-02 | Progress tracker | Start PR/Confluence review | Observe progress panel during run | Progress updates appear continuously and remain readable |  |  |  |
| UX-03 | Quick review mode switching | Quick review modal open | Toggle PR/Confluence/Combined modes | Correct panel opens and matching controls shown |  |  |  |
| UX-04 | Mobile usability | Mobile viewport | Execute basic chat and open review modals | Layout remains usable; no hidden critical controls |  |  |  |
| REL-01 | Invalid GitHub token | Invalid GitHub creds | Run PR review | Clear auth failure, no hang |  |  |  |
| REL-02 | Invalid Confluence token | Invalid Confluence creds | Run Confluence review | Clear auth failure, no hang |  |  |  |
| REL-03 | Network timeout path | Introduce timeout/latency | Trigger stream endpoints | Error surfaced to user; UI remains responsive |  |  |  |
| REL-04 | Repeated long runs | Valid creds | Run several back-to-back PR/Confluence reviews | No thread leak symptoms or progressive slowdown |  |  |  |
| REL-05 | Shutdown behavior | App process stopping | Stop backend while idle and during load | MCP client shutdown occurs cleanly; no fatal traceback loop |  |  |  |
| REG-01 | No NameError after cleanup | App running | Run chat, PR review, and Confluence review | No NameError for queue, re, json, threading |  |  |  |
| REG-02 | queue.Empty handling | App running | Complete a full review stream run | Review completes; no queue.Empty exception |  |  |  |
| REG-03 | Removed deferred-check no impact | App running | Run full PR review with default checklist | Review output unchanged; no missing checks |  |  |  |

---

## API Payload Samples

### Parse PR
```json
{"url": "https://github.com/org/repo/pull/123"}
```

### Review Stream
```json
{
  "prompt": "Please review https://github.com/org/repo/pull/123",
  "checklist": ["Flake8", "Conventions", "Cross-file consistency"],
  "user_auth": {"github_owner": "org", "github_token": "***"}
}
```

### Confluence Review Stream
```json
{
  "page_input": "https://your-domain.atlassian.net/wiki/spaces/ABC/pages/123456789/My-Page",
  "user_auth": {"confluence_email": "user@company.com", "confluence_api_token": "***"}
}
```
