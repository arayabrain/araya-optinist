"""
WS2 producer-side contract test (#731).

Asserts the FastAPI premium/routing response models agree with the shared
fixtures the frontend consumes:
  frontend/src/utils/routing/__fixtures__/premium_routing/premium_contract.json

The frontend reads the SAME file (frontend/src/utils/routing/
premiumRoutingContract.test.ts), so a shape drift on either side breaks one of
the two tests. The backend test container mounts the repo root at /app, so the
fixture is reachable by repo-relative path.

Identifier-omission guards already live in test_premium_api_contract.py; this
file does not duplicate them — it only checks the fixtures declare no
identifier keys and that each fixture is a faithful subset of its model.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from studio.app.common.schemas.premium import (
    FreeLogoutResponse,
    PremiumAssignResponse,
    PremiumHeartbeatResponse,
    PremiumReleaseResponse,
    PremiumStatusResponse,
    RoutingInfoResponse,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_FIXTURE = (
    _REPO_ROOT
    / "frontend/src/utils/routing/__fixtures__/premium_routing/premium_contract.json"
)
_AWS_CONSTANTS = _REPO_ROOT / "infrastructure" / "aws_constants.py"

# The fixture and the header source of truth live in sibling trees (frontend/,
# infrastructure/). They are present under the repo-root mount (.:/app); skip
# cleanly on a partial checkout rather than erroring at collection.
if not _FIXTURE.exists() or not _AWS_CONSTANTS.exists():
    pytest.skip(
        "premium contract fixture or aws_constants not present in this checkout",
        allow_module_level=True,
    )

CONTRACT = json.loads(_FIXTURE.read_text())


def _backend_routing_headers():
    # Header-name source of truth is infrastructure/aws_constants.py. Load it
    # straight from the file under a unique name: the app test suite stubs
    # sys.modules["aws_constants"] (see test_data_sync.py), so a plain import
    # would pick up that stub instead of the real constants.
    spec = importlib.util.spec_from_file_location(
        "_premium_contract_aws", _AWS_CONSTANTS
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RoutingHeaders


RoutingHeaders = _backend_routing_headers()

# The FE PremiumAssignment interface (nested under PremiumStatusResult). The
# response model types status.assignment only as Optional[dict], so its shape
# is unenforced by the model layer and must be pinned here.
STATUS_ASSIGNMENT_REQUIRED = {"instance_id", "assigned_at", "status", "is_shared"}
STATUS_ASSIGNMENT_OPTIONAL = {"instance_id_hash", "assignment_source"}

# (fixture key, response model) — the six typed endpoints.
TYPED_MODELS = [
    ("routing_info", RoutingInfoResponse),
    ("premium_assign", PremiumAssignResponse),
    ("premium_release", PremiumReleaseResponse),
    ("premium_status", PremiumStatusResponse),
    ("premium_heartbeat", PremiumHeartbeatResponse),
    ("free_logout", FreeLogoutResponse),
]


@pytest.mark.parametrize("key,model_cls", TYPED_MODELS)
def test_fixture_is_valid_for_model(key, model_cls):
    fixture = CONTRACT[key]
    # Constructs => every required field is present with a coercible type.
    model_cls(**fixture)
    # Every fixture key is a declared field => no drift / typo / stale key.
    unknown = set(fixture) - set(model_cls.__fields__)
    assert not unknown, f"{key} has keys absent from {model_cls.__name__}: {unknown}"


def test_status_nested_assignment_shape():
    # PremiumStatusResponse.assignment is Optional[dict] (unenforced), so pin
    # the nested shape against the FE PremiumAssignment interface here.
    a = CONTRACT["premium_status"]["assignment"]
    missing = STATUS_ASSIGNMENT_REQUIRED - set(a)
    assert not missing, f"nested assignment missing required fields: {missing}"
    unknown = set(a) - STATUS_ASSIGNMENT_REQUIRED - STATUS_ASSIGNMENT_OPTIONAL
    assert not unknown, f"nested assignment has unknown fields: {unknown}"
    assert "uid" not in a and "user_id" not in a


@pytest.mark.parametrize("key,_model_cls", TYPED_MODELS)
def test_fixture_carries_no_identifier(key, _model_cls):
    fixture = CONTRACT[key]
    assert "uid" not in fixture
    assert "user_id" not in fixture


def test_release_beacon_untyped_shape():
    # release-beacon stays response_model=Dict (untyped); pin its shape here.
    f = CONTRACT["release_beacon"]
    assert isinstance(f["success"], bool)
    assert isinstance(f.get("message", ""), str)


def test_header_names_match_backend_source_of_truth():
    h = CONTRACT["headers"]
    assert RoutingHeaders.ROUTING_ID.lower() == h["routing_id"].lower()
    assert RoutingHeaders.USER_TIER.lower() == h["user_tier"].lower()
    assert RoutingHeaders.SERVED_BY_INSTANCE.lower() == h["served_by_instance"].lower()
