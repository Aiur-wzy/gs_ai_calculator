"""Lossless SpreadsheetML ST_Xstring encoding, not a source-data sanitizer."""
from __future__ import annotations

import re
from xml.sax.saxutils import escape

_ESCAPED = re.compile(r"_x[0-9a-fA-F]{4}_")


def spreadsheet_text(value, *, location="cell"):
    text = str(value)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise ValueError(f"XLSX文本含未配对Unicode代理项：{location}")
    # Escape original escape tokens first. Generated controls below must remain
    # decodable exactly once, including literal '_x005F_' in the input.
    text = _ESCAPED.sub(lambda match: "_x005F_" + match.group()[1:], text)
    text = "".join(f"_x{ord(char):04X}_" if (ord(char) < 32 and char not in "\t\n")
                   or ord(char) in (0xFFFE, 0xFFFF) else char for char in text)
    return escape(text)


def xml_formula(value, *, location="formula"):
    text = str(value)
    if any((ord(char) < 32 and char not in "\t\n\r") or
           0xD800 <= ord(char) <= 0xDFFF or ord(char) in (0xFFFE, 0xFFFF)
           for char in text):
        raise ValueError(f"XLSX公式不能无损表达该字符：{location}")
    return escape(text).replace("\r", "&#13;")
