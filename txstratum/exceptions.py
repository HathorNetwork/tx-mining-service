class NewJobRefused(Exception):
    """Exception raised when a new job is refused by the server."""

    pass


class JobAlreadyExists(Exception):
    """Exception raised when a job already exists."""

    pass


class InvalidVersionFormat(Exception):
    """Exception raised when comparing versions that are in the wrong format."""

    pass
