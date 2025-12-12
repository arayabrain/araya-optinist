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
"""

import os

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from studio.app.dir_path import DIRPATH


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
        # Initialize templates for serving index.html
        self.build_templates = Jinja2Templates(directory=DIRPATH.FRONTEND_DIRS.BUILD)
        self.public_templates = Jinja2Templates(directory=DIRPATH.FRONTEND_DIRS.PUBLIC)

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

        # Check Accept header for text/html
        headers = dict(scope.get("headers", []))
        accept = headers.get(b"accept", b"").decode("utf-8", errors="ignore")

        # If request accepts text/html, it's a browser navigation
        return "text/html" in accept

    async def _serve_index_html(self, scope: Scope) -> Response:
        """
        Serve the index.html file for SPA routing

        Args:
            scope: ASGI scope dictionary

        Returns:
            Response with index.html content
        """
        # Create a Request object for template rendering
        request = Request(scope)

        # Check if build exists, otherwise serve no-build page
        if os.path.exists(f"{DIRPATH.FRONTEND_DIRS.BUILD}/index.html"):
            return self.build_templates.TemplateResponse(
                "index.html", {"request": request}
            )
        else:
            return self.public_templates.TemplateResponse(
                "no-built-pages.html", {"request": request}
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Check if we should serve the SPA
        if self._should_serve_spa(scope):
            # Serve index.html and let SPA router handle the route
            response = await self._serve_index_html(scope)
            await response(scope, receive, send)
            return

        # Otherwise, pass through to the next middleware/app
        await self.app(scope, receive, send)
