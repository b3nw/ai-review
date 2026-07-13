import pytest

from ai_review.services.cost.schema import CalculateCostSchema
from ai_review.services.review.gateway.review_agent_llm_gateway import ReviewAgentLLMGateway
from ai_review.services.review.gateway.review_comment_gateway import ReviewCommentGateway
from ai_review.services.review.gateway.review_direct_llm_gateway import ReviewDirectLLMGateway
from ai_review.services.review.gateway.review_dry_run_comment_gateway import ReviewDryRunCommentGateway
from ai_review.services.review.service import ReviewService
from ai_review.tests.fixtures.services.cost import FakeCostService
from ai_review.tests.fixtures.services.review.runner.context import FakeContextReviewRunner
from ai_review.tests.fixtures.services.review.runner.inline import FakeInlineReviewRunner
from ai_review.tests.fixtures.services.review.runner.inline_reply import FakeInlineReplyReviewRunner
from ai_review.tests.fixtures.services.review.runner.summary import FakeSummaryReviewRunner
from ai_review.tests.fixtures.services.review.runner.summary_reply import FakeSummaryReplyReviewRunner
from ai_review.tests.fixtures.services.vcs import FakeVCSClient



@pytest.mark.asyncio
async def test_run_inline_review_invokes_runner(
        review_service: ReviewService,
        fake_inline_review_runner: FakeInlineReviewRunner
):
    """Should call run() on InlineReviewRunner."""
    await review_service.run_inline_review()
    assert fake_inline_review_runner.calls == [("run", {})]


@pytest.mark.asyncio
async def test_run_context_review_invokes_runner(
        review_service: ReviewService,
        fake_context_review_runner: FakeContextReviewRunner
):
    """Should call run() on ContextReviewRunner."""
    await review_service.run_context_review()
    assert fake_context_review_runner.calls == [("run", {})]


@pytest.mark.asyncio
async def test_run_summary_review_invokes_runner(
        review_service: ReviewService,
        fake_summary_review_runner: FakeSummaryReviewRunner
):
    """Should call run() on SummaryReviewRunner."""
    await review_service.run_summary_review()
    assert fake_summary_review_runner.calls == [("run", {})]


@pytest.mark.asyncio
async def test_run_inline_reply_review_invokes_runner(
        review_service: ReviewService,
        fake_inline_reply_review_runner: FakeInlineReplyReviewRunner
):
    """Should call run() on InlineReplyReviewRunner."""
    await review_service.run_inline_reply_review()
    assert fake_inline_reply_review_runner.calls == [("run", {})]


@pytest.mark.asyncio
async def test_run_summary_reply_review_invokes_runner(
        review_service: ReviewService,
        fake_summary_reply_review_runner: FakeSummaryReplyReviewRunner
):
    """Should call run() on SummaryReplyReviewRunner."""
    await review_service.run_summary_reply_review()
    assert fake_summary_reply_review_runner.calls == [("run", {})]


def test_report_total_cost_with_data(
        capsys: pytest.CaptureFixture,
        review_service: ReviewService,
        fake_cost_service: FakeCostService
):
    """Should log total cost when cost report exists."""
    fake_cost_service.reports.append(
        fake_cost_service.calculate(
            result=CalculateCostSchema(
                prompt_tokens=50,
                completion_tokens=10,
            )
        )
    )

    review_service.report_total_cost()
    output = capsys.readouterr().out

    assert "TOTAL REVIEW COST" in output
    assert "fake-model" in output
    assert "0.006" in output


def test_report_total_cost_no_data(capsys: pytest.CaptureFixture, review_service: ReviewService):
    """Should log message when no cost data is available."""
    review_service.report_total_cost()
    output = capsys.readouterr().out

    assert "No cost data collected" in output


def test_review_service_uses_dry_run_comment_gateway(monkeypatch: pytest.MonkeyPatch):
    """Should use ReviewDryRunCommentGateway when settings.review.dry_run=True."""
    monkeypatch.setattr("ai_review.config.settings.review.dry_run", True)

    service = ReviewService()
    assert type(service.review_comment_gateway) is ReviewDryRunCommentGateway  # noqa


def test_review_service_uses_real_comment_gateway(monkeypatch: pytest.MonkeyPatch):
    """Should use normal ReviewCommentGateway when dry_run=False."""
    monkeypatch.setattr("ai_review.config.settings.review.dry_run", False)

    service = ReviewService()
    assert type(service.review_comment_gateway) is ReviewCommentGateway  # noqa


def test_review_service_initializes_agent_components():
    service = ReviewService()
    assert service.agent_loop is not None
    assert type(service.review_direct_llm_gateway) is ReviewDirectLLMGateway  # noqa


def test_review_service_uses_agent_gateway_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ai_review.config.settings.agent.enabled", True)
    service = ReviewService()
    assert type(service.review_llm_gateway) is ReviewAgentLLMGateway


def test_review_service_uses_default_gateway_when_agent_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("ai_review.config.settings.agent.enabled", False)
    service = ReviewService()
    assert type(service.review_llm_gateway) is ReviewDirectLLMGateway


@pytest.mark.asyncio
async def test_assign_reviewer_uses_configured_username(
        monkeypatch: pytest.MonkeyPatch,
        review_service: ReviewService,
        fake_vcs_client: FakeVCSClient,
):
    """Should use settings.review.reviewer_username when configured, avoiding get_authenticated_user_login."""
    from ai_review.config import settings
    monkeypatch.setattr(settings.review, "reviewer_username", "my-custom-reviewer")
    monkeypatch.setattr(review_service, "vcs", fake_vcs_client)

    await review_service.assign_reviewer()

    # Verify get_authenticated_user_login was NOT called
    assert not any(call[0] == "get_authenticated_user_login" for call in fake_vcs_client.calls)

    # Verify request_reviewers was called with configured username
    req_calls = [call for call in fake_vcs_client.calls if call[0] == "request_reviewers"]
    assert len(req_calls) == 1
    assert req_calls[0][1][0] == ["my-custom-reviewer"]


@pytest.mark.asyncio
async def test_assign_reviewer_fallback_to_user(
        monkeypatch: pytest.MonkeyPatch,
        review_service: ReviewService,
        fake_vcs_client: FakeVCSClient,
):
    """Should fallback to get_authenticated_user_login when no reviewer_username is configured."""
    from ai_review.config import settings
    monkeypatch.setattr(settings.review, "reviewer_username", None)
    monkeypatch.setattr(review_service, "vcs", fake_vcs_client)
    fake_vcs_client.responses["get_authenticated_user_login"] = "authed-bot-user"

    await review_service.assign_reviewer()

    # Verify get_authenticated_user_login WAS called
    assert any(call[0] == "get_authenticated_user_login" for call in fake_vcs_client.calls)

    # Verify request_reviewers was called with discovered username
    req_calls = [call for call in fake_vcs_client.calls if call[0] == "request_reviewers"]
    assert len(req_calls) == 1
    assert req_calls[0][1][0] == ["authed-bot-user"]


@pytest.mark.asyncio
async def test_assign_reviewer_user_failure_is_non_blocking(
        monkeypatch: pytest.MonkeyPatch,
        review_service: ReviewService,
        fake_vcs_client: FakeVCSClient,
):
    """Discovery and assignment errors should be non-blocking."""
    from ai_review.config import settings
    monkeypatch.setattr(settings.review, "reviewer_username", None)
    monkeypatch.setattr(review_service, "vcs", fake_vcs_client)
    fake_vcs_client.responses["get_authenticated_user_login"] = None  # fails discovery

    # Should not raise exception
    await review_service.assign_reviewer()

    # Now let discovery succeed but assignment fail
    fake_vcs_client.responses["get_authenticated_user_login"] = "authed-bot-user"
    fake_vcs_client.responses["request_reviewers_error"] = RuntimeError("API error")

    # Should still not raise exception (non-blocking)
    await review_service.assign_reviewer()


