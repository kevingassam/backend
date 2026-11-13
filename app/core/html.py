"""Sanitize rich text (HTML) entered in the admin before it is stored and rendered on the public site (XSS)."""

import nh3

TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "h2", "h3", "h4", "ul", "ol", "li", "a", "blockquote",
    "code", "pre", "hr", "table", "thead", "tbody", "tr", "th", "td", "img", "span", "div",
}
ATTRS = {
    "*": {"class"},
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}


def clean(html: str | None) -> str:
    if not html:
        return ""
    return nh3.clean(html, tags=TAGS, attributes=ATTRS, url_schemes={"http", "https", "mailto", "tel"}, link_rel="noopener noreferrer")
