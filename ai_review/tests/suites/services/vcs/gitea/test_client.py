import pytest

from ai_review.clients.gitea.pr.schema.reviews import (
    GiteaReviewSchema,
    GiteaReviewCommentSchema,
    GiteaGetReviewsResponseSchema,
    GiteaGetReviewCommentsResponseSchema,
)
from ai_review.clients.gitea.pr.schema.user import GiteaUserSchema
from ai_review.services.vcs.gitea.client import GiteaVCSClient
from ai_review.services.vcs.types import (
    ReviewInfoSchema,
    ReviewCommentSchema,
    ReviewThreadSchema,
    ThreadKind,
    InlineCommentCreateSchema,
)
from ai_review.tests.fixtures.clients.gitea import FakeGiteaPullRequestsHTTPClient


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_review_info_returns_valid_schema(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    info = await gitea_vcs_client.get_review_info()

    assert isinstance(info, ReviewInfoSchema)
    assert info.id == 1
    assert info.title == "Fake Gitea PR"
    assert info.author.username == "tester"
    assert "src/main.py" in info.changed_files
    assert info.source_branch.ref == "feature"
    assert info.target_branch.ref == "main"


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_general_comments_returns_list(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    comments = await gitea_vcs_client.get_general_comments()
    assert all(isinstance(comment, ReviewCommentSchema) for comment in comments)
    assert len(comments) > 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_inline_comments_fetches_from_reviews_api(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    comments = await gitea_vcs_client.get_inline_comments()
    assert isinstance(comments, list)
    assert all(isinstance(comment, ReviewCommentSchema) for comment in comments)
    assert len(comments) == 1
    assert comments[0].id == 500
    assert comments[0].body == "Inline review comment"
    assert comments[0].file == "src/main.py"
    assert comments[0].line == 10

    call_names = [name for name, _ in fake_gitea_pull_requests_http_client.calls]
    assert "get_reviews" in call_names
    assert "get_review_comments" in call_names
    assert "get_comments" not in call_names


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_create_general_comment_posts_comment(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.create_general_comment("Test comment")
    calls = [name for name, _ in fake_gitea_pull_requests_http_client.calls]
    assert "create_comment" in calls


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_create_inline_comment_posts_review(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.create_inline_comment(
        file="src/main.py",
        line=10,
        message="Inline comment",
    )

    calls = fake_gitea_pull_requests_http_client.calls
    review_calls = [payload for name, payload in calls if name == "create_review"]

    assert review_calls
    assert not any(name == "create_comment" for name, _ in calls)
    # Empty body avoids orphan "Inline review" conversation shells in Gitea.
    assert review_calls[0]["body"] == ""
    assert review_calls[0]["comments"][0]["path"] == "src/main.py"
    assert review_calls[0]["comments"][0]["body"] == "Inline comment"
    assert review_calls[0]["comments"][0]["new_position"] == 10


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_create_inline_comment_raises_on_error(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    async def fail_create_review(*_, **__):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        fake_gitea_pull_requests_http_client,
        "create_review",
        fail_create_review,
    )

    with pytest.raises(RuntimeError):
        await gitea_vcs_client.create_inline_comment(
            file="src/main.py",
            line=10,
            message="Inline comment",
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_create_inline_comments_posts_single_review_with_all_comments(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    """Option A: all findings go into one Gitea review (one conversation box)."""
    await gitea_vcs_client.create_inline_comments(
        [
            InlineCommentCreateSchema(file="src/main.py", line=10, message="First"),
            InlineCommentCreateSchema(file="src/main.py", line=20, message="Second"),
            InlineCommentCreateSchema(file="src/other.py", line=5, message="Third"),
        ]
    )

    review_calls = [
        payload
        for name, payload in fake_gitea_pull_requests_http_client.calls
        if name == "create_review"
    ]
    assert len(review_calls) == 1
    assert review_calls[0]["body"] == ""
    assert len(review_calls[0]["comments"]) == 3
    assert review_calls[0]["comments"][0]["path"] == "src/main.py"
    assert review_calls[0]["comments"][0]["new_position"] == 10
    assert review_calls[0]["comments"][0]["body"] == "First"
    assert review_calls[0]["comments"][1]["path"] == "src/main.py"
    assert review_calls[0]["comments"][1]["new_position"] == 20
    assert review_calls[0]["comments"][2]["path"] == "src/other.py"
    assert review_calls[0]["comments"][2]["new_position"] == 5


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_create_inline_comments_empty_is_noop(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.create_inline_comments([])
    assert not any(
        name == "create_review" for name, _ in fake_gitea_pull_requests_http_client.calls
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_inline_threads_groups_by_comment(
        gitea_vcs_client: GiteaVCSClient,
):
    threads = await gitea_vcs_client.get_inline_threads()
    assert all(isinstance(thread, ReviewThreadSchema) for thread in threads)
    assert all(thread.kind == ThreadKind.INLINE for thread in threads)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_general_threads_wraps_comments(
        gitea_vcs_client: GiteaVCSClient,
):
    threads = await gitea_vcs_client.get_general_threads()
    assert all(isinstance(thread, ReviewThreadSchema) for thread in threads)
    assert all(thread.kind == ThreadKind.SUMMARY for thread in threads)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_delete_general_comment_calls_delete_issue_comment(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    """Should delete a general PR comment by id."""
    comment_id = 123

    await gitea_vcs_client.delete_general_comment(comment_id)

    calls = [
        args for name, args in fake_gitea_pull_requests_http_client.calls
        if name == "delete_issue_comment"
    ]
    assert len(calls) == 1

    call_args = calls[0]
    assert call_args["comment_id"] == comment_id
    assert call_args["owner"] == "owner"
    assert call_args["repo"] == "repo"


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_delete_inline_comment_calls_delete_review(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    """Should delete an entire review by review id."""
    review_id = 500

    await gitea_vcs_client.delete_inline_comment(review_id)

    calls = [
        args for name, args in fake_gitea_pull_requests_http_client.calls
        if name == "delete_review"
    ]
    assert len(calls) == 1

    call_args = calls[0]
    assert call_args["review_id"] == review_id
    assert call_args["owner"] == "owner"
    assert call_args["repo"] == "repo"
    assert call_args["pull_number"] == "1"


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_inline_comments_returns_empty_when_no_reviews(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    async def return_empty_reviews(*_, **__):
        return GiteaGetReviewsResponseSchema(root=[])

    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_reviews", return_empty_reviews)

    comments = await gitea_vcs_client.get_inline_comments()
    assert comments == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_inline_comments_returns_empty_on_api_error(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    async def fail_get_reviews(*_, **__):
        raise RuntimeError("API error")

    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_reviews", fail_get_reviews)

    comments = await gitea_vcs_client.get_inline_comments()
    assert comments == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_inline_comments_returns_all_comments_in_review(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    """Every inline comment must be returned so clear/delete can remove the full set."""

    async def return_single_review(*_, **__):
        return GiteaGetReviewsResponseSchema(root=[
            GiteaReviewSchema(id=700, body="review", user=GiteaUserSchema(id=1, login="bot")),
        ])

    async def return_two_comments(*_, **__):
        return GiteaGetReviewCommentsResponseSchema(root=[
            GiteaReviewCommentSchema(id=801, body="first", path="a.py", position=1),
            GiteaReviewCommentSchema(id=802, body="second", path="b.py", position=2),
        ])

    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_reviews", return_single_review)
    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_review_comments", return_two_comments)

    comments = await gitea_vcs_client.get_inline_comments()
    assert len(comments) == 2
    assert {c.body for c in comments} == {"first", "second"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_inline_comments_handles_multiple_reviews(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    async def return_multiple_reviews(*_, **__):
        return GiteaGetReviewsResponseSchema(root=[
            GiteaReviewSchema(id=700, body="review 1"),
            GiteaReviewSchema(id=701, body="review 2"),
        ])

    review_comments = {
        700: GiteaGetReviewCommentsResponseSchema(root=[
            GiteaReviewCommentSchema(id=801, body="comment A", path="a.py", position=10),
        ]),
        701: GiteaGetReviewCommentsResponseSchema(root=[
            GiteaReviewCommentSchema(id=802, body="comment B", path="b.py", position=20),
        ]),
    }

    async def return_comments_for_review(*, owner, repo, pull_number, review_id):
        return review_comments[review_id]

    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_reviews", return_multiple_reviews)
    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_review_comments", return_comments_for_review)

    comments = await gitea_vcs_client.get_inline_comments()
    assert len(comments) == 2
    assert {c.id for c in comments} == {700, 701}
    assert {c.body for c in comments} == {"comment A", "comment B"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_inline_comments_skips_reviews_with_no_comments(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    async def return_review(*_, **__):
        return GiteaGetReviewsResponseSchema(root=[
            GiteaReviewSchema(id=700, body="empty review"),
        ])

    async def return_empty_comments(*_, **__):
        return GiteaGetReviewCommentsResponseSchema(root=[])

    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_reviews", return_review)
    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "get_review_comments", return_empty_comments)

    comments = await gitea_vcs_client.get_inline_comments()
    assert comments == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_create_inline_reply_falls_back_to_general_comment(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.create_inline_reply(thread_id=42, message="Reply text")

    call_names = [name for name, _ in fake_gitea_pull_requests_http_client.calls]
    assert "create_comment" in call_names
    assert "create_review" not in call_names


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_create_summary_reply_posts_general_comment(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.create_summary_reply(thread_id=99, message="Summary reply")

    call_names = [name for name, _ in fake_gitea_pull_requests_http_client.calls]
    assert "create_comment" in call_names


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_delete_inline_comment_raises_on_error(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    async def fail_delete_review(*_, **__):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "delete_review", fail_delete_review)

    with pytest.raises(RuntimeError):
        await gitea_vcs_client.delete_inline_comment(500)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_delete_general_comment_raises_on_error(
        monkeypatch: pytest.MonkeyPatch,
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    async def fail_delete(*_, **__):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(fake_gitea_pull_requests_http_client, "delete_issue_comment", fail_delete)

    with pytest.raises(RuntimeError):
        await gitea_vcs_client.delete_general_comment(123)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_authenticated_user_login(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    login = await gitea_vcs_client.get_authenticated_user_login()
    assert login == "ai-bot"
    assert any(name == "get_authenticated_user" for name, _ in fake_gitea_pull_requests_http_client.calls)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_request_reviewers(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.request_reviewers(["reviewer1"])
    assert any(
        name == "request_reviewers" and args["reviewers"] == ["reviewer1"]
        for name, args in fake_gitea_pull_requests_http_client.calls
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_approve_pull_request(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.approve_pull_request("sha123")
    review_calls = [
        args for name, args in fake_gitea_pull_requests_http_client.calls
        if name == "create_review"
    ]
    assert len(review_calls) == 1
    call_args = review_calls[0]
    assert call_args["event"] == "APPROVED"
    assert call_args["commit_id"] == "sha123"
    assert call_args["body"] == "Approved by AI reviewer"


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_submit_review(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.submit_review(commit_id="sha456", event="COMMENT", body="Comment body")
    review_calls = [
        args for name, args in fake_gitea_pull_requests_http_client.calls
        if name == "create_review"
    ]
    assert len(review_calls) == 1
    call_args = review_calls[0]
    assert call_args["event"] == "COMMENT"
    assert call_args["commit_id"] == "sha456"
    assert call_args["body"] == "Comment body"



@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_update_general_comment(
        gitea_vcs_client: GiteaVCSClient,
        fake_gitea_pull_requests_http_client: FakeGiteaPullRequestsHTTPClient,
):
    await gitea_vcs_client.update_general_comment(123, "New message")
    assert any(
        name == "update_general_comment" and args["comment_id"] == 123 and args["message"] == "New message"
        for name, args in fake_gitea_pull_requests_http_client.calls
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("gitea_http_client_config")
async def test_get_commit_url(
        gitea_vcs_client: GiteaVCSClient,
):
    url = await gitea_vcs_client.get_commit_url("sha1234567")
    assert url == "https://gitea.example.com/owner/repo/commit/sha1234567"


