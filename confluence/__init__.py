"""
Confluence API client and review orchestration package.

This package is organized as:
  - confluence_actions: Core API client and CRUD operations
  - confluence_syntax_actions: Text parsing, normalization, and language checking
  - confluence_review_actions: Review orchestration and issue detection checks
"""

from .confluence_actions import ConfluenceAPI, confluence_api
from .confluence_syntax_actions import SyntaxActions
from .confluence_review_actions import ReviewActions

__all__ = [
    "ConfluenceAPI",
    "confluence_api",
    "SyntaxActions",
    "ReviewActions",
]
