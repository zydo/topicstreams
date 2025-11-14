"""Custom exception classes for TopicStreams API."""

from typing import Optional


class TopicStreamsException(Exception):
    def __init__(self, message: str, error_code: Optional[str] = None):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class WebSocketError(TopicStreamsException):
    def __init__(self, message: str, connection_id: Optional[str] = None):
        self.connection_id = connection_id
        super().__init__(message, "WEBSOCKET_ERROR")


class NotificationError(TopicStreamsException):
    def __init__(self, message: str, payload: Optional[str] = None):
        self.payload = payload
        super().__init__(message, "NOTIFICATION_ERROR")
