import pytest

from ai_review.config import settings
from ai_review.services.review.gateway.review_comment_gateway import ReviewCommentGateway
from ai_review.services.review.internal.inline.schema import InlineCommentSchema, InlineCommentListSchema
from ai_review.services.review.internal.inline_reply.schema import InlineCommentReplySchema
from ai_review.services.review.internal.summary.schema import SummaryCommentSchema
from ai_review.services.review.internal.summary_reply.schema import SummaryCommentReplySchema
from ai_review.services.vcs.types import ReviewThreadSchema, ReviewCommentSchema, ThreadKind
from ai_review.tests.fixtures.services.artifacts import FakeArtifactsService
from ai_review.tests.fixtures.services.vcs import FakeVCSClient


# === INLINE THREADS ===

@pytest.mark.asyncio
async def test_get_inline_threads_filters_by_tag(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should return only threads containing AI inline tags."""
    threads = [
        ReviewThreadSchema(
            id="1",
            kind=ThreadKind.INLINE,
            file="a.py",
            comments=[ReviewCommentSchema(id="1", body=f"Hello {settings.review.inline_reply_tag}")]
        ),
        ReviewThreadSchema(
            id="2",
            kind=ThreadKind.INLINE,
            file="b.py",
            comments=[ReviewCommentSchema(id="2", body="No AI tag here")]
        ),
    ]
    fake_vcs_client.responses["get_inline_threads"] = threads

    result = await review_comment_gateway.get_inline_threads()

    assert len(result) == 1
    assert result[0].id == "1"
    assert any(call[0] == "get_inline_threads" for call in fake_vcs_client.calls)


@pytest.mark.asyncio
async def test_get_summary_threads_filters_by_tag(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should return only threads containing AI summary tags."""
    threads = [
        ReviewThreadSchema(
            id="10",
            kind=ThreadKind.SUMMARY,
            comments=[ReviewCommentSchema(id="1", body=f"AI {settings.review.summary_reply_tag}")]
        ),
        ReviewThreadSchema(
            id="11",
            kind=ThreadKind.SUMMARY,
            comments=[ReviewCommentSchema(id="2", body="No tags here")]
        ),
    ]
    fake_vcs_client.responses["get_general_threads"] = threads

    result = await review_comment_gateway.get_summary_threads()

    assert len(result) == 1
    assert result[0].id == "10"
    assert any(call[0] == "get_general_threads" for call in fake_vcs_client.calls)


# === GET INLINE COMMENTS ===

@pytest.mark.asyncio
async def test_get_inline_comments_filters_only_ai_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should return only inline comments containing AI inline tag."""
    fake_vcs_client.responses["get_inline_comments"] = [
        ReviewCommentSchema(id="1", body=f"{settings.review.inline_tag} AI comment"),
        ReviewCommentSchema(id="2", body="Regular inline comment"),
    ]

    result = await review_comment_gateway.get_inline_comments()

    assert len(result) == 1
    assert result[0].id == "1"

    assert any(call[0] == "get_inline_comments" for call in fake_vcs_client.calls)


@pytest.mark.asyncio
async def test_get_inline_comments_returns_empty_when_no_ai_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should return empty list when no AI inline comments exist."""
    fake_vcs_client.responses["get_inline_comments"] = [
        ReviewCommentSchema(id="1", body="Just a comment"),
    ]

    result = await review_comment_gateway.get_inline_comments()

    assert result == []


# === GET SUMMARY COMMENTS ===

@pytest.mark.asyncio
async def test_get_summary_comments_filters_only_ai_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should return only summary comments containing AI summary tag."""
    fake_vcs_client.responses["get_general_comments"] = [
        ReviewCommentSchema(id="10", body=f"{settings.review.summary_tag} AI summary"),
        ReviewCommentSchema(id="11", body="Regular summary"),
    ]

    result = await review_comment_gateway.get_summary_comments()

    assert len(result) == 1
    assert result[0].id == "10"

    assert any(call[0] == "get_general_comments" for call in fake_vcs_client.calls)


@pytest.mark.asyncio
async def test_get_summary_comments_returns_empty_when_no_ai_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should return empty list when no AI summary comments exist."""
    fake_vcs_client.responses["get_general_comments"] = [
        ReviewCommentSchema(id="1", body="Regular comment"),
    ]

    result = await review_comment_gateway.get_summary_comments()

    assert result == []


# === INLINE REPLY ===

@pytest.mark.asyncio
async def test_process_inline_reply_happy_path(
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should create inline reply and emit hook events."""
    reply = InlineCommentReplySchema(message="AI reply text")

    await review_comment_gateway.process_inline_reply("t1", reply)

    assert any(call[0] == "create_inline_reply" for call in fake_vcs_client.calls)

    assert ("save_vcs_inline_reply", {"thread_id": "t1", "reply": reply}) in fake_artifacts_service.calls


@pytest.mark.asyncio
async def test_process_inline_reply_error(
        capsys: pytest.CaptureFixture,
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should log and emit error if VCS fails to create reply."""

    async def failing_create_inline_reply(thread_id: str, body: str):
        raise RuntimeError("API error")

    fake_vcs_client.create_inline_reply = failing_create_inline_reply

    reply = InlineCommentReplySchema(message="AI reply text")
    await review_comment_gateway.process_inline_reply("t1", reply)
    output = capsys.readouterr().out

    assert "Failed to create inline reply" in output

    assert all(call[0] != "save_vcs_inline_reply" for call in fake_artifacts_service.calls)


# === SUMMARY REPLY ===

@pytest.mark.asyncio
async def test_process_summary_reply_success(
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should create summary reply comment."""
    reply = SummaryCommentReplySchema(text="AI summary reply")
    await review_comment_gateway.process_summary_reply("t42", reply)
    assert any(call[0] == "create_summary_reply" for call in fake_vcs_client.calls)

    assert ("save_vcs_summary_reply", {"thread_id": "t42", "reply": reply}) in fake_artifacts_service.calls


@pytest.mark.asyncio
async def test_process_summary_reply_error(
        capsys: pytest.CaptureFixture,
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should log and emit error on exception in summary reply."""

    async def failing_create_summary_reply(thread_id: str, body: str):
        raise RuntimeError("Network fail")

    fake_vcs_client.create_summary_reply = failing_create_summary_reply

    reply = SummaryCommentReplySchema(text="AI summary reply")
    await review_comment_gateway.process_summary_reply("t42", reply)
    output = capsys.readouterr().out

    assert "Failed to create summary reply" in output


# === INLINE COMMENT ===

@pytest.mark.asyncio
async def test_process_inline_comment_happy_path(
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should create inline comment via VCS."""
    comment = InlineCommentSchema(file="f.py", line=1, message="AI inline comment")
    await review_comment_gateway.process_inline_comment(comment)
    assert any(call[0] == "create_inline_comment" for call in fake_vcs_client.calls)

    assert ("save_vcs_inline", {"comment": comment}) in fake_artifacts_service.calls
    assert all(call[0] != "save_vcs_summary" for call in fake_artifacts_service.calls)
    assert all(call[0] != "save_vcs_summary_reply" for call in fake_artifacts_service.calls)


@pytest.mark.asyncio
async def test_process_inline_comment_error_fallback(
        capsys: pytest.CaptureFixture,
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should fall back to inline fallback comment when inline comment fails."""

    async def failing_create_inline_comment(file: str, line: int, message: str):
        raise RuntimeError("Failed to post inline")

    fake_vcs_client.create_inline_comment = failing_create_inline_comment

    comment = InlineCommentSchema(file="x.py", line=5, message="AI inline")
    await review_comment_gateway.process_inline_comment(comment)
    output = capsys.readouterr().out

    assert "Falling back to general comment" in output
    assert any(call[0] == "create_general_comment" for call in fake_vcs_client.calls)

    fallback_call = next(call for call in fake_vcs_client.calls if call[0] == "create_general_comment")
    posted_body = fallback_call[1][0]
    assert settings.review.inline_fallback_tag in posted_body
    assert settings.review.summary_tag not in posted_body

    assert all(call[0] != "save_vcs_inline" for call in fake_artifacts_service.calls)
    assert any(call[0] == "save_vcs_summary" for call in fake_artifacts_service.calls)


# === SUMMARY COMMENT ===

@pytest.mark.asyncio
async def test_process_summary_comment_happy_path(
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should create general summary comment successfully."""
    comment = SummaryCommentSchema(text="AI summary")
    await review_comment_gateway.process_summary_comment(comment)
    assert any(call[0] == "create_general_comment" for call in fake_vcs_client.calls)

    assert ("save_vcs_summary", {"comment": comment}) in fake_artifacts_service.calls


@pytest.mark.asyncio
async def test_process_summary_comment_error(
        capsys: pytest.CaptureFixture,
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should log error if summary comment creation fails."""

    async def failing_create_general_comment(body: str):
        raise RuntimeError("Backend down")

    fake_vcs_client.create_general_comment = failing_create_general_comment

    comment = SummaryCommentSchema(text="Broken")
    await review_comment_gateway.process_summary_comment(comment)
    output = capsys.readouterr().out

    assert "Failed to process summary comment" in output

    assert all(call[0] != "save_vcs_summary" for call in fake_artifacts_service.calls)


# === INLINE FALLBACK COMMENT ===

@pytest.mark.asyncio
async def test_process_inline_fallback_comment_happy_path(
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should create general comment with inline fallback tag."""
    comment = SummaryCommentSchema(text="**x.py:42** — missing check")
    await review_comment_gateway.process_inline_fallback_comment(comment)

    assert any(call[0] == "create_general_comment" for call in fake_vcs_client.calls)

    fallback_call = next(call for call in fake_vcs_client.calls if call[0] == "create_general_comment")
    posted_body = fallback_call[1][0]
    assert settings.review.inline_fallback_tag in posted_body
    assert settings.review.summary_tag not in posted_body

    assert ("save_vcs_summary", {"comment": comment}) in fake_artifacts_service.calls


@pytest.mark.asyncio
async def test_process_inline_fallback_comment_error(
        capsys: pytest.CaptureFixture,
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should log error if inline fallback comment creation fails."""

    async def failing_create_general_comment(body: str):
        raise RuntimeError("Backend down")

    fake_vcs_client.create_general_comment = failing_create_general_comment

    comment = SummaryCommentSchema(text="Broken fallback")
    await review_comment_gateway.process_inline_fallback_comment(comment)
    output = capsys.readouterr().out

    assert "Failed to process inline fallback comment" in output

    assert all(call[0] != "save_vcs_summary" for call in fake_artifacts_service.calls)


@pytest.mark.asyncio
async def test_process_inline_comments_calls_each(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should process all inline comments concurrently."""
    comments = InlineCommentListSchema(root=[
        InlineCommentSchema(file="a.py", line=1, message="c1"),
        InlineCommentSchema(file="b.py", line=2, message="c2"),
    ])

    await review_comment_gateway.process_inline_comments(comments)

    created = [call for call in fake_vcs_client.calls if call[0] == "create_inline_comment"]
    assert len(created) == 2


@pytest.mark.asyncio
async def test_process_inline_comment_error_no_fallback_when_disabled(
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        fake_vcs_client: FakeVCSClient,
        fake_artifacts_service: FakeArtifactsService,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should NOT fall back to summary comment when inline fallback is disabled."""
    monkeypatch.setattr(settings.review, "inline_comment_fallback", False)

    async def failing_create_inline_comment(file: str, line: int, message: str):
        raise RuntimeError("Failed to post inline")

    fake_vcs_client.create_inline_comment = failing_create_inline_comment

    comment = InlineCommentSchema(file="x.py", line=10, message="AI inline")
    await review_comment_gateway.process_inline_comment(comment)
    output = capsys.readouterr().out

    assert "Failed to process inline comment" in output
    assert "Falling back to general comment" not in output

    assert all(call[0] != "create_general_comment" for call in fake_vcs_client.calls)
    assert all(call[0] != "save_vcs_summary" for call in fake_artifacts_service.calls)
    assert all(call[0] != "save_vcs_inline" for call in fake_artifacts_service.calls)


@pytest.mark.asyncio
async def test_clear_inline_comments_deletes_all_ai_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should delete all existing AI inline comments."""
    fake_vcs_client.responses["get_inline_comments"] = [
        ReviewCommentSchema(id="1", body=f"{settings.review.inline_tag} comment 1"),
        ReviewCommentSchema(id="2", body=f"{settings.review.inline_tag} comment 2"),
    ]

    await review_comment_gateway.clear_inline_comments()

    deleted = [call for call in fake_vcs_client.calls if call[0] == "delete_inline_comment"]
    assert len(deleted) == 2
    assert {call[1][0] for call in deleted} == {"1", "2"}


@pytest.mark.asyncio
async def test_clear_inline_comments_noop_when_no_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should not call delete when no inline AI comments exist."""
    fake_vcs_client.responses["get_inline_comments"] = []

    await review_comment_gateway.clear_inline_comments()

    assert all(call[0] != "delete_inline_comment" for call in fake_vcs_client.calls)


@pytest.mark.asyncio
async def test_clear_summary_comments_deletes_all_ai_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should delete all existing AI summary comments."""
    fake_vcs_client.responses["get_general_comments"] = [
        ReviewCommentSchema(id="10", body=f"{settings.review.summary_tag} summary 1"),
        ReviewCommentSchema(id="11", body=f"{settings.review.summary_tag} summary 2"),
    ]

    await review_comment_gateway.clear_summary_comments()

    deleted = [call for call in fake_vcs_client.calls if call[0] == "delete_general_comment"]
    assert len(deleted) == 2
    assert {call[1][0] for call in deleted} == {"10", "11"}


@pytest.mark.asyncio
async def test_clear_summary_comments_noop_when_no_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Should not call delete when no summary AI comments exist."""
    fake_vcs_client.responses["get_general_comments"] = []

    await review_comment_gateway.clear_summary_comments()

    assert all(call[0] != "delete_general_comment" for call in fake_vcs_client.calls)


@pytest.mark.asyncio
async def test_get_summary_comments_excludes_fallback_comments(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """Summary comments detection should not include fallback-tagged comments."""
    fake_vcs_client.responses["get_general_comments"] = [
        ReviewCommentSchema(id="10", body=f"Summary {settings.review.summary_tag}"),
        ReviewCommentSchema(id="11", body=f"Fallback {settings.review.inline_fallback_tag}"),
    ]

    result = await review_comment_gateway.get_summary_comments()

    assert len(result) == 1
    assert result[0].id == "10"


def test_build_summary_body_with_history(
        review_comment_gateway: ReviewCommentGateway,
):
    """Prior reviews should be collapsed under <details>, not expanded inline."""
    new_text = "Status: Suggestions Only | Recommendation: Merge\n\nNew overview\n\n- file.py - 0 issues\n\n[abcdefg](http://commit/abcdefg)"
    old_body = "Status: 1 Issue Found | Recommendation: Address before merge\n\nOld overview\n\n- file.py - 1 issue(s)\n\n[1234567](http://commit/1234567)\n\n#ai-review-summary"

    result = review_comment_gateway.build_summary_body_with_history(new_text, old_body)

    assert result.startswith("Status: Suggestions Only | Recommendation: Merge")
    assert "<!-- ai-review-history-separator -->" in result
    assert "<details>" in result
    assert "</details>" in result
    assert "<summary>Previous review (1234567) — Status: 1 Issue Found | Recommendation: Address before merge</summary>" in result
    assert "Old overview" in result
    # Authoritative section stays expanded; history is after the separator.
    before, after = result.split("<!-- ai-review-history-separator -->", 1)
    assert "Old overview" not in before
    assert "Old overview" in after
    assert result.endswith(f"\n\n{settings.review.summary_tag}")


def test_build_summary_body_with_history_migrates_legacy_stack(
        review_comment_gateway: ReviewCommentGateway,
):
    """Already-stacked legacy history is re-wrapped into collapsible snapshots."""
    new_text = "Status: No Issues Found | Recommendation: Merge\n\nClean\n\n[aabbccd](http://c/aabbccd)"
    old_body = (
        "Status: 2 Issues Found | Recommendation: Address before merge\n\n"
        "Mid overview\n\n[2108ebb](http://c/2108ebb)\n\n"
        "<!-- ai-review-history-separator -->\n\n"
        "Current summary above is authoritative. Previous snapshots are kept for context only.\n\n"
        "### Previous review (commit 517ac94)\n"
        "Status: 4 Issues Found | Recommendation: Address before merge\n\n"
        "Oldest overview\n\n[517ac94](http://c/517ac94)\n\n"
        "#ai-review-summary"
    )

    result = review_comment_gateway.build_summary_body_with_history(new_text, old_body)

    assert result.count("<details>") == 2
    assert "Mid overview" in result
    assert "Oldest overview" in result
    assert "### Previous review" not in result
    assert "Current summary above is authoritative" not in result
    before, _ = result.split("<!-- ai-review-history-separator -->", 1)
    assert "Mid overview" not in before
    assert "Oldest overview" not in before


def test_build_summary_body_with_history_dedupes_and_limits(
        review_comment_gateway: ReviewCommentGateway,
):
    """Identical consecutive snapshots collapse; history is capped."""
    review_comment_gateway._SUMMARY_HISTORY_LIMIT = 2
    new_text = "Status: No Issues Found | Recommendation: Merge\n\nLatest\n\n[1111111](http://c/1111111)"
    snap_a = "Status: A\n\nA body\n\n[aaaaaaa](http://c/aaaaaaa)"
    snap_b = "Status: B\n\nB body\n\n[bbbbbbb](http://c/bbbbbbb)"
    snap_c = "Status: C\n\nC body\n\n[ccccccc](http://c/ccccccc)"
    old_body = (
        f"{snap_a}\n\n"
        "<!-- ai-review-history-separator -->\n\n"
        f"{review_comment_gateway._format_history_snapshot(snap_a)}\n\n"
        f"{review_comment_gateway._format_history_snapshot(snap_b)}\n\n"
        f"{review_comment_gateway._format_history_snapshot(snap_c)}\n\n"
        "#ai-review-summary"
    )

    result = review_comment_gateway.build_summary_body_with_history(new_text, old_body)

    # snap_a de-duped (current prev + first history), then snap_b; snap_c dropped by limit.
    assert result.count("<details>") == 2
    assert "A body" in result
    assert "B body" in result
    assert "C body" not in result


@pytest.mark.asyncio
async def test_inline_comment_failure_falls_back_and_tracks_finding(
        fake_vcs_client: FakeVCSClient,
        review_comment_gateway: ReviewCommentGateway,
):
    """When posting an inline comment fails, it falls back and is still tracked in created_inline_comments."""
    async def failing_create_inline_comment(file: str, line: int, message: str):
        raise RuntimeError("Failed to post inline")

    fake_vcs_client.create_inline_comment = failing_create_inline_comment

    from ai_review.services.review.internal.inline.schema import InlineCommentSchema
    comment = InlineCommentSchema(file="x.py", line=10, message="AI inline", severity="CRITICAL")
    await review_comment_gateway.process_inline_comment(comment)

    # Check that it fell back to a general comment
    assert any(call[0] == "create_general_comment" for call in fake_vcs_client.calls)

    # Check that it is still tracked in created_inline_comments
    assert len(review_comment_gateway.created_inline_comments) == 1
    assert review_comment_gateway.created_inline_comments[0].file == "x.py"
    assert review_comment_gateway.created_inline_comments[0].severity == "CRITICAL"


