"""Erreurs visibles par l'exploitant."""


class ConfigError(ValueError):
    """Configuration absente ou invalide."""


class SipUnavailableError(RuntimeError):
    """Les bindings PJSUA2 ne sont pas importables."""
