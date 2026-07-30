/**
 * WS2 consumer-side contract test (#731).
 *
 * The frontend and backend share ONE fixture file
 * (frontend/src/utils/routing/__fixtures__/premium_routing/premium_contract.json).
 * The backend producer test (studio/tests/app/common/routers/
 * test_premium_contract_fixtures.py) is the per-PR contract authority: it
 * asserts each fixture is a drift-free instance of the FastAPI response models.
 *
 * This file is the consumer half:
 *   - the compile-time bindings below tie each fixture to its FE interface, so
 *     a renamed/retyped interface field fails `yarn build` and the IDE. jest
 *     runs through babel (types erased, no typecheck), so this half is a
 *     build/IDE gate, not a jest gate — a jest test cannot observe a TS rename.
 *   - the runtime tests verify the fixture is well-formed for the fields the
 *     frontend reads, that RoutingService consumes them, and that the routing
 *     header names match the production RoutingHeaders const.
 *
 * Identifier-omission guards already live in
 * studio/tests/app/common/routers/test_premium_api_contract.py; not duplicated.
 */

import { describe, test, expect } from "@jest/globals"

import type {
  PremiumAssignment,
  PremiumAssignmentResult,
  PremiumHeartbeatResult,
  PremiumReleaseResult,
  PremiumStatusResult,
  RoutingInfo,
} from "api/premium/PremiumAssignmentApi"
import { RoutingHeaders, UserTier } from "const/Subscription"
import contract from "utils/routing/__fixtures__/premium_routing/premium_contract.json"
import { RoutingService } from "utils/routing/RoutingService"


// Compile-time interface binding (build/IDE gate). Enum-typed fields are cast
// from the JSON's widened string; every other field is checked structurally,
// so an interface rename/retype fails to compile here. The nested assignment
// is checked through the PremiumStatusResult binding.
const _routingInfo: RoutingInfo = {
  ...contract.routing_info,
  user_tier: contract.routing_info.user_tier as UserTier,
}
const _assign: PremiumAssignmentResult = contract.premium_assign
const _release: PremiumReleaseResult = contract.premium_release
const _assignment: PremiumAssignment = contract.premium_status.assignment
const _status: PremiumStatusResult = {
  ...contract.premium_status,
  subscription_type: contract.premium_status.subscription_type as UserTier,
}
const _heartbeat: PremiumHeartbeatResult = {
  ...contract.premium_heartbeat,
  user_tier: contract.premium_heartbeat.user_tier as UserTier,
}
void [_routingInfo, _assign, _release, _assignment, _status, _heartbeat]

// Mock localStorage for the RoutingService instance used below.
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value
    },
    removeItem: (key: string) => {
      delete store[key]
    },
    clear: () => {
      store = {}
    },
  }
})()
Object.defineProperty(global, "localStorage", { value: localStorageMock })

describe("premium-routing contract fixtures (consumer side)", () => {
  test("no response body carries a user_id / uid identifier", () => {
    expect(JSON.stringify(contract)).not.toMatch(/"(user_id|uid)"\s*:/)
  })

  test("routing-info is well-formed for the fields the frontend reads", () => {
    const f = contract.routing_info
    expect(typeof f.user_tier).toBe("string")
    expect(typeof f.requires_premium_routing).toBe("boolean")
    expect(typeof f.routing_headers).toBe("object")
  })

  test("premium/assign is well-formed", () => {
    const f = contract.premium_assign
    expect(typeof f.message).toBe("string")
    expect(typeof f.assigned).toBe("boolean")
    expect(typeof f.instance_id).toBe("string")
    expect(typeof f.instance_id_hash).toBe("string")
    expect(typeof f.is_shared).toBe("boolean")
    expect(typeof f.assignment_source).toBe("string")
  })

  test("premium/assign (delete) is well-formed", () => {
    const f = contract.premium_release
    expect(typeof f.message).toBe("string")
    expect(typeof f.released).toBe("boolean")
    expect(typeof f.released_instance).toBe("string")
  })

  test("premium/status + nested assignment are well-formed", () => {
    const f = contract.premium_status
    expect(typeof f.subscription_type).toBe("string")
    expect(typeof f.is_premium).toBe("boolean")
    const a = f.assignment
    expect(typeof a.instance_id).toBe("string")
    expect(typeof a.instance_id_hash).toBe("string")
    expect(typeof a.assigned_at).toBe("string")
    expect(typeof a.status).toBe("string")
    expect(typeof a.is_shared).toBe("boolean")
  })

  test("premium/heartbeat is well-formed", () => {
    const f = contract.premium_heartbeat
    expect(typeof f.message).toBe("string")
    expect(typeof f.updated).toBe("boolean")
    expect(typeof f.user_tier).toBe("string")
    expect(typeof f.assignment_active).toBe("boolean")
  })

  test("free/logout is well-formed for logoutFreeUserApi's return shape", () => {
    const f = contract.free_logout
    expect(typeof f.message).toBe("string")
    expect(typeof f.logged_out).toBe("boolean")
    expect(typeof f.cleanup_after_minutes).toBe("number")
  })

  test("release-beacon is well-formed for the untyped BeaconResult shape", () => {
    const f = contract.release_beacon
    expect(typeof f.success).toBe("boolean")
    expect(typeof f.message).toBe("string")
  })

  test("routing header names match the RoutingHeaders const (case-insensitive)", () => {
    const h = contract.headers
    expect(RoutingHeaders.ROUTING_ID.toLowerCase()).toBe(
      h.routing_id.toLowerCase(),
    )
    expect(RoutingHeaders.USER_TIER.toLowerCase()).toBe(
      h.user_tier.toLowerCase(),
    )
    expect(RoutingHeaders.SERVED_BY_INSTANCE.toLowerCase()).toBe(
      h.served_by_instance.toLowerCase(),
    )
  })

  test("RoutingService consumes the assign fixture's routing fields", () => {
    const svc = new RoutingService()
    // Drive RoutingService with the fixture's fields the way
    // PremiumAssignmentContext does on a successful assign.
    svc.updateRoutingToken("rid-x")
    svc.setPremiumInstanceId(contract.premium_assign.instance_id_hash)
    svc.setPremiumShared(contract.premium_assign.is_shared)
    svc.setPremiumAssigned(true)

    expect(svc.getPremiumInstanceId()).toBe(
      contract.premium_assign.instance_id_hash,
    )
    expect(svc.isPremiumShared()).toBe(contract.premium_assign.is_shared)
    // The pinned routing-id header name is what RoutingService actually emits.
    expect(Object.keys(svc.getRoutingHeaders())).toContain(
      contract.headers.routing_id,
    )
  })
})
