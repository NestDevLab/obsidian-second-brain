"""Safe local credential loading for the dependency-free AMF client."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path


MAX_TOKEN_BYTES = 16 * 1024
BEARER_TOKEN = re.compile(r"^[A-Za-z0-9\-._~+/]+=*$")


def private_token(path: Path) -> str:
    """Read a bounded owner-only token without following any path symlinks."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("amf_token_file_unsafe")
    absolute = path.expanduser().absolute()
    parts = absolute.parts
    directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = -1
    try:
        for part in parts[1:-1]:
            opened_directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = opened_directory
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        os.close(directory)
        directory = -1
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_mode & 0o077
            or opened.st_uid not in {0, os.geteuid()}
            or not 1 <= opened.st_size <= MAX_TOKEN_BYTES
        ):
            raise ValueError("amf_token_file_unsafe")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            content = handle.read(MAX_TOKEN_BYTES + 1)
        if len(content) != opened.st_size:
            raise ValueError("amf_token_file_unsafe")
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "amf_token_file_unsafe":
            raise
        raise ValueError("amf_token_file_unsafe") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory >= 0:
            os.close(directory)
    try:
        token = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("amf_token_file_invalid") from error
    if token.endswith("\r\n"):
        token = token[:-2]
    elif token.endswith("\n"):
        token = token[:-1]
    if (
        not token
        or len(token.encode("utf-8")) > MAX_TOKEN_BYTES
        or BEARER_TOKEN.fullmatch(token) is None
    ):
        raise ValueError("amf_token_file_invalid")
    return token


def load_amf_token(environment: Mapping[str, str] | None = None) -> str | None:
    """Load the service credential file, or retain direct-env interactive use."""
    values = environment if environment is not None else os.environ
    token_file = values.get("OBSIDIAN_AMF_TOKEN_FILE")
    if token_file:
        return private_token(Path(token_file))
    return values.get("OBSIDIAN_AMF_TOKEN")
