"""将邮件 HTML 安全转换为看板可读的纯文本。"""
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional


_HTML_TAG_RE = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_BARE_TAG_AT_LINE_START = re.compile(
    r"(?m)^(?!<)((?:/?)(?:html|body|div|p|br|li|ul|ol|table|tr|td|h[1-6])(?:\s[^>]*)?>)"
)


class _EmailTextParser(HTMLParser):
    _BLOCK_TAGS = {"address", "article", "blockquote", "body", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "pre", "section", "table", "tr"}
    _SKIP_TAGS = {"script", "style", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "br" or tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        if self.skip_depth:
            return
        if tag.lower() == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if not self.skip_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self.skip_depth:
            self.parts.append(data)


def is_html_email_body(value: Any) -> bool:
    """判断正文是否包含 HTML，决定是否保留到 body_html。"""
    raw = str(value or "")
    return bool(_HTML_TAG_RE.search(raw) or _BARE_TAG_AT_LINE_START.search(raw))


def clean_email_body(value: Any) -> str:
    """去掉邮件 HTML 标签、解码实体，并保留段落换行。"""
    # Snov 偶尔会把 HTML 双重转义（&lt;p&gt;...），先解码再解析。
    raw = unescape(str(value or "").strip())
    if not raw:
        return ""

    # 个别 Snov 历史记录会漏掉开头标签的 '<'，例如 'div>...<p>'。
    raw = _BARE_TAG_AT_LINE_START.sub(r"<\1", raw)
    if not _HTML_TAG_RE.search(raw):
        return unescape(raw)

    parser = _EmailTextParser()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = _HTML_TAG_RE.sub("", raw)

    # 防御性清理：处理上游的嵌套/畸形 HTML 残片，始终只向看板提供纯文本。
    text = _HTML_TAG_RE.sub("", text)
    text = _BARE_TAG_AT_LINE_START.sub("", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in unescape(text).splitlines()]
    output: list[str] = []
    for line in lines:
        if line or (output and output[-1]):
            output.append(line)
    return "\n".join(output).strip()
