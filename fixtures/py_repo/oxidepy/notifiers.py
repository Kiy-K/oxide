"""Notification backends used when a retried operation finally gives up."""

from .retry import RetryPolicy


class Notifier:
    """Base class for delivery channels. Subclasses implement `notify`."""

    def notify(self, message: str) -> None:
        raise NotImplementedError


class EmailNotifier(Notifier):
    def __init__(self, address: str):
        self.address = address

    def notify(self, message: str) -> None:
        print(f"email to {self.address}: {message}")


class SlackNotifier(Notifier):
    def __init__(self, channel: str):
        self.channel = channel

    def notify(self, message: str) -> None:
        print(f"slack #{self.channel}: {message}")


# A call to should_retry(x, y) mentioned here in a comment must not count as
# a real call site for anything matching purely on text, not AST structure.
def notify_after_final_attempt(policy: RetryPolicy, notifier: Notifier, attempt: int, error: Exception) -> None:
    if not policy.should_retry(attempt, error):
        notifier.notify(f"giving up after attempt {attempt}: {error}")
