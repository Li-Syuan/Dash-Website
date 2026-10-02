"""Safe domain error types shared by services and presentation adapters."""


class ProviderUnavailable(Exception):
    """An opaque provider failure; raw details never become a user message."""
