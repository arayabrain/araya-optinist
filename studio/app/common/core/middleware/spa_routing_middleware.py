"""
SPA Routing Middleware for handling browser navigation to frontend routes

This middleware intercepts browser navigation requests (Accept: text/html) to
frontend SPA routes and serves index.html instead of hitting backend API routes.

Problem this solves:
- When a user navigates directly to /workspaces in the browser, the request goes
  to the backend API route /workspaces instead of the frontend SPA
- This causes "Could not validate credentials" errors because browser navigation
  doesn't send authentication tokens
- The SPA router (React Router) should handle these routes client-side

Solution:
- Check if request has Accept: text/html (browser navigation)
- If yes, serve index.html and let the SPA router handle it
- If no (API call with Accept: application/json), let it pass through to API routes

The served shell also gets per-route head metadata: the built index.html
hardcodes canonical/og:url to the site root, so every route would otherwise
self-canonicalize to "/" and Search Console reports the sitemap URLs as
duplicates. For the indexable routes listed in frontend/public/sitemap.xml,
rewrite_route_metadata() rewrites the canonical, URL, title and description
head tags before the document leaves the server, which covers crawlers and
link scrapers that never execute JavaScript.
"""

import re
import secrets

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from studio.app.common.core.logger import AppLogger
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()

INDEX_HTML_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}

# Root files crawlers must receive as themselves, served by dedicated routes
# in __main_unit__ and excluded from the shell intercept here.
ROOT_SEO_FILES = {
    "/robots.txt": "text/plain",
    "/sitemap.xml": "application/xml",
    "/llms.txt": "text/plain",
}

# Keys are the canonical paths and must match the non-root <loc> entries of
# frontend/public/sitemap.xml (tested). Only these frozen values are ever
# written into the served HTML.
ROUTE_HEAD_METADATA = {
    "/login": {
        "title": "Log In - Araya OptiNiSt",
        "description": (
            "Log in to Araya OptiNiSt to build and run no-code analysis "
            "pipelines for calcium imaging and neuroscience data."
        ),
    },
    "/register": {
        "title": "Create an Account - Araya OptiNiSt",
        "description": (
            "Create a free Araya OptiNiSt account to analyze calcium imaging "
            "data with no-code visual pipelines and NWB-compatible workflows."
        ),
    },
    "/public": {
        "title": "Public Datasets - Araya OptiNiSt",
        "description": (
            "Browse publicly shared analyses and datasets on Araya OptiNiSt, "
            "the no-code platform for neuroscience data analysis."
        ),
    },
}

# Pattern plus the metadata field it takes its value from. Anchored to
# attribute names and whitespace-tolerant, so both the Prettier-formatted
# source and the html-minified build output match. The URL patterns keep the
# origin the document already carries and replace only the path, so index.html
# stays the single source of the canonical host.
_HEAD_TAG_PATTERNS = {
    "canonical": (
        re.compile(r'(?P<prefix><link\s+rel="canonical"\s+href="https?://[^"/]+)[^"]*'),
        "route",
    ),
    "og_url": (
        re.compile(
            r'(?P<prefix><meta\s+property="og:url"\s+content="https?://[^"/]+)[^"]*'
        ),
        "route",
    ),
    "title": (re.compile(r"(?P<prefix><title>)[^<]*"), "title"),
    "og_title": (
        re.compile(r'(?P<prefix><meta\s+property="og:title"\s+content=")[^"]*'),
        "title",
    ),
    "twitter_title": (
        re.compile(r'(?P<prefix><meta\s+name="twitter:title"\s+content=")[^"]*'),
        "title",
    ),
    "description": (
        re.compile(r'(?P<prefix><meta\s+name="description"\s+content=")[^"]*'),
        "description",
    ),
    "og_description": (
        re.compile(r'(?P<prefix><meta\s+property="og:description"\s+content=")[^"]*'),
        "description",
    ),
    "twitter_description": (
        re.compile(r'(?P<prefix><meta\s+name="twitter:description"\s+content=")[^"]*'),
        "description",
    ),
}

# A tag shape the patterns no longer match is logged once per process, not on
# every request.
_missing_tag_warnings = set()

_public_templates = Jinja2Templates(directory=DIRPATH.FRONTEND_DIRS.PUBLIC)

# Report-only until the violation reports from real traffic are clean.
CSP_HEADER = "Content-Security-Policy-Report-Only"
CSP_REPORT_PATH = "/log-report/csp"


def content_security_policy(nonce: str) -> str:
    # style-src needs 'unsafe-inline': MUI/emotion and react-flow inject <style>.
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' https://www.googletagmanager.com",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: blob: https://*.google-analytics.com"
            " https://*.googletagmanager.com",
            "connect-src 'self' https://*.google-analytics.com"
            " https://*.analytics.google.com https://*.googletagmanager.com",
            "worker-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            f"report-uri {CSP_REPORT_PATH}",
        )
    )


def rewrite_route_metadata(html: str, path: str) -> str:
    """Rewrite the head metadata tags for an indexable route.

    Unlisted paths return the document unchanged (root canonical, the
    pre-existing behavior). The emitted values are always the frozen dict key
    and its literals, never the request path.
    """
    route = "/" + path.strip("/").lower()
    metadata = ROUTE_HEAD_METADATA.get(route)
    if metadata is None:
        return html

    values = {
        "route": route,
        "title": metadata["title"],
        "description": metadata["description"],
    }
    for name, (pattern, field) in _HEAD_TAG_PATTERNS.items():
        value = values[field]
        html, count = pattern.subn(
            lambda match: match.group("prefix") + value, html, count=1
        )
        if count == 0 and name not in _missing_tag_warnings:
            _missing_tag_warnings.add(name)
            logger.warning(
                f"SPA shell is missing the {name} head tag; "
                f"routes are served without its per-route value"
            )
    return html


def serve_spa_shell(request: Request) -> Response:
    """Serve the SPA shell with per-route head metadata.

    Shared by the SPARoutingMiddleware intercept, the "/" route and the
    catch-all, so every path that can hand the shell to a crawler emits the
    same per-route canonical.
    """
    try:
        with open(f"{DIRPATH.FRONTEND_DIRS.BUILD}/index.html", encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        if not isinstance(e, FileNotFoundError):
            logger.warning(f"SPA shell build index unreadable: {e!r}")
        return _public_templates.TemplateResponse(
            "no-built-pages.html",
            {"request": request},
            headers=INDEX_HTML_CACHE_HEADERS,
        )
    nonce = secrets.token_urlsafe(16)
    html = rewrite_route_metadata(html, request.scope["path"]).replace(
        "<script>", f'<script nonce="{nonce}">'
    )
    return HTMLResponse(
        html,
        headers={
            **INDEX_HTML_CACHE_HEADERS,
            CSP_HEADER: content_security_policy(nonce),
        },
    )


class SPARoutingMiddleware:
    """
    Pure ASGI Middleware to handle SPA routing for browser navigation

    This middleware intercepts requests that:
    1. Have Accept: text/html (browser navigation, not API calls)
    2. Don't match exact API routes
    3. Should be handled by the frontend SPA router

    It serves index.html for these requests, allowing the SPA router to handle routing.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    def _should_serve_spa(self, scope: Scope) -> bool:
        """
        Determine if this request should be served by the SPA

        Returns True if:
        - Request accepts text/html (browser navigation)
        - Request path is not a static file or API endpoint

        Args:
            scope: ASGI scope dictionary

        Returns:
            bool: True if should serve SPA, False otherwise
        """
        # Only handle HTTP/HTTPS requests (scope["type"] == "http" for both)
        # Other types include "websocket", "lifespan", etc.
        if scope["type"] != "http":
            return False

        # Get request path
        path = scope.get("path", "")

        # Don't intercept static files, images, or specific API routes
        if path.startswith(("/static/", "/images/", "/docs", "/openapi", "/health")):
            return False

        # Root SEO files have dedicated routes serving the real file
        if path in ROOT_SEO_FILES:
            return False

        # Check Accept header for text/html
        headers = dict(scope.get("headers", []))
        accept = headers.get(b"accept", b"").decode("utf-8", errors="ignore")

        # If request accepts text/html, it's a browser navigation
        return "text/html" in accept

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Check if we should serve the SPA
        if self._should_serve_spa(scope):
            # Serve index.html and let SPA router handle the route
            response = serve_spa_shell(Request(scope))
            await response(scope, receive, send)
            return

        # Otherwise, pass through to the next middleware/app
        await self.app(scope, receive, send)
