class AIProviderError(Exception):
    """Raised when the configured AI provider cannot complete a request."""

    def __init__(self, message, *, provider=None, retryable=False):
        self.provider = provider
        self.retryable = retryable
        super().__init__(message)


class AIConfigurationError(AIProviderError):
    """Missing credentials or an unknown provider name."""

    def __init__(self, message, *, provider=None):
        super().__init__(message, provider=provider, retryable=False)
