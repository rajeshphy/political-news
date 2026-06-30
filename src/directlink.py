from __future__ import annotations

import html
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from functools import lru_cache


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
}

BAD_FINAL_DOMAINS = (
    "googleusercontent.com",
    "gstatic.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)

BAD_FINAL_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
)

REDIRECT_TIMEOUT_SECONDS = 4


class DirectLinkFound(Exception):
    def __init__(self, url: str):
        self.url = url


class DirectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)

        if is_direct_article_redirect(req.full_url, newurl):
            raise DirectLinkFound(newurl)

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def clean_url(url: str) -> str:
    url = html.unescape((url or "").strip())

    if not url:
        return ""

    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key.lower() not in TRACKING_PARAMS]

    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urllib.parse.urlencode(query),
            "",
        )
    )


def is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def is_bad_final_url(url: str) -> bool:
    lowered = url.lower()
    parsed = urllib.parse.urlparse(lowered)

    if any(domain in parsed.netloc for domain in BAD_FINAL_DOMAINS):
        return True

    if any(parsed.path.endswith(ext) for ext in BAD_FINAL_EXTENSIONS):
        return True

    return False


def is_direct_article_redirect(source_url: str, target_url: str) -> bool:
    source_host = urllib.parse.urlparse(source_url).netloc.lower()
    target_host = urllib.parse.urlparse(target_url).netloc.lower()

    if not target_host or target_host == source_host:
        return False

    google_hosts = ("news.google.", "google.com", "www.google.")

    if "news.google." in source_host and not any(host in target_host for host in google_hosts):
        return True

    return False


def browser_final_url(url: str) -> str:
    """
    Open an RSS/indirect link and return the actual URL reached after
    browser-like HTTP GET redirects.

    This does not guess from HTML and does not extract random links from the
    page body. It trusts only urllib's final response URL.
    """
    url = clean_url(url)

    if not is_http_url(url):
        return ""

    try:
        opener = urllib.request.build_opener(DirectRedirectHandler)
        request = urllib.request.Request(url, headers=HEADERS, method="GET")

        with opener.open(request, timeout=REDIRECT_TIMEOUT_SECONDS) as response:
            final_url = response.geturl() or url

    except DirectLinkFound as found:
        final_url = found.url

    except (HTTPError, URLError, TimeoutError, OSError):
        return url

    try:
        final_url = clean_url(final_url)

        if not is_http_url(final_url):
            return ""

        if is_bad_final_url(final_url):
            return ""

        return final_url

    except Exception:
        return url


@lru_cache(maxsize=512)
def resolve_direct_link(url: str) -> str:
    """
    Resolve feed/news links by opening them and returning the landed URL.
    If the destination is slow, keep the original URL instead of blocking
    the whole digest.
    """
    return browser_final_url(url)
