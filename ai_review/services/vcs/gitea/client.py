from ai_review.clients.gitea.client import get_gitea_http_client
from ai_review.clients.gitea.pr.schema.comments import GiteaCreateCommentRequestSchema
from ai_review.clients.gitea.pr.schema.reviews import (
    GiteaReviewInlineCommentSchema,
    GiteaCreateReviewRequestSchema,
)
from ai_review.config import settings
from ai_review.libs.logger import get_logger
from ai_review.services.vcs.gitea.adapter import (
    get_user_from_gitea_user,
    get_review_comment_from_gitea_comment,
    get_review_comment_from_gitea_review_comment,
)
from ai_review.services.vcs.types import (
    VCSClientProtocol,
    ThreadKind,
    BranchRefSchema,
    ReviewInfoSchema,
    ReviewThreadSchema,
    ReviewCommentSchema,
)

logger = get_logger("GITEA_VCS_CLIENT")


class GiteaVCSClient(VCSClientProtocol):
    def __init__(self):
        self.http_client = get_gitea_http_client()
        self.owner = settings.vcs.pipeline.owner
        self.repo = settings.vcs.pipeline.repo
        self.pull_number = settings.vcs.pipeline.pull_number
        self.pull_request_ref = f"{self.owner}/{self.repo}#{self.pull_number}"

    # --- Review info ---
    async def get_review_info(self) -> ReviewInfoSchema:
        try:
            pr = await self.http_client.pr.get_pull_request(
                owner=self.owner, repo=self.repo, pull_number=self.pull_number
            )
            files = await self.http_client.pr.get_files(
                owner=self.owner, repo=self.repo, pull_number=self.pull_number
            )

            logger.info(f"Fetched PR info for {self.pull_request_ref}")

            return ReviewInfoSchema(
                id=pr.number,
                title=pr.title,
                description=pr.body or "",
                author=get_user_from_gitea_user(pr.user),
                labels=[],
                base_sha=pr.base.sha,
                head_sha=pr.head.sha,
                assignees=[],
                reviewers=[],
                source_branch=BranchRefSchema(ref=pr.head.ref, sha=pr.head.sha),
                target_branch=BranchRefSchema(ref=pr.base.ref, sha=pr.base.sha),
                changed_files=[file.filename for file in files.root],
            )
        except Exception as error:
            logger.exception(f"Failed to fetch PR info {self.pull_request_ref}: {error}")
            return ReviewInfoSchema()

    # --- Comments ---
    async def get_general_comments(self) -> list[ReviewCommentSchema]:
        try:
            response = await self.http_client.pr.get_comments(
                owner=self.owner, repo=self.repo, pull_number=self.pull_number
            )
            logger.info(f"Fetched comments for {self.pull_request_ref}")

            return [get_review_comment_from_gitea_comment(comment) for comment in response.root]
        except Exception as error:
            logger.exception(f"Failed to fetch comments for {self.pull_request_ref}: {error}")
            return []

    async def get_inline_comments(self) -> list[ReviewCommentSchema]:
        try:
            reviews = await self.http_client.pr.get_reviews(
                owner=self.owner, repo=self.repo, pull_number=self.pull_number
            )
            logger.info(f"Fetched {len(reviews.root)} reviews for {self.pull_request_ref}")

            result: list[ReviewCommentSchema] = []
            seen_review_ids: set[int] = set()

            for review in reviews.root:
                comments = await self.http_client.pr.get_review_comments(
                    owner=self.owner,
                    repo=self.repo,
                    review_id=review.id,
                    pull_number=self.pull_number,
                )
                for comment in comments.root:
                    if review.id in seen_review_ids:
                        continue

                    seen_review_ids.add(review.id)
                    result.append(
                        get_review_comment_from_gitea_review_comment(comment, review_id=review.id)
                    )

            logger.info(f"Fetched {len(result)} inline review comments for {self.pull_request_ref}")
            return result
        except Exception as error:
            logger.exception(f"Failed to fetch inline comments for {self.pull_request_ref}: {error}")
            return []

    async def create_general_comment(self, message: str) -> None:
        try:
            logger.info(f"Posting general comment to PR {self.pull_request_ref}: {message}")
            request = GiteaCreateCommentRequestSchema(body=message)
            await self.http_client.pr.create_comment(
                owner=self.owner,
                repo=self.repo,
                pull_number=self.pull_number,
                request=request,
            )
            logger.info(f"Created general comment in PR {self.pull_request_ref}")
        except Exception as error:
            logger.exception(f"Failed to create general comment in PR {self.pull_request_ref}: {error}")
            raise

    async def create_inline_comment(self, file: str, line: int, message: str) -> None:
        try:
            logger.info(f"Posting inline comment in {self.pull_request_ref} at {file}:{line}: {message}")

            request = GiteaCreateReviewRequestSchema(
                body="Inline review",
                comments=[
                    GiteaReviewInlineCommentSchema(
                        path=file,
                        body=message,
                        new_position=line
                    )
                ],
            )
            await self.http_client.pr.create_review(
                owner=self.owner,
                repo=self.repo,
                pull_number=self.pull_number,
                request=request,
            )

            logger.info(f"Created inline comment in {self.pull_request_ref} at {file}:{line}")
        except Exception as error:
            logger.exception(f"Failed to create inline comment in {self.pull_request_ref} at {file}:{line}: {error}")
            raise

    async def delete_general_comment(self, comment_id: int | str) -> None:
        try:
            logger.info(f"Deleting general comment {comment_id=} in PR {self.pull_request_ref}")
            await self.http_client.pr.delete_issue_comment(
                owner=self.owner,
                repo=self.repo,
                comment_id=comment_id,
            )
            logger.info(f"Deleted general comment {comment_id=} in PR {self.pull_request_ref}")
        except Exception as error:
            logger.exception(f"Failed to delete general comment {comment_id=} in PR {self.pull_request_ref}: {error}")
            raise

    async def delete_inline_comment(self, comment_id: int | str) -> None:
        try:
            logger.info(f"Deleting review {comment_id=} in PR {self.pull_request_ref}")
            await self.http_client.pr.delete_review(
                owner=self.owner,
                repo=self.repo,
                pull_number=self.pull_number,
                review_id=comment_id,
            )
            logger.info(f"Deleted review {comment_id=} in PR {self.pull_request_ref}")
        except Exception as error:
            logger.exception(
                f"Failed to delete review {comment_id=} in PR {self.pull_request_ref}: {error}"
            )
            raise

    # --- Replies ---
    async def create_inline_reply(self, thread_id: int | str, message: str) -> None:
        logger.warning("Gitea does not support threaded replies — posting new general comment instead")
        await self.create_general_comment(message)

    async def create_summary_reply(self, thread_id: int | str, message: str) -> None:
        await self.create_general_comment(message)

    # --- Threads ---
    async def get_inline_threads(self) -> list[ReviewThreadSchema]:
        try:
            comments = await self.get_inline_comments()
            return [
                ReviewThreadSchema(
                    id=comment.thread_id,
                    kind=ThreadKind.INLINE,
                    file=comment.file,
                    line=comment.line,
                    comments=[comment],
                )
                for comment in comments
            ]
        except Exception as error:
            logger.exception(f"Failed to build inline threads for {self.pull_request_ref}: {error}")
            return []

    async def get_general_threads(self) -> list[ReviewThreadSchema]:
        try:
            comments = await self.get_general_comments()
            return [
                ReviewThreadSchema(
                    id=comment.thread_id,
                    kind=ThreadKind.SUMMARY,
                    comments=[comment],
                )
                for comment in comments
            ]
        except Exception as error:
            logger.exception(f"Failed to build general threads for {self.pull_request_ref}: {error}")
            return []

    async def get_commit_url(self, sha: str) -> str | None:
        try:
            api_url = str(settings.vcs.http_client.api_url_value)
            base_url = api_url.split("/api/v1")[0].rstrip("/")
            return f"{base_url}/{self.owner}/{self.repo}/commit/{sha}"
        except Exception:
            return None

    # --- Reviewer management & approvals ---
    async def get_authenticated_user_login(self) -> str | None:
        try:
            user = await self.http_client.pr.get_authenticated_user()
            return user.login
        except Exception as error:
            logger.exception(f"Failed to fetch authenticated user login: {error}")
            return None

    async def request_reviewers(self, reviewers: list[str]) -> None:
        try:
            await self.http_client.pr.request_reviewers(
                owner=self.owner,
                repo=self.repo,
                pull_number=self.pull_number,
                reviewers=reviewers,
            )
        except Exception as error:
            # Swallowed: failing to request reviewers is a non-blocking configuration-level error.
            # It should not fail the entire review run if code analysis succeeded.
            logger.exception(f"Failed to request reviewers {reviewers}: {error}")

    async def approve_pull_request(self, commit_id: str) -> None:
        await self.submit_review(commit_id=commit_id, event="APPROVED", body="Approved by AI reviewer")

    async def submit_review(self, commit_id: str, event: str, body: str) -> None:
        try:
            gitea_event = event
            if event == "APPROVE":
                gitea_event = "APPROVED"
            await self.http_client.pr.create_review(
                owner=self.owner,
                repo=self.repo,
                pull_number=self.pull_number,
                request=GiteaCreateReviewRequestSchema(
                    event=gitea_event,
                    body=body,
                    commit_id=commit_id,
                )
            )
        except Exception as error:
            # Swallowed: PR formal review failure is a non-critical permission/workflow action.
            # Swallowing it prevents crashing the run when review comments have been posted.
            logger.exception(f"Failed to submit review {event}: {error}")



    async def update_general_comment(self, comment_id: int | str, message: str) -> None:
        try:
            await self.http_client.pr.update_general_comment(
                owner=self.owner,
                repo=self.repo,
                comment_id=comment_id,
                message=message,
            )
        except Exception as error:
            # Re-raised: failing to update the summary comment is a data integrity error
            # that must be bubbled up to halt the pipeline and flag summary failure.
            logger.exception(f"Failed to update general comment {comment_id}: {error}")
            raise


