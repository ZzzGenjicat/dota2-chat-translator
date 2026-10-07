"""Bounded KeyValues reader shared by Steam discovery and GSI validation."""
from __future__ import annotations

import re

_TOKEN = re.compile(r'\s+|//[^\r\n]*|"(?:\\.|[^"\\])*"|[{}]|[^\s{}"\\]+')


def parse_keyvalues(text: str) -> dict:
    if len(text) > 1048576:
        raise ValueError("KeyValues size limit")
    tokens = []
    offset = 0
    text = text.lstrip("\ufeff")
    while offset < len(text):
        match = _TOKEN.match(text, offset)
        if match is None:
            raise ValueError("Invalid KeyValues token")
        token = match.group()
        offset = match.end()
        if not token.isspace() and not token.startswith("//"):
            tokens.append(token)
    position = 0

    def unquote(token: str) -> str:
        if token.startswith('"'):
            return re.sub(r'\\(["\\])', r'\1', token[1:-1])
        return token

    def block(depth: int = 0) -> dict:
        nonlocal position
        if depth > 16:
            raise ValueError("Config nesting limit")
        values = {}
        while position < len(tokens):
            key = tokens[position]
            position += 1
            if key == "}":
                if depth == 0:
                    raise ValueError("Unexpected closing brace")
                return values
            if key == "{" or position >= len(tokens):
                raise ValueError("Missing KeyValues value")
            key = unquote(key).lower()
            if not key or key in values:
                raise ValueError("Empty or duplicate KeyValues key")
            value = tokens[position]
            position += 1
            if value == "{":
                values[key] = block(depth + 1)
            elif value == "}":
                raise ValueError("Missing KeyValues value")
            else:
                values[key] = unquote(value)
        if depth:
            raise ValueError("Missing closing brace")
        return values

    return block()
