class WorkflowError(Exception):
    """Base error for workflow execution."""


class ValidationError(WorkflowError):
    pass


class ConflictError(WorkflowError):
    pass


class NotFoundError(WorkflowError):
    pass

