from typing import Any

import pytest

from ai_review.services.vcs.types import (
    VCSClientProtocol,
    ReviewInfoSchema,
    ReviewThreadSchema,
    ReviewCommentSchema,
    InlineCommentCreateSchema,
)


class FakeVCSClient(VCSClientProtocol):
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.responses = responses or {}

    async def get_review_info(self) -> ReviewInfoSchema:
        self.calls.append(("get_review_info", (), {}))
        return self.responses.get(
            "get_review_info",
            ReviewInfoSchema(changed_files=["file.py"], base_sha="A", head_sha="B")
        )

    async def get_general_comments(self) -> list[ReviewCommentSchema]:
        self.calls.append(("get_general_comments", (), {}))
        return self.responses.get("get_general_comments", [])

    async def get_inline_comments(self) -> list[ReviewCommentSchema]:
        self.calls.append(("get_inline_comments", (), {}))
        return self.responses.get("get_inline_comments", [])

    async def create_general_comment(self, message: str) -> None:
        self.calls.append(("create_general_comment", (message,), {}))
        if error := self.responses.get("create_general_comment_error"):
            raise error

        return self.responses.get("create_general_comment_result", None)

    async def create_inline_comment(self, file: str, line: int, message: str) -> None:
        self.calls.append(("create_inline_comment", (file, line, message), {}))
        if error := self.responses.get("create_inline_comment_error"):
            raise error

        return self.responses.get("create_inline_comment_result", None)

    async def create_inline_comments(self, comments: list[InlineCommentCreateSchema]) -> None:
        self.calls.append(("create_inline_comments", (comments,), {}))
        if error := self.responses.get("create_inline_comments_error"):
            raise error
        if "create_inline_comments_result" in self.responses:
            return self.responses["create_inline_comments_result"]

        for comment in comments:
            await self.create_inline_comment(comment.file, comment.line, comment.message)

    async def delete_general_comment(self, comment_id: int | str) -> None:
        self.calls.append(("delete_general_comment", (comment_id,), {}))
        if error := self.responses.get("delete_general_comment_error"):
            raise error

        return self.responses.get("delete_general_comment_result", None)

    async def delete_inline_comment(self, comment_id: int | str) -> None:
        self.calls.append(("delete_inline_comment", (comment_id,), {}))
        if error := self.responses.get("delete_inline_comment_error"):
            raise error

        return self.responses.get("delete_inline_comment_result", None)

    async def create_inline_reply(self, thread_id: int | str, message: str) -> None:
        self.calls.append(("create_inline_reply", (thread_id, message), {}))
        if error := self.responses.get("create_inline_reply_error"):
            raise error

        return self.responses.get("create_inline_reply_result", None)

    async def create_summary_reply(self, thread_id: int | str, message: str) -> None:
        self.calls.append(("create_summary_reply", (thread_id, message), {}))
        if error := self.responses.get("create_summary_reply_error"):
            raise error

        return self.responses.get("create_summary_reply_result", None)

    async def get_inline_threads(self) -> list[ReviewThreadSchema]:
        self.calls.append(("get_inline_threads", (), {}))
        return self.responses.get("get_inline_threads", [])

    async def get_general_threads(self) -> list[ReviewThreadSchema]:
        self.calls.append(("get_general_threads", (), {}))
        return self.responses.get("get_general_threads", [])

    async def get_commit_url(self, sha: str) -> str | None:
        self.calls.append(("get_commit_url", (sha,), {}))
        return self.responses.get("get_commit_url", f"https://fakevcs.com/commit/{sha}")

    async def get_authenticated_user_login(self) -> str | None:
        self.calls.append(("get_authenticated_user_login", (), {}))
        return self.responses.get("get_authenticated_user_login", "ai-bot")

    async def request_reviewers(self, reviewers: list[str]) -> None:
        self.calls.append(("request_reviewers", (reviewers,), {}))
        if error := self.responses.get("request_reviewers_error"):
            raise error

    async def approve_pull_request(self, commit_id: str) -> None:
        self.calls.append(("approve_pull_request", (commit_id,), {}))
        if error := self.responses.get("approve_pull_request_error"):
            raise error

    async def submit_review(self, commit_id: str, event: str, body: str) -> None:
        self.calls.append(("submit_review", (commit_id, event, body), {}))
        if error := self.responses.get("submit_review_error"):
            raise error



    async def update_general_comment(self, comment_id: int | str, message: str) -> None:
        self.calls.append(("update_general_comment", (comment_id, message), {}))
        if error := self.responses.get("update_general_comment_error"):
            raise error



@pytest.fixture
def fake_vcs_client() -> FakeVCSClient:
    return FakeVCSClient()
