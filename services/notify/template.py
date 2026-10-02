"""Invitation templates: the admin's rich text, made safe for email, with placeholders.

The builder's editor produces HTML. Before it goes into anyone's inbox it is
reduced to a short allow-list of formatting tags (no scripts, styles, images or
attributes other than a link's http/https/mailto address), and placeholders
are filled with escaped values. The participant's own link and access code are
always appended by the server, so a template can't lose them.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

ALLOWED = {"p", "br", "b", "strong", "i", "em", "u", "a", "ul", "ol", "li", "blockquote", "h2", "h3"}
VOID = {"br"}
PLACEHOLDERS = ("PARTICIPANT_NAME", "ROLE_PLAY", "LANGUAGE", "DURATION", "COMPANY_NAME")


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0  # inside <script>/<style>: drop the content too

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag == "div":
            tag = "p"
        if tag not in ALLOWED:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not re.match(r"^(https?://|mailto:)", href.strip(), re.I):
                return
            self.out.append(f'<a href="{html.escape(href.strip(), quote=True)}">')
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if tag == "div":
            tag = "p"
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def sanitize(raw: str) -> str:
    p = _Clean()
    p.feed(raw or "")
    p.close()
    return "".join(p.out) + "".join(f"</{t}>" for t in reversed(p.stack))


def fill(template_html: str, values: dict[str, str]) -> str:
    out = sanitize(template_html)
    for k in PLACEHOLDERS:
        out = out.replace("{" + k + "}", html.escape(values.get(k, ""), quote=False))
    return out


def to_text(html_: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", html_)
    t = re.sub(r"</(p|h2|h3|blockquote|li)>", "\n", t)
    t = re.sub(r"<li>", "• ", t)
    t = re.sub(r"<a href=\"([^\"]+)\">(.*?)</a>", r"\2 (\1)", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(t)).strip()


def default_template(purpose: str) -> str:
    kind = {"Hiring": "an AI conversation", "HR": "a conversation", "L&D": "a practice role-play"}.get(purpose, "a role-play")
    return (
        "<p>Hi {PARTICIPANT_NAME},</p>"
        f"<p>You've been invited to complete <b>{kind}: {{ROLE_PLAY}}</b>. It's a spoken conversation in your web browser, "
        "on a computer or phone, and takes about {DURATION} minutes.</p>"
        "<p>The conversation will be in {LANGUAGE}. Please make sure you're comfortable speaking in this language, "
        "and find a quiet place with a working microphone.</p>"
        "<p>Your personal link and access code are below.</p>"
        "<p>Wishing you the best of luck!<br>{COMPANY_NAME}</p>"
    )
