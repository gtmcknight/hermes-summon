"""X platform for Hermes Agent: tag your bot under any post and your Hermes answers in the thread."""


def register(ctx):
    # Import-light: the adapter (and its gateway imports) load only when Hermes registers the plugin.
    from .adapter import register as _register

    _register(ctx)


__all__ = ["register"]
