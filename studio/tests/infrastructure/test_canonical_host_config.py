"""Serving the app from one host, read from the terraform source.

Sessions and client storage are per host. With a custom domain the app is
served from www only:
- Page loads on the apex are redirected to the same path on www, and nothing
  else on the apex is (API calls, webhook POSTs, scripts).
- Every service names www as the app's origin: FRONTEND_URL, and
  STRIPE_CALLBACK_URL where Stripe is used.
- CORS allows that origin only.

Without one (dev) the origin is the ALB DNS name on the HTTPS listener's port,
and CORS allows every origin.
Configuration assertions: they pin the declaration, not what AWS applied.
"""

import inspect
import re

import pytest
from premium_manager import create_alb_rule
from terraform_source import (
    PROJECT_ROOT,
    TERRAFORM_DIR,
    attribute,
    identifier_list,
    read,
    resource_block,
    task_environment,
)

MAIN_TF = TERRAFORM_DIR / "main.tf"
COMPUTE_DOMAIN_TF = TERRAFORM_DIR / "compute_domain.tf"
DEPLOYMENT_TF = TERRAFORM_DIR / "deployment.tf"
MAINTENANCE_MODE_SH = (
    PROJECT_ROOT / "infrastructure" / "scripts" / "maintenance_mode.sh"
)

# Task definition name -> file that declares it
TASK_DEFINITIONS = {
    "autoscaling": "compute.tf",
    "premium": "compute.tf",
    "public": "public_service.tf",
    "public_dataview": "public_dataview_service.tf",
    "background": "background_service.tf",
}
# The public tiers gate the subscriptions router out and get no Stripe vars
STRIPE_TASK_DEFINITIONS = ("autoscaling", "premium", "background")


def _leaf_block(source: str, name: str) -> str:
    """Return the body of one leaf block, e.g. ``redirect { ... }``. Ends at a
    line holding only ``}``, so ``#{path}`` inside a value does not end it."""
    match = re.search(
        r"^\s*%s\s*\{\n(.*?)^\s*\}\s*$" % re.escape(name),
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"no {name} block found"
    return match.group(1)


def _string(body: str, name: str) -> str:
    match = re.search(r'^\s*%s\s*=\s*"([^"]*)"' % re.escape(name), body, re.MULTILINE)
    assert match, f"string attribute {name} not found"
    return match.group(1)


def _list(body: str, name: str) -> list:
    """Return the raw items of a one-line list, quotes kept: ``["GET"]`` gives
    ``['"GET"']`` and ``[var.x]`` gives ``['var.x']``."""
    match = re.search(r"^\s*%s\s*=\s*\[(.*?)\]" % re.escape(name), body, re.MULTILINE)
    assert match, f"list attribute {name} not found"
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def _env_values(task_definition: str, name: str) -> list:
    """Every value the container environment gives ``name``, in either layout:
    ``{ name = "X", value = Y }`` or one attribute per line."""
    return re.findall(
        r'name\s*=\s*"%s"\s*,?\s*value\s*=\s*(.+?)\s*(?:\},?)?\s*$' % re.escape(name),
        task_definition,
        re.MULTILINE,
    )


def _task_environment(name: str) -> str:
    return task_environment(TASK_DEFINITIONS[name], name)


def _canonical_origin() -> tuple:
    """Return (custom domain origin, origin without one) of local.canonical_origin."""
    value = attribute(read(MAIN_TF), "canonical_origin")
    match = re.fullmatch(r'var\.enable_custom_domain \? "([^"]+)" : "([^"]+)"', value)
    assert match, f"canonical_origin is not a custom-domain switch: {value}"
    return match.group(1), match.group(2)


class TestApexRedirect:
    """``aws_lb_listener_rule.apex_to_www``."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.rule = resource_block(
            read(COMPUTE_DOMAIN_TF), "aws_lb_listener_rule", "apex_to_www"
        )

    def test_exists_only_with_a_custom_domain(self):
        """Without one there is no apex: dev serves page and API from the ALB
        DNS name."""
        assert attribute(self.rule, "count") == "var.enable_custom_domain ? 1 : 0"

    def test_matches_the_exact_apex_host(self):
        """A wildcard would also match www and loop. The background sync job
        and the manager Lambdas call the ALB DNS name, which must never be
        redirected."""
        host = _leaf_block(self.rule, "host_header")
        assert _list(host, "values") == ["var.frontend_domain"]

    def test_matches_page_loads_only(self):
        """A redirected POST is replayed as a GET, and a redirected API call
        fails in the browser (a cross-host redirect drops Authorization).
        Browsers send text/html in Accept only when loading a page."""
        method = _leaf_block(self.rule, "http_request_method")
        assert _list(method, "values") == ['"GET"', '"HEAD"']
        header = _leaf_block(self.rule, "http_header")
        assert _string(header, "http_header_name") == "Accept"
        assert _list(header, "values") == ['"*text/html*"']

    def test_sends_the_same_path_and_query_to_www(self):
        """Email action links and bookmarks on the apex keep working. 301, so
        browsers go straight to www next time."""
        redirect = _leaf_block(self.rule, "redirect")
        assert attribute(self.rule, "type") == '"redirect"'
        assert _string(redirect, "host") == "www.${var.frontend_domain}"
        assert _string(redirect, "path") == "/#{path}"
        assert _string(redirect, "query") == "#{query}"
        assert _string(redirect, "protocol") == "HTTPS"
        assert _string(redirect, "port") == "443"
        assert _string(redirect, "status_code") == "HTTP_301"

    def test_priority_sits_between_maintenance_and_premium(self):
        """The maintenance page must still win during a window, and the
        premium-manager Lambda allocates its rules from the premium band up."""
        priority = int(attribute(self.rule, "priority"))
        maintenance = re.search(
            r'^PRIORITY="(\d+)"$', read(MAINTENANCE_MODE_SH), re.MULTILINE
        )
        assert maintenance, "PRIORITY not found in maintenance_mode.sh"
        premium_start = (
            inspect.signature(create_alb_rule).parameters["start_priority"].default
        )
        assert int(maintenance.group(1)) < priority < premium_start

    def test_waits_for_the_image_rollout(self):
        """A page sent to www while an older bundle that calls a fixed API
        host still serves would call that host cross-origin and lose the
        session on reload. Without the dependency the apply creates the rule
        at its start, before the image is built and rolled out."""
        assert identifier_list(attribute(self.rule, "depends_on")) == [
            "null_resource.deploy_to_ecs"
        ]
        resource_block(read(DEPLOYMENT_TF), "null_resource", "deploy_to_ecs")


class TestCanonicalOrigin:
    """``local.canonical_origin`` and the services that name it."""

    def test_custom_domain_origin_is_www_over_https(self):
        assert _canonical_origin()[0] == "https://www.${var.frontend_domain}"

    def test_origin_without_one_is_the_alb_on_the_listener_port(self):
        """Dev serves on HTTP:8080; port 80 only redirects there."""
        assert _canonical_origin()[1] == "http://${aws_lb.autoscaling.dns_name}:8080"

    def test_apex_page_loads_go_to_that_origin(self):
        redirect = _leaf_block(
            resource_block(
                read(COMPUTE_DOMAIN_TF), "aws_lb_listener_rule", "apex_to_www"
            ),
            "redirect",
        )
        assert _canonical_origin()[0] == f"https://{_string(redirect, 'host')}"

    @pytest.mark.parametrize("name", sorted(TASK_DEFINITIONS))
    def test_every_service_names_it_as_the_frontend_url(self, name):
        """The origin /auth trusts for the refresh cookie (the public tier
        serves /auth), and links the app builds to itself. Unset, it falls
        back to localhost."""
        values = _env_values(_task_environment(name), "FRONTEND_URL")
        assert values == ["local.canonical_origin"]

    @pytest.mark.parametrize("name", STRIPE_TASK_DEFINITIONS)
    def test_stripe_checkout_returns_to_it(self, name):
        """A checkout that returns to another host finds no session there."""
        values = _env_values(_task_environment(name), "STRIPE_CALLBACK_URL")
        assert values == ["local.canonical_origin"]


class TestCorsOrigins:
    """``local.cors_origins`` and the services that read it.

    With ``*`` the app echoes any request origin with credentials allowed
    (``get_cors_origin_options``). The app itself is same-origin and needs no
    CORS; dev keeps the wildcard.
    """

    def test_custom_domain_allows_only_the_canonical_origin(self):
        value = attribute(read(MAIN_TF), "cors_origins")
        assert value == 'var.enable_custom_domain ? local.canonical_origin : "*"'

    @pytest.mark.parametrize("name", sorted(TASK_DEFINITIONS))
    def test_every_service_reads_it(self, name):
        values = _env_values(_task_environment(name), "CORS_ORIGINS")
        assert values == ["local.cors_origins"]
