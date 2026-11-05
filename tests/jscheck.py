"""Tiny static checks for the page's JavaScript (the dev container has no JS engine).

Not a parser: it tokenises strings, template literals (with nested ``${...}``),
comments and regex literals well enough to check that brackets balance, and lists
the ``$("id")`` element ids a script uses. Catches the typical editing slip (an
unclosed brace or bracket) that would stop the whole module from loading.
"""

from __future__ import annotations

import re

_PAIRS = {")": "(", "]": "[", "}": "{"}
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^") | {""}
_KEYWORDS_BEFORE_REGEX = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw"}


class JSCheckError(ValueError):
    pass


def check_brackets(src: str) -> None:
    stack: list[tuple[str, int]] = []  # (bracket, line); "`" marks a template literal
    i, n, line = 0, len(src), 1
    prev = ""  # last significant character outside strings/comments
    prev_word = ""
    while i < n:
        c = src[i]
        if stack and stack[-1][0] == "`":  # inside a template literal
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
                prev = "a"
            elif c == "$" and i + 1 < n and src[i + 1] == "{":
                stack.append(("${", line))
                i += 2
                prev = "{"
                continue
            elif c == "\n":
                line += 1
            i += 1
            continue
        if c == "\n":
            line += 1
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise JSCheckError(f"line {line}: unclosed /* comment")
            line += src.count("\n", i, j)
            i = j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n":
                    raise JSCheckError(f"line {line}: unterminated string")
                j += 1
            if j >= n:
                raise JSCheckError(f"line {line}: unterminated string")
            i = j + 1
            prev = "a"
            continue
        if c == "`":
            stack.append(("`", line))
            i += 1
            continue
        if c == "/" and (prev in _REGEX_PREV or prev_word in _KEYWORDS_BEFORE_REGEX):  # regex literal
            j, in_class = i + 1, False
            while j < n:
                d = src[j]
                if d == "\\":
                    j += 2
                    continue
                if d == "\n":
                    raise JSCheckError(f"line {line}: unterminated regex")
                if d == "[":
                    in_class = True
                elif d == "]":
                    in_class = False
                elif d == "/" and not in_class:
                    break
                j += 1
            i = j + 1
            while i < n and src[i].isalpha():
                i += 1
            prev = "a"
            continue
        if c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if not stack:
                raise JSCheckError(f"line {line}: unexpected {c!r}")
            top, opened = stack.pop()
            if c == "}" and top == "${":
                pass
            elif _PAIRS[c] != top:
                raise JSCheckError(f"line {line}: {c!r} closes {top!r} opened on line {opened}")
        m = re.match(r"[A-Za-z_$][\w$]*", src[i:])
        if m:
            prev_word = m.group(0)
            prev = "a"
            i += len(prev_word)
            continue
        prev_word = ""
        prev = c
        i += 1
    if stack:
        top, opened = stack[-1]
        raise JSCheckError(f"{top!r} opened on line {opened} is never closed")


def element_ids(src: str) -> set[str]:
    return set(re.findall(r"""\$\(\s*["']([\w-]+)["']\s*\)""", src))
