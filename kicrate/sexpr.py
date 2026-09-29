"""Minimal S-expression reader/writer for KiCad files.

Nodes are Python lists; atoms are `Sym` (bare symbols), `str` (quoted
strings), `int` or `float`.
"""

from __future__ import annotations

import re


class Sym(str):
    """A bare (unquoted) symbol such as `layer` or `yes`."""

    def __repr__(self) -> str:
        return f"Sym({str.__repr__(self)})"


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def loads(text: str) -> list:
    stack: list[list] = [[]]
    pos = 0
    while True:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError(f"unexpected input at offset {pos}")
            break
        pos = m.end()
        lparen, rparen, quoted, atom = m.groups()
        if lparen:
            stack.append([])
        elif rparen:
            node = stack.pop()
            stack[-1].append(node)
        elif quoted is not None:
            stack[-1].append(_unescape(quoted))
        else:
            stack[-1].append(_atom(atom))
    if len(stack) != 1 or len(stack[0]) != 1:
        raise ValueError("unbalanced s-expression")
    return stack[0][0]


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def _atom(tok: str):
    if _NUM.match(tok):
        return float(tok) if any(c in tok for c in ".eE") else int(tok)
    return Sym(tok)


def fmt_num(v: float) -> str:
    if isinstance(v, bool):
        raise TypeError("bool is not a KiCad number")
    if isinstance(v, int):
        return str(v)
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _fmt_atom(a) -> str:
    if isinstance(a, Sym):
        return str(a)
    if isinstance(a, str):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return fmt_num(a)


def dumps(node, indent: int = 0) -> str:
    """Serialize in KiCad's style: nested lists on their own lines, small nodes inline."""
    tab = "\t" * indent
    if not isinstance(node, list):
        return tab + _fmt_atom(node)
    kids = [c for c in node if isinstance(c, list)]
    # Keep small nodes like (at 1 2), (font (size 1 1)) on one line.
    if len(kids) <= 2 and all(not any(isinstance(g, list) for g in c) for c in kids):
        return tab + "(" + " ".join(dumps(c) for c in node) + ")"
    first = next(i for i, c in enumerate(node) if isinstance(c, list))
    out = [tab + "(" + " ".join(_fmt_atom(a) for a in node[:first])]
    out += [dumps(c, indent + 1) for c in node[first:]]
    out.append(f"{tab})")
    return "\n".join(out)


def find(node: list, name: str) -> list | None:
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def find_all(node: list, name: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == name]
