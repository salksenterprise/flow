"""ISRP orchestration error types.

Anything arising from workflow data or workflow state raises a WorkflowError,
so an embedding host can catch one type and roll its transaction back.

A misconfiguration — no database path, a call outside a transaction — raises
RuntimeError instead. That is a programming mistake in the host, not a
workflow outcome, and it should not be swallowed by a handler meant for
business failures.
"""


class WorkflowError(Exception):
    """Base error for workflow execution."""


class ValidationError(WorkflowError):
    """The request or definition is malformed."""


class ConflictError(WorkflowError):
    """The request is well formed but not allowed in the current state."""


class NotFoundError(WorkflowError):
    """The referenced workflow, step or job does not exist."""


class ExecutionError(WorkflowError):
    """The engine could not reach a stable state."""
