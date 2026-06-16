"""Custom exception classes for TopicStreams API."""


class TopicStreamsException(Exception):
    def __init__(self, message: str, error_code: str | None = None):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)
