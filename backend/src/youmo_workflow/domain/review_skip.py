from youmo_workflow.domain.models import Contract


class ReviewSkip(Contract):
    task_id: str
    batch_id: str
    video_id: str
    content_id: str
    error_code: str
    input_sha256: str
    output_sha256: str
    attempts: int
    authorized_at: str
    publication_succeeded: bool = False


class SupplementalReviewSkip(ReviewSkip):
    execution_sha256: str
