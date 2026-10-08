from typing import Any


class ModelHubError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "client_error",
        request_id: str | None = None,
        retry_after: float | None = None,
        details: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after
        self.details = details


class AuthenticationError(ModelHubError):
    pass


class PermissionDeniedError(ModelHubError):
    pass


class BillingError(ModelHubError):
    pass


class RateLimitError(ModelHubError):
    pass


class ValidationError(ModelHubError):
    pass


class NotFoundError(ModelHubError):
    pass


class UnsupportedCapabilityError(ModelHubError):
    pass


class ProtocolError(ModelHubError):
    pass


class SubmissionUnknownError(ModelHubError):
    def __init__(self, message: str, *, candidate_task_uuid: str | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.candidate_task_uuid = candidate_task_uuid


class TaskError(ModelHubError):
    def __init__(self, message: str, *, task_uuid: str, task=None, **kwargs):
        super().__init__(message, **kwargs)
        self.task_uuid = task_uuid
        self.task = task


class TaskFailedError(TaskError):
    pass


class TaskCancelledError(TaskError):
    pass


class TaskWaitTimeoutError(TaskError):
    pass
