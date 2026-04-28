"""Composed GitHub API client built from focused action/review modules."""


from .github_actions import GitHubActions
from .github_review_actions import GitHubReviewActions
from .github_syntax_actions import GitHubSyntaxActions

class GitHubAPI(GitHubReviewActions, GitHubSyntaxActions, GitHubActions):
    """Facade class that preserves the original GitHubAPI public interface."""
    pass

github_api = GitHubAPI(
    base_url="https://api.github.com",
    owner="",
    github_token="",
)
