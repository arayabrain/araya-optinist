"""The SPA shell catch-all and the health endpoint.

Both are served by ``__main_unit__`` on every tier, including public, where the
ALB health check and the browser's deep links are the only two things reaching
the instance before any API call happens.

The ALB routing that puts these requests on the public target group needs a
deployed environment; what is asserted here is that the application answers them
correctly once they arrive.

Two mechanisms can serve a deep link, and the assertions below deliberately do
not care which: ``SPARoutingMiddleware`` intercepts anything with
``Accept: text/html`` before routing, and the ``/{_:path}`` catch-all handles
whatever reaches the router. The contract is the response, not the mechanism.

Every mechanism funnels through ``serve_spa_shell``, which has two branches:
CI checks out and runs pytest with no frontend build, so it serves
``no-built-pages.html``, while a runner with a build serves the real
``index.html``. Asserting only "200 and text/html" therefore proves nothing
about the shell in CI - a "frontend not built" placeholder satisfies it.
``TestSpaCatchAllServesTheShell`` pins both branches explicitly instead, so the
built-index path is covered wherever it runs.
"""

import json
import os
import re
from unittest.mock import patch
from urllib.parse import urlparse
from xml.etree import ElementTree

import pytest

from studio.__main_unit__ import app
from studio.app.common.core.middleware import spa_routing_middleware
from studio.app.common.core.middleware.spa_routing_middleware import (
    CSP_HEADER,
    CSP_REPORT_PATH,
    INDEX_HTML_CACHE_HEADERS,
    ROUTE_HEAD_METADATA,
    rewrite_route_metadata,
)
from studio.app.dir_path import DIRPATH

BUILT_INDEX_PATH = f"{DIRPATH.FRONTEND_DIRS.BUILD}/index.html"

HEAD_TAG_RES = {
    "canonical": re.compile(r'<link\s+rel="canonical"\s+href="([^"]*)"'),
    "og_url": re.compile(r'<meta\s+property="og:url"\s+content="([^"]*)"'),
    "title": re.compile(r"<title>([^<]*)</title>"),
    "og_title": re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"'),
    "twitter_title": re.compile(r'<meta\s+name="twitter:title"\s+content="([^"]*)"'),
    "description": re.compile(r'<meta\s+name="description"\s+content="([^"]*)"'),
    "og_description": re.compile(
        r'<meta\s+property="og:description"\s+content="([^"]*)"'
    ),
    "twitter_description": re.compile(
        r'<meta\s+name="twitter:description"\s+content="([^"]*)"'
    ),
}
CANONICAL_TAG_RE = HEAD_TAG_RES["canonical"]


def read_public_index() -> str:
    with open(f"{DIRPATH.FRONTEND_DIRS.PUBLIC}/index.html", encoding="utf-8") as f:
        return f.read()


def patched_build_index(tmp_path, html: str):
    (tmp_path / "index.html").write_text(html, encoding="utf-8")
    return patch.object(DIRPATH.FRONTEND_DIRS, "BUILD", str(tmp_path))


# Paths React Router owns that match no API router, so they reach the
# ``/{_:path}`` catch-all. Deliberately not ``/workspaces`` or ``/dataview``,
# which are real router prefixes; those are covered separately by the
# colliding-deep-link case, which needs the middleware rather than the catch-all.
CLIENT_SIDE_ONLY_PATHS = [
    "/dashboard",
    "/account",
    "/login",
    "/subscription/thanks",
    "/subscription/failed",
]


class TestSpaCatchAllServesTheShell:
    """A client-side route returns the SPA shell, not a 404.

    These paths exist only in React Router. A cold browser load or a refresh on
    any of them arrives as a plain GET matching no router, so ``any_pages`` must
    hand back ``index.html`` and let the client route. If it 404s, every deep
    link and every refresh is a broken page.
    """

    def test_client_side_routes_are_not_api_routes(self):
        """Guards the premise of every case below. If one of these paths ever
        becomes a real router prefix, the catch-all is no longer what answers
        it and the assertions stop meaning anything."""
        api_paths = {route.path for route in app.routes if hasattr(route, "path")}

        for path in CLIENT_SIDE_ONLY_PATHS:
            assert path not in api_paths, (
                f"{path} is now a mounted API route; pick a different "
                f"client-side-only path for the catch-all assertions"
            )

    @pytest.mark.parametrize("path", CLIENT_SIDE_ONLY_PATHS)
    def test_a_client_side_route_returns_the_shell(self, client, path):
        response = client.get(path)

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_an_unknown_api_path_404s_for_a_json_client(self, client):
        """The other half of the same handler: an XHR asking for JSON must get a
        404 rather than a page of HTML, or the frontend parses the shell as a
        failed response body instead of surfacing the error.
        """
        response = client.get(
            "/api/does-not-exist", headers={"accept": "application/json"}
        )

        assert response.status_code == 404

    def test_the_same_path_branches_on_the_accept_header(self, client):
        """The branch is ``"application/json" in accept``, so one path must go
        both ways depending only on that header."""
        as_browser = client.get("/dashboard", headers={"accept": "text/html"})
        as_xhr = client.get("/dashboard", headers={"accept": "application/json"})

        assert as_browser.status_code == 200
        assert as_xhr.status_code == 404

    @pytest.mark.parametrize("path", CLIENT_SIDE_ONLY_PATHS[:2])
    def test_the_built_index_is_served_when_a_build_exists(
        self, client, path, tmp_path
    ):
        """The branch CI never reaches on its own.

        Without this, every assertion in this class is satisfied by
        ``no-built-pages.html`` - a placeholder that says the frontend is not
        built, which is the opposite of the shell being served.
        """
        marker = '<!--built-index-marker--><div id="root"></div>'

        with patched_build_index(tmp_path, marker):
            response = client.get(path)

        assert response.status_code == 200
        assert "built-index-marker" in response.text
        for header, value in INDEX_HTML_CACHE_HEADERS.items():
            assert response.headers.get(header) == value

    def test_a_deep_link_that_collides_with_a_real_api_route_gets_html(self, client):
        """``/workspaces`` is both a React route and a real authenticated
        ``GET``, which is the one case the catch-all cannot handle.

        Browser navigation carries no Authorization header, so the request must be
        intercepted by ``SPARoutingMiddleware`` on ``Accept: text/html`` before
        routing. Reaching the API instead is what produced "Could not validate
        credentials" on a refresh. Deliberately the bare path: ``/workspaces/123``
        matches no route, so it falls through to the catch-all and would pass with
        the middleware disabled.
        """
        response = client.get("/workspaces", headers={"accept": "text/html"})

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_the_shell_is_not_cached(self, client):
        """``index.html`` names hashed asset bundles. A cached shell after a
        deploy points at bundles that no longer exist, which is the chunk-load
        failure the client-side chunk-load reload exists to absorb.

        Asserted against the constant the app sends rather than a duplicated
        header string, so the two cannot drift apart.
        """
        response = client.get("/dashboard")

        for header, value in INDEX_HTML_CACHE_HEADERS.items():
            assert response.headers.get(header) == value, (
                f"{header} was {response.headers.get(header)!r}, " f"expected {value!r}"
            )
        assert (
            "no-store" in INDEX_HTML_CACHE_HEADERS["Cache-Control"]
        ), "the constant itself stopped forbidding storage"


class TestHealthEndpoint:
    """``/health`` answers 200.

    The public ASG uses ``health_check_type = "ELB"``, so this response is what
    keeps instances in the target group. A non-200 rolls the whole public tier.
    """

    def test_health_returns_200_and_healthy(self, client):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_health_needs_no_authentication(self):
        """The ALB health check sends no Authorization header.

        Asserted against the route's declaration, not a request.
        ``dependency_overrides`` is state on the shared ``app``, and the session
        fixture stubs the auth dependencies out for the whole run - so a request
        would 200 even if someone added ``Depends(get_current_user)`` here, and
        the ASG would then drop every instance in production.
        """
        health = next(
            route for route in app.routes if getattr(route, "path", None) == "/health"
        )

        # ``route.dependencies`` only holds the decorator's ``dependencies=[...]``.
        # A ``Depends`` in the handler signature lands in ``dependant`` instead,
        # which is the shape that actually gets written by accident.
        resolved = [
            getattr(dep.call, "__name__", dep.call)
            for dep in health.dependant.dependencies
        ]
        assert not health.dependencies, (
            f"/health gained dependencies {health.dependencies}; the ALB health "
            f"check cannot satisfy any of them"
        )
        assert not resolved, (
            f"/health gained signature-level dependencies {resolved}; the ALB "
            f"health check cannot satisfy any of them"
        )
        assert not health.dependant.security_requirements


# The single-line shapes html-minifier emits in frontend/build/index.html
# (keepClosingSlash, collapsed whitespace); the other cases read the
# Prettier-formatted frontend/public/index.html, and the rewrite must work on both.
MINIFIED_SHELL = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"/>'
    "<title>Araya OptiNiSt - No-Code Scientific Data Analysis Platform</title>"
    '<meta name="description" content="Araya OptiNiSt is a no-code cloud platform."/>'
    '<meta property="og:title" content="Araya OptiNiSt - No-Code Platform"/>'
    '<meta property="og:description" content="Build analysis pipelines visually."/>'
    '<meta property="og:url" content="https://www.araya-optinist.com/"/>'
    '<link rel="canonical" href="https://www.araya-optinist.com/"/>'
    '<meta name="twitter:title" content="Araya OptiNiSt - No-Code Platform"/>'
    '<meta name="twitter:description" content="Build analysis pipelines visually."/>'
    '<script type="application/ld+json">'
    '{"url": "https://www.araya-optinist.com/"}</script>'
    '</head><body><div id="root"></div></body></html>'
)


class TestPerRouteHeadMetadata:
    """The served shell carries per-route head metadata.

    The static document hardcodes canonical, URL, title and description tags to
    the site root's values, so every sitemap URL self-canonicalized to "/" and
    Search Console reported /login, /register and /public as duplicates (issue
    #809). The rewrite happens server-side because crawlers must see it in the
    raw HTML: the React router only mounts after a backend round-trip, and OG
    scrapers never execute JavaScript.
    """

    def head_values(self, html: str) -> dict:
        values = {}
        for name, pattern in HEAD_TAG_RES.items():
            match = pattern.search(html)
            assert match, f"{name} tag not found in the document"
            values[name] = match.group(1)
        return values

    def test_route_metadata_guards_no_api_route(self):
        """Same premise guard as CLIENT_SIDE_ONLY_PATHS: a whitelisted path
        that becomes a real router prefix is no longer served by the shell."""
        api_paths = {route.path for route in app.routes if hasattr(route, "path")}

        assert not set(ROUTE_HEAD_METADATA) & api_paths

    @pytest.mark.parametrize("route", sorted(ROUTE_HEAD_METADATA))
    def test_rewrites_the_real_source_document(self, route):
        """Against the actual frontend/public/index.html, so a reformat that
        breaks the tag patterns fails here instead of silently serving the
        root canonical everywhere."""
        rewritten = rewrite_route_metadata(read_public_index(), route)

        values = self.head_values(rewritten)
        expected_title = ROUTE_HEAD_METADATA[route]["title"]
        expected_description = ROUTE_HEAD_METADATA[route]["description"]
        assert values["canonical"].endswith(route)
        assert values["og_url"] == values["canonical"]
        for key in ("title", "og_title", "twitter_title"):
            assert values[key] == expected_title
        for key in ("description", "og_description", "twitter_description"):
            assert values[key] == expected_description

    def test_rewrites_the_minified_build_shape(self):
        rewritten = rewrite_route_metadata(MINIFIED_SHELL, "/register")

        values = self.head_values(rewritten)
        assert values["canonical"] == "https://www.araya-optinist.com/register"
        assert values["og_url"] == "https://www.araya-optinist.com/register"
        assert values["title"] == ROUTE_HEAD_METADATA["/register"]["title"]
        assert values["og_title"] == ROUTE_HEAD_METADATA["/register"]["title"]

    def test_a_missing_tag_leaves_the_rest_and_warns_once(self):
        """The likeliest production failure is a build change that breaks one
        tag shape; the others must still be rewritten and the warning must be
        logged once per process, not per request."""
        spa_routing_middleware._missing_tag_warnings.clear()
        headless = "<html><head><title>x</title></head><body></body></html>"

        try:
            with patch.object(spa_routing_middleware, "logger") as mock_logger:
                first = rewrite_route_metadata(headless, "/login")
                second = rewrite_route_metadata(headless, "/login")
        finally:
            spa_routing_middleware._missing_tag_warnings.clear()

        expected_title = ROUTE_HEAD_METADATA["/login"]["title"]
        assert first == second == headless.replace("x", expected_title)
        # 8 patterns, only <title> is present: 7 warnings, not 14.
        assert mock_logger.warning.call_count == 7
        warned = " ".join(call.args[0] for call in mock_logger.warning.call_args_list)
        assert "canonical" in warned
        assert "og_url" in warned

    def test_the_origin_comes_from_the_document_not_the_request(self):
        """Apex and www both resolve to the ALB with no host redirect, so the
        canonical host must stay whatever the document declares."""
        rewritten = rewrite_route_metadata(MINIFIED_SHELL, "/login")

        values = self.head_values(rewritten)
        assert values["canonical"] == "https://www.araya-optinist.com/login"

    def test_json_ld_urls_are_untouched(self):
        html = read_public_index()
        root_urls_before = html.count('"https://www.araya-optinist.com/"')

        rewritten = rewrite_route_metadata(html, "/login")

        # canonical and og:url each lose one root URL; the JSON-LD blocks keep
        # theirs verbatim.
        assert (
            rewritten.count('"https://www.araya-optinist.com/"') == root_urls_before - 2
        )

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/dashboard",
            "/workspaces/12",
            "/loginx",
            '/login"><script>alert(1)</script>',
        ],
    )
    def test_unlisted_paths_are_served_byte_identical(self, path):
        """Unknown and hostile paths change nothing: only frozen whitelist
        values are ever written into the document."""
        html = read_public_index()

        assert rewrite_route_metadata(html, path) == html

    @pytest.mark.parametrize("path", ["/login/", "/LOGIN", "//login"])
    def test_path_variants_normalize_to_the_canonical_form(self, path):
        """Trailing-slash and case variants would otherwise mint new
        self-canonicalizing duplicates of the sitemap URL."""
        rewritten = rewrite_route_metadata(read_public_index(), path)

        assert self.head_values(rewritten)["canonical"].endswith("/login")

    def test_whitelist_matches_the_sitemap(self):
        """ROUTE_HEAD_METADATA exists to serve exactly the sitemap's non-root
        URLs; this keeps the two from drifting (and forces a decision when a
        route is added to either side)."""
        tree = ElementTree.parse(f"{DIRPATH.FRONTEND_DIRS.PUBLIC}/sitemap.xml")
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text for el in tree.findall(".//sm:loc", namespace)]

        assert {urlparse(loc).path for loc in locs} - {"/"} == set(ROUTE_HEAD_METADATA)

        canonical_host = re.search(
            r'rel="canonical"\s+href="https?://([^"/]+)', read_public_index()
        ).group(1)
        assert {urlparse(loc).netloc for loc in locs} == {canonical_host}

    def test_frontend_copy_matches_the_server_values(self):
        """Client-side navigation never refetches the shell, so the SPA applies
        the same values from its own copy; this keeps the two in step."""
        path = f"{DIRPATH.FRONTEND_DIR}/src/const/routeHeadMetadata.json"
        with open(path, encoding="utf-8") as f:
            frontend = json.load(f)
        defaults = self.head_values(read_public_index())

        assert frontend["routes"] == ROUTE_HEAD_METADATA
        assert frontend["default"] == {
            "title": defaults["title"],
            "description": defaults["description"],
            "socialDescription": defaults["og_description"],
        }
        assert defaults["twitter_description"] == defaults["og_description"]

    @pytest.mark.parametrize("accept", ["text/html,application/xhtml+xml", "*/*"])
    def test_both_serving_mechanisms_emit_the_route_canonical(
        self, client, tmp_path, accept
    ):
        """text/html goes through SPARoutingMiddleware, anything else (curl,
        facebookexternalhit) through the catch-all; both must rewrite."""
        with patched_build_index(tmp_path, read_public_index()):
            response = client.get("/login", headers={"accept": accept})

        assert response.status_code == 200
        assert (
            CANONICAL_TAG_RE.search(response.text).group(1)
            == "https://www.araya-optinist.com/login"
        )
        for header, value in INDEX_HTML_CACHE_HEADERS.items():
            assert response.headers.get(header) == value

    def test_a_route_off_the_whitelist_keeps_the_root_canonical(self, client, tmp_path):
        with patched_build_index(tmp_path, read_public_index()):
            response = client.get("/dashboard", headers={"accept": "text/html"})

        assert (
            CANONICAL_TAG_RE.search(response.text).group(1)
            == "https://www.araya-optinist.com/"
        )

    @pytest.mark.skipif(
        not os.path.exists(BUILT_INDEX_PATH),
        reason="no frontend build on this runner",
    )
    def test_rewrites_the_actual_build_output(self):
        """Runs only where a real build exists (a developer machine); CI covers
        the same contract through MINIFIED_SHELL."""
        with open(BUILT_INDEX_PATH, encoding="utf-8") as f:
            built = f.read()

        rewritten = rewrite_route_metadata(built, "/public")

        values = self.head_values(rewritten)
        assert values["canonical"].endswith("/public")
        assert values["og_url"].endswith("/public")
        assert values["title"] == ROUTE_HEAD_METADATA["/public"]["title"]


class TestContentSecurityPolicy:
    """Report-only CSP on the built shell, with a per-response script nonce."""

    def shell(self, client, tmp_path, path="/dashboard"):
        with patched_build_index(tmp_path, read_public_index()):
            return client.get(path, headers={"accept": "text/html"})

    def test_the_shell_carries_a_report_only_policy(self, client, tmp_path):
        policy = self.shell(client, tmp_path).headers[CSP_HEADER]

        assert CSP_HEADER == "Content-Security-Policy-Report-Only"
        assert f"report-uri {CSP_REPORT_PATH}" in policy
        assert "object-src 'none'" in policy
        script_src = re.search(r"script-src ([^;]*)", policy).group(1)
        assert "'unsafe-inline'" not in script_src

    def test_every_inline_script_carries_the_header_nonce(self, client, tmp_path):
        response = self.shell(client, tmp_path)
        nonce = re.search(r"'nonce-([^']+)'", response.headers[CSP_HEADER]).group(1)

        inline_scripts = read_public_index().count("<script>")
        assert inline_scripts, "the source document has inline scripts to nonce"
        assert response.text.count(f'<script nonce="{nonce}">') == inline_scripts
        assert "<script>" not in response.text

    def test_json_ld_blocks_are_left_alone(self, client, tmp_path):
        text = self.shell(client, tmp_path).text
        assert text.count('<script type="application/ld+json">') == (
            read_public_index().count('<script type="application/ld+json">')
        )

    def test_the_nonce_differs_per_response(self, client, tmp_path):
        first = self.shell(client, tmp_path).headers[CSP_HEADER]
        second = self.shell(client, tmp_path).headers[CSP_HEADER]
        assert first != second

    def test_the_report_path_is_registered_without_auth(self):
        """Browsers post reports without credentials."""
        (route,) = [r for r in app.routes if getattr(r, "path", "") == CSP_REPORT_PATH]
        assert "POST" in route.methods
        assert route.dependant.dependencies == []


class TestRootSeoFiles:
    """/robots.txt, /sitemap.xml and /llms.txt are real files, not the shell.

    Before #809 no route served them: the SPA catch-all answered with HTML, so
    Search Console could not read the sitemap at all. They are served from the
    build directory, which is all the production image carries.
    """

    def serve(self, client, tmp_path, filename, content="x", write=True):
        if write:
            (tmp_path / filename).write_text(content, encoding="utf-8")
        with patch.object(DIRPATH.FRONTEND_DIRS, "BUILD", str(tmp_path)):
            # text/html is the hostile case: the middleware must not swallow
            # the file request and answer with the shell.
            return client.get(f"/{filename}", headers={"accept": "text/html"})

    def test_sitemap_is_served_as_xml(self, client, tmp_path):
        response = self.serve(client, tmp_path, "sitemap.xml", content="<urlset/>")

        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert response.text == "<urlset/>"

    def test_robots_is_served_as_text(self, client, tmp_path):
        response = self.serve(client, tmp_path, "robots.txt", content="User-agent: *")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.text == "User-agent: *"

    def test_llms_is_served_as_text(self, client, tmp_path):
        response = self.serve(client, tmp_path, "llms.txt")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"

    def test_a_missing_file_is_a_404_not_the_shell(self, client, tmp_path):
        response = self.serve(client, tmp_path, "sitemap.xml", write=False)

        assert response.status_code == 404

    def test_path_variants_still_get_the_shell(self, client, tmp_path):
        """Deliberate: only the exact lowercase paths are files; crawlers
        request them exactly, and anything else stays a client-side route."""
        (tmp_path / "robots.txt").write_text("User-agent: *", encoding="utf-8")
        with patched_build_index(tmp_path, read_public_index()):
            response = client.get("/robots.txt/", headers={"accept": "text/html"})

        assert '<div id="root">' in response.text
        assert "User-agent" not in response.text

    def test_the_source_sitemap_ships_with_the_frontend(self):
        """The routes serve build/, which CRA populates by copying public/;
        this pins that the files to copy actually exist."""
        for filename in ("robots.txt", "sitemap.xml", "llms.txt"):
            assert os.path.exists(f"{DIRPATH.FRONTEND_DIRS.PUBLIC}/{filename}")
