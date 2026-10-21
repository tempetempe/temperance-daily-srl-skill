"""Conservative full-visible-text verification for an imported learning Markdown.

This is not a general Markdown/MDX renderer. Unsupported or ambiguous input raises
UnsupportedDocument; valid but different complete text returns False. It verifies
document_full_text, never the byte integrity of the source Markdown. The JSON
original remains the lossless learning-fact artifact.
"""
from __future__ import annotations

import html
import json
import re
import string


class UnsupportedDocument(ValueError):
    """The full visible text cannot be established using this narrow grammar."""


BLOCKS = {"Heading", "Paragraph", "BulletedList", "NumberedList", "BlockQuote", "Divider"}
INLINE = {"Mark", "Link"}
GENERATED = re.compile(r"<!-- daily-srl-(?:learning-record: 1|record-status: (?:final|review_saved)) -->\Z")
ENTITY = re.compile(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")
BLOCK_COLORS = set("default grey light_grey dark light_blue blue light_sky_blue sky_blue light_green green light_yellow yellow light_orange orange light_red red light_rose_red rose_red light_purple purple".split())
TEXT_COLORS = set("default grey blue sky_blue green yellow orange red rose_red purple".split())


def _fail(message):
    raise UnsupportedDocument(message)


def _decode(data):
    if not isinstance(data, bytes):
        _fail("document input must be bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UnsupportedDocument("document input is not valid UTF-8") from error
    if any(ord(c) < 32 and c not in "\r\n" for c in text) or "\ufffd" in text:
        _fail("control or replacement characters are not supported")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _entities(text):
    def replace(match):
        value = html.unescape(match[0])
        if value == match[0] or "\ufffd" in value or any(ord(c) < 32 for c in value):
            _fail("unknown or invalid character entity")
        return value
    return ENTITY.sub(replace, text)


def _normal(text):
    # Only layout whitespace changes. Punctuation, digits, negation, and Unicode
    # characters are retained; separate visible blocks remain separate entries.
    return re.sub(r"[ \n]+", " ", text).strip(" \n")


def _inline_markdown(text, depth=0):
    if depth > 8 or any(c in text for c in "<>`$|{}") or "![" in text:
        _fail("unsupported Markdown inline content")
    result, i = [], 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            if i + 1 == len(text) or text[i + 1] not in string.punctuation:
                _fail("unsupported Markdown escape")
            result.append(text[i + 1]); i += 2
        elif char == "&" and ENTITY.match(text, i):
            entity = ENTITY.match(text, i)
            result.append(_entities(entity[0])); i = entity.end()
        elif char == "[":
            match = re.match(r"\[([^\[\]\n]+)\]\(([^()\s]+)\)", text[i:])
            if not match or not re.match(r"(?:https?://|mailto:)", match[2]):
                _fail("only simple explicit links are supported")
            result.append(_inline_markdown(match[1], depth + 1)); i += len(match[0])
        elif char in "*_~":
            delimiter = char * (2 if text.startswith(char * 2, i) else 1)
            # CommonMark underscores within a word are literal, including a
            # double run. Never erase punctuation from names such as Unit_1.
            if char == "_":
                run = re.match(r"_+", text[i:])[0]
                before = text[i - 1] if i else ""
                after = text[i + len(run):i + len(run) + 1]
                if before.isalnum() and after.isalnum():
                    result.append(run); i += len(run); continue
            if delimiter == "~":
                _fail("ambiguous Markdown tilde")
            end = text.find(delimiter, i + len(delimiter))
            if char == "_":
                while end >= 0 and text[end - 1:end].isalnum() and text[end + len(delimiter):end + len(delimiter) + 1].isalnum():
                    end = text.find(delimiter, end + len(delimiter))
            body = text[i + len(delimiter):end] if end >= 0 else ""
            if not body or body[0].isspace() or body[-1].isspace():
                _fail("unmatched or ambiguous Markdown emphasis")
            result.append(_inline_markdown(body, depth + 1)); i = end + len(delimiter)
        elif char == "]":
            _fail("unmatched Markdown link bracket")
        else:
            result.append(char); i += 1
    return "".join(result)


def _without_generated_comments(text):
    lines = []
    for line in text.splitlines():
        if GENERATED.fullmatch(line.strip()):
            continue
        if "<!--" in line or "-->" in line:
            _fail("unknown or embedded HTML comment")
        lines.append(line)
    return "\n".join(lines)


def _markdown(text, depth=0):
    if depth > 8:
        _fail("Markdown quote nesting is too deep")
    lines, values, paragraph = text.splitlines(), [], []
    def flush():
        if paragraph:
            values.append(_normal(_inline_markdown(" ".join(paragraph))))
            paragraph.clear()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush(); i += 1; continue
        if line.startswith("    ") or line.startswith(("```", "~~~")) or "|" in line:
            _fail("code, indented blocks and tables are not supported")
        if line.startswith(">"):
            flush(); quoted = []
            while i < len(lines) and lines[i].startswith(">"):
                quoted.append(lines[i][2:] if lines[i].startswith("> ") else lines[i][1:])
                i += 1
            values.extend(_markdown("\n".join(quoted), depth + 1)); continue
        heading = re.fullmatch(r"#{1,6} +(.+)", line)
        bullet = re.fullmatch(r"(?:[-+*]|[0-9]+[.)]) +(.+)", line)
        if heading or bullet:
            flush(); body = (heading or bullet)[1]
            if heading:
                body = re.sub(r" +#+ *$", "", body)
            if body.startswith(("[ ] ", "[x] ", "[X] ")):
                _fail("task lists are not supported")
            values.append(_normal(_inline_markdown(body)))
        elif re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", line):
            if paragraph:
                _fail("ambiguous setext heading or divider")
            # A divider has no visible text.
        elif line.startswith(("#", " ")) or re.fullmatch(r"=+", line):
            _fail("unsupported Markdown block")
        else:
            paragraph.append(line)
        i += 1
    flush()
    return values


def _transport(data):
    if not data.startswith(b"KS_DOC_REVIEWS"):
        return _decode(data)
    first, separator, body = data.partition(b"\n")
    fields = first.rstrip(b"\r").split(b"\t")
    if not separator or len(fields) != 4 or fields[0] != b"KS_DOC_REVIEWS" or not fields[1] or not fields[2].isdigit():
        _fail("invalid WorkBuddy full-document header")
    size = int(fields[2])
    # The official reader adds exactly one LF when the original lacks one.
    if len(body) == size:
        original = body
    elif len(body) == size + 1 and body.endswith(b"\n") and not body[:-1].endswith(b"\n"):
        original = body[:-1]
    else:
        _fail("WorkBuddy full-document byte count mismatch or truncation")
    return _decode(original)


def _frontmatter(text):
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        _fail("unterminated document frontmatter")
    # Official full-page readback permits only a title. It is metadata, never a
    # replacement for a missing body heading or a place to hide record content.
    fields = text[4:end].splitlines()
    if len(fields) != 1 or not re.fullmatch(r"title: ?[^\r\n]*", fields[0]):
        _fail("unsupported document frontmatter")
    return text[end + 5:]


def _attributes(tag, text):
    result, i = {}, 0
    while i < len(text):
        if text[i:].isspace():
            break
        match = re.match(r"\s+([A-Za-z][A-Za-z0-9]*)", text[i:])
        if not match:
            _fail("invalid component attribute")
        name = match[1]; i += len(match[0]); value = True
        if text[i:i + 1] == "=":
            i += 1
            if text[i:i + 1] == '"':
                end = text.find('"', i + 1)
                if end < 0:
                    _fail("unterminated component attribute")
                value = _entities(text[i + 1:end]); i = end + 1
            elif tag == "Mark" and name == "comment" and text[i:i + 1] == "{":
                end = text.find("}", i + 1)
                try:
                    value = json.loads(text[i + 1:end])
                except (ValueError, TypeError) as error:
                    raise UnsupportedDocument("unsupported comment-anchor expression") from error
                if end < 0 or not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                    _fail("unsupported comment-anchor expression")
                i = end + 1
            else:
                _fail("unknown component expression or unquoted attribute")
        if name in result or name in {"action", "reviewId", "ar"}:
            _fail("duplicate attribute or unapplied review revision")
        result[name] = value
    allowed = {"id"} if tag in BLOCKS else set()
    if tag in {"Heading", "Paragraph", "BlockQuote"}:
        allowed |= {"textAlign", "blockColor"}
    if tag in {"BulletedList", "NumberedList"}:
        allowed.add("blockColor")
    if tag == "Heading":
        allowed.add("level")
        if result.get("level") not in set("123456"):
            _fail("invalid heading level")
    if tag == "Mark":
        allowed |= {"bold", "italic", "underline", "strike", "color", "backgroundColor", "comment"}
    if tag == "Link":
        allowed.add("href")
        if not isinstance(result.get("href"), str) or not re.match(r"(?:https?://|mailto:)", result["href"]):
            _fail("unsupported link destination")
    if set(result) - allowed:
        _fail("unknown component attribute")
    for name, value in result.items():
        if name in {"bold", "italic", "underline", "strike"}:
            if value is not True:
                _fail("style flag must be a boolean attribute")
        elif name == "comment":
            continue
        elif not isinstance(value, str) or not value:
            _fail("component attribute requires a nonempty string")
        if name == "textAlign" and value not in {"left", "center", "right"}:
            _fail("unknown text alignment")
        if name in {"blockColor", "backgroundColor"} and value not in BLOCK_COLORS:
            _fail("unknown block color")
        if name == "color" and value not in TEXT_COLORS:
            _fail("unknown text color")
    return result


def _components(text):
    root = ["root", []]; stack = [root]; position = 0
    while position < len(text):
        if text[position] != "<":
            end = text.find("<", position)
            end = len(text) if end < 0 else end
            value = text[position:end]
            if "{" in value or "}" in value:
                _fail("component content expressions are not supported")
            stack[-1][1].append(_entities(value)); position = end; continue
        match = re.match(r"<(/?)([A-Za-z][A-Za-z0-9]*)([^<>]*?)(/?)>", text[position:])
        if not match:
            _fail("malformed component or literal tag")
        closing, tag, attributes, self_closing = match.groups(); position += len(match[0])
        if tag not in BLOCKS | INLINE:
            _fail("unsupported component, review summary or review card")
        if closing:
            if attributes or self_closing or len(stack) == 1 or stack[-1][0] != tag:
                _fail("mismatched component closing tag")
            stack.pop(); continue
        _attributes(tag, attributes)
        if bool(self_closing) != (tag == "Divider"):
            _fail("only Divider uses the self-closing form")
        if len(stack) > 32:
            _fail("component nesting is too deep")
        node = [tag, []]; stack[-1][1].append(node)
        if not self_closing:
            stack.append(node)
    if len(stack) != 1:
        _fail("truncated or unclosed component")
    def inline(node):
        result = []
        for child in node[1]:
            if isinstance(child, str):
                result.append(child)
            elif child[0] in INLINE:
                result.append(inline(child))
            else:
                _fail("block component nested inside an inline text flow")
        return "".join(result)
    def blocks(node):
        if node[0] in {"Paragraph", "Heading", "BulletedList", "NumberedList"}:
            value = _normal(inline(node))
            return [value] if value else []
        if node[0] == "Divider":
            return []
        values = []
        for child in node[1]:
            if isinstance(child, str):
                if child.strip():
                    _fail("text outside a block component")
            elif child[0] not in BLOCKS:
                _fail("inline component outside a text block")
            else:
                values.extend(blocks(child))
        return values
    return blocks(root)


def verify_document(source_bytes, remote_bytes):
    """Compare complete ordered visible text; raise for unsupported grammar.

    The caller must separately establish native document ID, parent space,
    successful complete readback and applied content. Never pass a search snippet
    or a locally reconstructed remote body. This function performs no I/O.
    """
    source = _without_generated_comments(_decode(source_bytes))
    expected = _markdown(source)
    remote = _without_generated_comments(_frontmatter(_transport(remote_bytes)))
    if not expected or not remote.strip():
        _fail("document body is missing")
    actual = _components(remote) if remote.lstrip().startswith("<") else _markdown(remote)
    if not actual:
        _fail("document body is missing")
    return expected == actual
