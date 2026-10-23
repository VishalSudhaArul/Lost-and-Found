class WorkflowError(Exception):
    """A business rule prevented the action. The message is safe to show users."""
