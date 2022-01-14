class NewJobRefused(Exception):
    """Exception raised when a new job is refused by the server."""

    pass


class JobAlreadyExists(Exception):
    """Exception raised when a job already exists."""

    pass
