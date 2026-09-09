"""Shared constants for the prelum application."""

# Safe characters allowed in filenames and template names.
# Includes alphanumeric characters, dot, hyphen, and underscore.
SAFE_FILENAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")

# Filename the renderer writes the inline template source to inside the temporary project.
INLINE_TEMPLATE_FILENAME = "main.typ"

# Header carrying the shared API token. The security scheme and the Sentry scrubber must name the
# same header: if they disagree, the credential ships to the error tracker with every captured 5xx.
API_TOKEN_HEADER = "X-Prelum-Api-Token"  # noqa: S105 - a header name, not a credential
