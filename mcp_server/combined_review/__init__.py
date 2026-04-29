"""Combined review module for GitHub PR and Confluence page reviews."""

"""
Short summary
--------------
Tools for cross-checking GitHub pull request changes against Confluence documentation
to ensure coverage, examples, API signature alignment, configuration and error-handling
documentation, and other cross-cutting concerns.

Description
-----------
This module provides a CombinedReviewChecks class that implements a variety of
checks which accept PR content (list of file objects with patches) and a Confluence
page body (string). It also exposes a handler registry (COMBINED_REVIEW_HANDLERS)
and a helper `execute_combined_check` for running a single check by id.

Example
-------
>>> from combined_review import execute_combined_check
>>> result = execute_combined_check(
...     "doc_coverage",
...     repo="org/repo",
...     pr_sha="abc123",
...     pr_content=[{"filename": "foo.py", "patch": "+def new_fn():\\n+    pass\\n"}],
...     conf_content="This page mentions new_fn"
... )
>>> print(result["coverage_percentage"])

Public API
----------
- CombinedReviewChecks
- COMBINED_REVIEW_HANDLERS
- execute_combined_check

Notes
-----
- Keep heavy imports out of top-level module code; the registry currently creates an instance of
  CombinedReviewChecks at import time — if constructing this class becomes expensive,
  consider lazy-instantiation (shown below).
- Use Sphinx automodule:: combined_review to render this docstring in your docs.
"""