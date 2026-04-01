PR_REVIEW_CHECKLIST = [
    # Document Type Awareness (NEW - BIGGEST UPGRADE)
    {
        "id": "document_type_awareness",
        "name": "Document Type Awareness",
        "description": "Automatically detect document type (meeting notes, statistics, report, plan, guidelines) and apply type-specific checks for more relevant results.",
        "tool": "semantic_analysis",
        "enabled": True,
        "execution_order": 1,
    },
    # Code quality
    {
        "id": "code_quality",
        "name": "Code quality",
        "description": "Ensure code is clean, readable, and follows project conventions.",
        "tool": "static_analysis",
        "enabled": True,
        "execution_order": 10,
    },
    # Functionality
    {
        "id": "functionality",
        "name": "Functionality",
        "description": "Verify that the feature works as intended and does not break existing functionality.",
        "tool": "manual_test",
        "enabled": True,
        "execution_order": 20,
    },
    # Tests
    {
        "id": "tests",
        "name": "Tests coverage",
        "description": "Ensure new code is covered by unit or integration tests and existing tests pass.",
        "tool": "test_runner",
        "enabled": True,
        "execution_order": 30,
    },
    # Security
    {
        "id": "security",
        "name": "Security",
        "description": "Check for sensitive data exposure, input validation, and common vulnerabilities.",
        "tool": "security_scanner",
        "enabled": True,
        "execution_order": 40,
    },
    # Consistency Checks
    {
        "id": "consistency_checks",
        "name": "Consistency Checks",
        "description": "Detect same terms spelled differently, metrics with inconsistent values, and formatting variations.",
        "tool": "semantic_analysis",
        "enabled": True,
        "execution_order": 45,
    },
    # Documentation
    {
        "id": "documentation",
        "name": "Documentation",
        "description": "Verify that code changes are properly documented and PR description is clear.",
        "tool": "manual_check",
        "enabled": True,
        "execution_order": 50,
    },
    # Style consistency
    {
        "id": "style_consistency",
        "name": "Style and formatting",
        "description": "Ensure consistent code style, indentation, and naming conventions.",
        "tool": "linter",
        "enabled": True,
        "execution_order": 60,
    },
    # Dependencies
    {
        "id": "dependencies",
        "name": "Dependencies",
        "description": "Check if any new dependencies are justified and safe to include.",
        "tool": "manual_check",
        "enabled": True,
        "execution_order": 70,
    },
    # Merge readiness
    {
        "id": "merge_readiness",
        "name": "Merge readiness",
        "description": "Ensure branch is up to date, CI passes, and no conflicts exist.",
        "tool": "git_check",
        "enabled": True,
        "execution_order": 80,
    },
]
