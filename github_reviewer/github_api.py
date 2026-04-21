"""Composed GitHub API client built from focused action/review modules."""

import config

from .github_actions import GitHubActions
from .github_review_actions import GitHubReviewActions
from .github_syntax_actions import GitHubSyntaxActions

class GitHubAPI(GitHubReviewActions, GitHubSyntaxActions, GitHubActions):
    """Facade class that preserves the original GitHubAPI public interface."""
    pass

github_api = GitHubAPI(
    base_url=config.GITHUB_BASE_URL,
    owner=config.GITHUB_OWNER,
    github_token=config.GITHUB_TOKEN,
)
