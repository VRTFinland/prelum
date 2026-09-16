"""Shared constants for the prelum application."""

from pathlib import Path

# Spelled here as well as in pyproject.toml because importlib.metadata cannot answer for a virtual
# uv project (`package = false`, no [build-system]), and the runtime image carries no pyproject.toml
# to read instead. Bump both; the OpenAPI test fails if they drift.
VERSION = "1.2.0"

# Safe characters allowed in filenames and template names.
# Includes alphanumeric characters, dot, hyphen, and underscore.
SAFE_FILENAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")

# Filename the renderer writes the inline template source to inside the temporary project. The
# leading '~' is deliberately outside SAFE_FILENAME_CHARS, which is what makes the name
# unrepresentable as a files key: no key validate_inline_file_key accepts can name this file, in any
# letter case or as a leading directory, so the entry point needs no reserved-name rule to protect
# it. Never add '~' to SAFE_FILENAME_CHARS — that would silently restore the collision this avoids.
# The name never reaches a caller: it lives inside a TemporaryDirectory, typst's diagnostics are
# discarded, and the response filename comes from RenderOutput.
INLINE_TEMPLATE_FILENAME = "~main.typ"

# Header carrying the shared API token. The security scheme and the Sentry scrubber must name the
# same header: if they disagree, the credential ships to the error tracker with every captured 5xx.
API_TOKEN_HEADER = "X-Prelum-Api-Token"  # noqa: S105 - a header name, not a credential

# The wrapper that applies the per-render memory limit, pinned absolute rather than resolved
# through PATH. _typst_env deliberately keeps PATH so a bare cli_path resolves, but a control that
# bounds caller-supplied code must not take its identity from an operator's PATH ordering. Provided
# by util-linux, which the image installs explicitly; the limit is off unless configured, because
# this exists only on Linux.
PRLIMIT_PATH = Path("/usr/bin/prlimit")
