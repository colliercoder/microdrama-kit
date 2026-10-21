"""A small YAML subset reader and writer, so the kit needs no third-party packages.

Supported: nested mappings by indentation, lists of scalars, lists of mappings
("- key: value"), inline lists ([a, b]), quoted strings, folded (>) and literal (|)
blocks, comments, true/false/null, ints and floats. Not supported: anchors, tags,
multi-document files, inline mappings other than the empty {}. The show bible only needs this subset.
"""
from __future__ import annotations

import json
import re
from typing import Any


class YamlError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_inline_list(body: str) -> list[str]:
    items, cur, quote = [], [], None
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        items.append("".join(cur).strip())
    return items


def parse_scalar(text: str) -> Any:
    s = text.strip()
    if s == "":
        return None
    if s == "{}":
        return {}
    if s.startswith("[") and s.endswith("]"):
        return [parse_scalar(x) for x in _split_inline_list(s[1:-1])]
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return json.loads(s)
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d*", s):
        return float(s)
    return s


_KEY_RE = re.compile(r"""^("(?:[^"\\]|\\.)*"|'[^']*'|[^:#'"\s][^:]*?)\s*:(?:\s+(.*))?$""")


def _key(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        return parse_scalar(raw)
    return raw


class _Lines:
    def __init__(self, text: str):
        self.raw = text.splitlines()
        self.items: list[tuple[int, str, int]] = []  # (indent, content, raw index)
        for idx, line in enumerate(self.raw):
            if "\t" in line[: len(line) - len(line.lstrip())]:
                raise YamlError(f"line {idx + 1}: tabs are not allowed for indentation")
            content = _strip_comment(line)
            if content.strip():
                self.items.append((len(content) - len(content.lstrip()), content.strip(), idx))
        self.pos = 0

    def peek(self):
        return self.items[self.pos] if self.pos < len(self.items) else None


def _block_scalar(lines: _Lines, raw_idx: int, parent_indent: int, style: str) -> str:
    collected, block_indent = [], None
    i = raw_idx + 1
    while i < len(lines.raw):
        line = lines.raw[i]
        if line.strip() == "":
            collected.append("")
            i += 1
            continue
        ind = len(line) - len(line.lstrip())
        if ind <= parent_indent:
            break
        if block_indent is None:
            block_indent = ind
        collected.append(line[block_indent:])
        i += 1
    while lines.peek() and lines.peek()[2] < i:
        lines.pos += 1
    while collected and collected[-1] == "":
        collected.pop()
    if style.startswith("|"):
        return "\n".join(collected)
    paras, cur = [], []
    for ln in collected:
        if ln == "":
            paras.append(" ".join(cur))
            cur = []
        else:
            cur.append(ln.strip())
    paras.append(" ".join(cur))
    return "\n".join(p for p in paras if p)


def _parse_value_after_key(lines: _Lines, rest: str | None, indent: int, raw_idx: int) -> Any:
    if rest is not None and rest.strip() in (">", "|", ">-", "|-"):
        return _block_scalar(lines, raw_idx, indent, rest.strip())
    if rest is not None and rest.strip() != "":
        return parse_scalar(rest)
    nxt = lines.peek()
    if nxt is None:
        return None
    if nxt[0] > indent:
        return _parse_block(lines, nxt[0])
    if nxt[0] == indent and nxt[1].startswith("- "):
        return _parse_block(lines, indent)
    return None


def _parse_block(lines: _Lines, indent: int) -> Any:
    first = lines.peek()
    if first is None:
        return None
    if first[1] == "-" or first[1].startswith("- "):
        return _parse_list(lines, indent)
    return _parse_map(lines, indent)


def _parse_map(lines: _Lines, indent: int, first_inline: tuple[str, int] | None = None) -> dict:
    out: dict = {}
    if first_inline is not None:
        content, raw_idx = first_inline
        m = _KEY_RE.match(content)
        if not m:
            raise YamlError(f"line {raw_idx + 1}: expected 'key: value', got {content!r}")
        out[_key(m.group(1))] = _parse_value_after_key(lines, m.group(2), indent, raw_idx)
    while True:
        item = lines.peek()
        if item is None or item[0] < indent:
            return out
        ind, content, raw_idx = item
        if ind > indent:
            raise YamlError(f"line {raw_idx + 1}: unexpected indentation")
        if content.startswith("- "):
            return out
        m = _KEY_RE.match(content)
        if not m:
            raise YamlError(f"line {raw_idx + 1}: expected 'key: value', got {content!r}")
        lines.pos += 1
        out[_key(m.group(1))] = _parse_value_after_key(lines, m.group(2), indent, raw_idx)


def _parse_list(lines: _Lines, indent: int) -> list:
    out: list = []
    while True:
        item = lines.peek()
        if item is None or item[0] < indent:
            return out
        ind, content, raw_idx = item
        if ind != indent or not (content == "-" or content.startswith("- ")):
            return out
        lines.pos += 1
        rest = content[1:].strip()
        if rest == "":
            nxt = lines.peek()
            out.append(_parse_block(lines, nxt[0]) if nxt and nxt[0] > indent else None)
        elif _KEY_RE.match(rest) and not rest.startswith(("'", '"', "[")):
            out.append(_parse_map(lines, indent + 2, first_inline=(rest, raw_idx)))
        else:
            out.append(parse_scalar(rest))


def loads(text: str) -> Any:
    lines = _Lines(text)
    if lines.peek() is None:
        return {}
    result = _parse_block(lines, lines.peek()[0])
    if lines.peek() is not None:
        _, content, raw_idx = lines.peek()
        raise YamlError(f"line {raw_idx + 1}: could not parse {content!r}")
    return result


_PLAIN_OK = re.compile(r"^[A-Za-z0-9_./][A-Za-z0-9 _./()+,'-]*$")


def _dump_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if (_PLAIN_OK.match(s) and not s.endswith(" ") and s.lower() not in
            ("true", "false", "yes", "no", "null") and not re.fullmatch(r"-?\d+(\.\d*)?", s)):
        return s
    return json.dumps(s, ensure_ascii=False)


def dumps(obj: Any, indent: int = 0) -> str:
    pad = " " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = _dump_scalar(k)
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{key}:")
                lines.append(dumps(v, indent + 2))
            elif isinstance(v, (dict, list)):
                lines.append(f"{pad}{key}: []" if isinstance(v, list) else f"{pad}{key}: {{}}")
            else:
                lines.append(f"{pad}{key}: {_dump_scalar(v)}")
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict) and v:
                inner = dumps(v, indent + 2).splitlines()
                lines.append(f"{pad}- {inner[0].lstrip()}")
                lines.extend(inner[1:])
            elif isinstance(v, list):
                lines.append(f"{pad}- [{', '.join(_dump_scalar(x) for x in v)}]")
            else:
                lines.append(f"{pad}- {_dump_scalar(v)}")
    else:
        lines.append(pad + _dump_scalar(obj))
    return "\n".join(lines)
