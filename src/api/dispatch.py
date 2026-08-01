"""Map GitHub webhook payloads into the existing polling review handler."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from github_ import (
    EventKind,
    Handler,
    IssueComment,
    PollEvent,
    PullRequestSummary,
    RepositoryHandle,
    parse_openrabbit_command,
)


class WebhookPayloadError(ValueError):
    """Raised when an actionable webhook payload is malformed or misrouted."""


@dataclass(frozen=True)
class WebhookDispatchRequest:
    """Validated work that can be handed to the shared review handler."""

    repository: str
    pr_number: int
    event_kind: EventKind | None = None
    pull_request: PullRequestSummary | None = None
    source_comment_id: int | None = None


@dataclass(frozen=True)
class WebhookDispatchPlan:
    """Mapping outcome for an accepted GitHub event."""

    repository: str
    request: WebhookDispatchRequest | None
    reason: str


class WebhookEventDispatcher:
    """Prepare webhook work and execute it through a shared review handler."""

    def __init__(
        self,
        *,
        expected_repository: str,
        handle: RepositoryHandle,
        handler: Handler,
    ) -> None:
        self._expected_repository = expected_repository
        self._handle = handle
        self._handler = handler

    def prepare(self, event_name: str, payload: dict[str, object]) -> WebhookDispatchPlan:
        """Validate and map a webhook payload without performing I/O."""
        return map_webhook_event(
            event_name,
            payload,
            expected_repository=self._expected_repository,
        )

    async def dispatch(self, request: WebhookDispatchRequest) -> None:
        """Run prepared work through the polling review handler."""
        if request.event_kind is not None and request.pull_request is not None:
            event = PollEvent(
                kind=request.event_kind,
                pull_request=request.pull_request,
            )
        else:
            pull_request = await self._handle.get_pull_request(request.pr_number)
            event = PollEvent(
                kind="pull_request_updated",
                pull_request=pull_request,
            )
        await self._handler(event, self._handle)


def map_webhook_event(
    event_name: str,
    payload: dict[str, object],
    *,
    expected_repository: str,
) -> WebhookDispatchPlan:
    """Map supported GitHub events into shared review-handler work."""
    repository = _repository_full_name(payload)
    if repository.casefold() != expected_repository.casefold():
        raise WebhookPayloadError("Webhook repository does not match repository.target.")

    if event_name == "ping":
        return WebhookDispatchPlan(repository=repository, request=None, reason="ping")
    if event_name == "pull_request":
        return _map_pull_request(payload, repository=repository)
    if event_name == "issue_comment":
        return _map_issue_comment(payload, repository=repository)
    raise WebhookPayloadError("GitHub webhook event is unsupported for dispatch.")


def _map_pull_request(
    payload: dict[str, object],
    *,
    repository: str,
) -> WebhookDispatchPlan:
    action = _required_string(payload, "action")
    event_kinds: dict[str, EventKind] = {
        "opened": "pull_request_opened",
        "reopened": "pull_request_opened",
        "ready_for_review": "pull_request_opened",
        "synchronize": "commit_pushed",
    }
    event_kind = event_kinds.get(action)
    if event_kind is None:
        return WebhookDispatchPlan(
            repository=repository,
            request=None,
            reason=f"pull_request_action_{action}_ignored",
        )

    raw_pull_request = payload.get("pull_request")
    if not isinstance(raw_pull_request, dict):
        raise WebhookPayloadError("Pull request webhook payload is missing pull_request data.")
    _validate_pull_request_base_repository(raw_pull_request, repository=repository)
    try:
        pull_request = PullRequestSummary.model_validate(raw_pull_request)
    except ValidationError as exc:
        raise WebhookPayloadError("Pull request webhook payload is malformed.") from exc
    return WebhookDispatchPlan(
        repository=repository,
        request=WebhookDispatchRequest(
            repository=repository,
            pr_number=pull_request.number,
            event_kind=event_kind,
            pull_request=pull_request,
        ),
        reason="scheduled",
    )


def _map_issue_comment(
    payload: dict[str, object],
    *,
    repository: str,
) -> WebhookDispatchPlan:
    action = _required_string(payload, "action")
    if action != "created":
        return WebhookDispatchPlan(
            repository=repository,
            request=None,
            reason=f"issue_comment_action_{action}_ignored",
        )

    issue = payload.get("issue")
    if not isinstance(issue, dict):
        raise WebhookPayloadError("Issue comment webhook payload is missing issue data.")
    if not isinstance(issue.get("pull_request"), dict):
        return WebhookDispatchPlan(
            repository=repository,
            request=None,
            reason="issue_comment_not_on_pull_request",
        )
    pr_number = issue.get("number")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        raise WebhookPayloadError("Issue comment webhook payload has an invalid issue number.")

    raw_comment = payload.get("comment")
    if not isinstance(raw_comment, dict):
        raise WebhookPayloadError("Issue comment webhook payload is missing comment data.")
    try:
        comment = IssueComment.model_validate(raw_comment)
    except ValidationError as exc:
        raise WebhookPayloadError("Issue comment webhook payload is malformed.") from exc
    if parse_openrabbit_command(comment.body) is None:
        return WebhookDispatchPlan(
            repository=repository,
            request=None,
            reason="issue_comment_without_command",
        )
    return WebhookDispatchPlan(
        repository=repository,
        request=WebhookDispatchRequest(
            repository=repository,
            pr_number=pr_number,
            source_comment_id=comment.id,
        ),
        reason="scheduled",
    )


def _repository_full_name(payload: dict[str, object]) -> str:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise WebhookPayloadError("GitHub webhook payload is missing repository data.")
    full_name = repository.get("full_name")
    if not isinstance(full_name, str) or not full_name.strip():
        raise WebhookPayloadError("GitHub webhook repository name is invalid.")
    return full_name.strip()


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WebhookPayloadError(f"GitHub webhook payload is missing {key}.")
    return value.strip().lower()


def _validate_pull_request_base_repository(
    pull_request: dict[str, object],
    *,
    repository: str,
) -> None:
    base = pull_request.get("base")
    if not isinstance(base, dict):
        return
    base_repository = base.get("repo")
    if base_repository is None:
        return
    if not isinstance(base_repository, dict):
        raise WebhookPayloadError("Pull request base repository metadata is malformed.")
    full_name = base_repository.get("full_name")
    if not isinstance(full_name, str) or not full_name.strip():
        raise WebhookPayloadError("Pull request base repository metadata is malformed.")
    if full_name.strip().casefold() != repository.casefold():
        raise WebhookPayloadError("Pull request base repository does not match repository.target.")
