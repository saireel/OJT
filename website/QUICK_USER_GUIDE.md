# Quick User Guide

This guide covers two common tasks:
- Quick review (GitHub PR or Confluence page)
- Using your own instruction file/checklist

## 1. Quick GitHub PR Review

1. Open the app.
2. Click **GitHub PR Review** in the left sidebar.
3. Paste a PR link in this format:
   - `https://github.com/owner/repo/pull/123`
4. Click **Parse**.
5. Pick the checklist items you want (or use Select All / Deselect All).
6. Pick expected outputs (inline comments, summary, etc.).
7. Click **Start Review**.

What happens next:
- The panel closes and the request is sent to chat.
- Live progress appears while review runs.
- Comments are posted according to the expected outputs you selected.

## 2. Quick Confluence Page Review

1. Click **Confluence Page Review** in the left sidebar.
2. Paste a Confluence page URL or numeric page ID.
3. Click **Parse**.
4. Optional: choose a document type.
5. Pick checklist items (or use Select All / Deselect All).
6. Pick expected outputs.
7. Click **Start Review**.

What happens next:
- Live progress appears.
- Review comments/summary are posted based on selected outputs.

## 3. If You Have Your Own Instruction File

Current behavior:
- There is no direct "upload instruction file" button yet.

Best options right now:
1. Open your instruction file (.txt/.md) and paste the key instructions into chat before running review.
2. For repeated use, keep a reusable prompt template in a local file and paste it each time.
3. If your team stores instructions in Confluence, paste that link first, then your PR/page link and ask the assistant to follow those instructions.

Recommended instruction format (copy/paste):
- Scope: what to review
- Rules: must-check items
- Output format: what you want posted (inline, summary, rewrite suggestions)
- Exclusions: what to ignore

Example:

```text
Use this checklist for my next review:
- Validate naming conventions and consistency
- Flag long lines and unclear comments
- Report security-sensitive patterns
Expected output:
- Inline comments for exact lines
- One summary comment with top risks
```

## 4. Quick Tips

- Clicking outside a review panel closes it.
- You can use Ctrl+Enter in chat to send quickly.
- If no link is found, parse the link first in the panel to confirm format.
