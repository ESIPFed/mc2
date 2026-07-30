"""Custom exceptions for the Map Control SDK."""


class MapControlError(Exception):
    """Base exception for Map Control SDK."""
    pass


class ServerError(MapControlError):
    """Raised when the server returns an error."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Server error {status_code}: {detail}")


class NotFoundError(MapControlError):
    """Raised when a resource is not found."""
    pass


class ConnectionError(MapControlError):
    """Raised when unable to connect to the server."""
    pass
