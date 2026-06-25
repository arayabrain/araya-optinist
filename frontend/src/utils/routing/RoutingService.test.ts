import { describe, test, expect, beforeEach, jest } from "@jest/globals"

import { UserDTO } from "api/users/UsersApiDTO"
import {
  PlanName,
  RoutingHeaders,
  SubscriptionStatus,
  UserTier,
} from "const/Subscription"
import { RoutingService } from "utils/routing/RoutingService"

// Mock localStorage
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

describe("RoutingService", () => {
  let routingService: RoutingService

  const createPremiumUser = (overrides?: Partial<UserDTO>): UserDTO => ({
    uid: "test-uid-123",
    email: "premium@test.com",
    data_usage: 0,
    subscription_plan_name: PlanName.PREMIUM,
    subscription_status: SubscriptionStatus.PREMIUM,
    ...overrides,
  })

  const createFreeUser = (overrides?: Partial<UserDTO>): UserDTO => ({
    uid: "test-uid-456",
    email: "free@test.com",
    data_usage: 0,
    subscription_plan_name: PlanName.FREE,
    subscription_status: SubscriptionStatus.FREE,
    ...overrides,
  })

  beforeEach(() => {
    localStorageMock.clear()
    routingService = new RoutingService()
  })

  describe("getRoutingHeaders", () => {
    test("should return empty object when no routing token is set", () => {
      const headers = routingService.getRoutingHeaders()
      expect(headers).toEqual({})
    })

    test("should return empty object when token is set but premiumAssigned is false", () => {
      routingService.updateRoutingToken("abc123def456")

      const headers = routingService.getRoutingHeaders()

      expect(headers).toEqual({})
    })

    test("should return X-Routing-ID header when token is set and premiumAssigned is true", () => {
      routingService.updateRoutingToken("abc123def456")
      routingService.setPremiumAssigned(true)

      const headers = routingService.getRoutingHeaders()

      expect(headers[RoutingHeaders.ROUTING_ID]).toBe("abc123def456")
    })

    test("should include X-User-Tier header for premium users", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingToken("abc123def456")
      routingService.updateRoutingInfo(premiumUser)
      routingService.setPremiumAssigned(true)

      const headers = routingService.getRoutingHeaders()

      expect(headers[RoutingHeaders.ROUTING_ID]).toBe("abc123def456")
      expect(headers[RoutingHeaders.USER_TIER]).toBe(UserTier.PREMIUM)
    })

    test("should include X-User-Tier header for free users", () => {
      const freeUser = createFreeUser()
      routingService.updateRoutingToken("abc123def456")
      routingService.updateRoutingInfo(freeUser)
      routingService.setPremiumAssigned(true)

      const headers = routingService.getRoutingHeaders()

      expect(headers[RoutingHeaders.ROUTING_ID]).toBe("abc123def456")
      expect(headers[RoutingHeaders.USER_TIER]).toBe(UserTier.FREE)
    })

    test("should return both headers required for ALB routing", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingToken("73279292c0867872")
      routingService.updateRoutingInfo(premiumUser)
      routingService.setPremiumAssigned(true)

      const headers = routingService.getRoutingHeaders()

      // Both headers must be present for ALB routing rules to match
      expect(Object.keys(headers)).toContain("X-Routing-ID")
      expect(Object.keys(headers)).toContain("X-User-Tier")
      expect(headers["X-User-Tier"]).toBe("premium")
      expect(headers["X-Routing-ID"]).toBe("73279292c0867872")
    })
  })

  describe("updateRoutingToken", () => {
    test("should store routing token", () => {
      routingService.updateRoutingToken("test-token")

      expect(routingService.getRoutingToken()).toBe("test-token")
    })

    test("should persist token to localStorage", () => {
      routingService.updateRoutingToken("persisted-token")

      expect(localStorageMock.getItem("routing_id")).toBe("persisted-token")
    })
  })

  describe("updateRoutingInfo", () => {
    test("should identify premium user correctly", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingInfo(premiumUser)

      expect(routingService.getUserTier()).toBe(UserTier.PREMIUM)
      expect(routingService.requiresPremiumRouting()).toBe(true)
    })

    test("should identify free user correctly", () => {
      const freeUser = createFreeUser()
      routingService.updateRoutingInfo(freeUser)

      expect(routingService.getUserTier()).toBe(UserTier.FREE)
      expect(routingService.requiresPremiumRouting()).toBe(false)
    })

    test("should identify Limit Grace user as free", () => {
      const graceUser = createPremiumUser({
        subscription_status: SubscriptionStatus.LIMIT_GRACE,
      })
      routingService.updateRoutingInfo(graceUser)

      expect(routingService.getUserTier()).toBe(UserTier.FREE)
      expect(routingService.requiresPremiumRouting()).toBe(false)
    })

    test("should identify expired premium user as free", () => {
      const expiredUser = createPremiumUser({
        subscription_status: SubscriptionStatus.EXPIRED,
      })
      routingService.updateRoutingInfo(expiredUser)

      expect(routingService.getUserTier()).toBe(UserTier.FREE)
      expect(routingService.requiresPremiumRouting()).toBe(false)
    })
  })

  describe("premiumAssigned gate", () => {
    test("should default to false", () => {
      expect(routingService.isPremiumAssigned()).toBe(false)
    })

    test("should be set to true via setPremiumAssigned", () => {
      routingService.setPremiumAssigned(true)
      expect(routingService.isPremiumAssigned()).toBe(true)
    })

    test("should persist to localStorage", () => {
      routingService.setPremiumAssigned(true)
      expect(localStorageMock.getItem("premium_assigned")).toBe("true")

      routingService.setPremiumAssigned(false)
      expect(localStorageMock.getItem("premium_assigned")).toBe("false")
    })

    test("should load from localStorage on construction", () => {
      localStorageMock.setItem("premium_assigned", "true")
      const newService = new RoutingService()
      expect(newService.isPremiumAssigned()).toBe(true)
    })

    test("should gate routing headers - no headers when false", () => {
      routingService.updateRoutingToken("test-token")
      routingService.updateRoutingInfo(createPremiumUser())

      expect(routingService.getRoutingHeaders()).toEqual({})
    })

    test("should gate routing headers - headers returned when true", () => {
      routingService.updateRoutingToken("test-token")
      routingService.updateRoutingInfo(createPremiumUser())
      routingService.setPremiumAssigned(true)

      const headers = routingService.getRoutingHeaders()
      expect(headers[RoutingHeaders.ROUTING_ID]).toBe("test-token")
      expect(headers[RoutingHeaders.USER_TIER]).toBe(UserTier.PREMIUM)
    })
  })

  describe("clearRoutingInfo", () => {
    test("should clear all routing state including premiumAssigned", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingToken("test-token")
      routingService.updateRoutingInfo(premiumUser)
      routingService.setPremiumAssigned(true)

      routingService.clearRoutingInfo()

      expect(routingService.getRoutingToken()).toBeNull()
      expect(routingService.getUserTier()).toBeNull()
      expect(routingService.requiresPremiumRouting()).toBe(false)
      expect(routingService.isPremiumAssigned()).toBe(false)
      expect(routingService.getRoutingHeaders()).toEqual({})
    })

    test("should remove token from localStorage", () => {
      routingService.updateRoutingToken("test-token")
      expect(localStorageMock.getItem("routing_id")).toBe("test-token")

      routingService.clearRoutingInfo()

      expect(localStorageMock.getItem("routing_id")).toBeNull()
    })

    test("should remove premiumAssigned from localStorage", () => {
      routingService.setPremiumAssigned(true)
      expect(localStorageMock.getItem("premium_assigned")).toBe("true")

      routingService.clearRoutingInfo()

      expect(localStorageMock.getItem("premium_assigned")).toBeNull()
    })
  })

  describe("localStorage persistence", () => {
    test("should load token from localStorage on initialization", () => {
      localStorageMock.setItem("routing_id", "stored-token")

      const newService = new RoutingService()

      expect(newService.getRoutingToken()).toBe("stored-token")
    })

    test("should persist user tier to localStorage", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingInfo(premiumUser)

      expect(localStorageMock.getItem("routing_tier")).toBe("premium")
    })

    test("should load tier from localStorage on initialization", () => {
      localStorageMock.setItem("routing_tier", "premium")

      const newService = new RoutingService()

      expect(newService.getUserTier()).toBe("premium")
    })

    test("should include stored tier in headers after page refresh when premiumAssigned", () => {
      localStorageMock.setItem("routing_id", "stored-token")
      localStorageMock.setItem("routing_tier", "premium")
      localStorageMock.setItem("premium_assigned", "true")

      const newService = new RoutingService()
      const headers = newService.getRoutingHeaders()

      expect(headers["X-Routing-ID"]).toBe("stored-token")
      expect(headers["X-User-Tier"]).toBe("premium")
    })

    test("should not include headers after page refresh when premiumAssigned is false", () => {
      localStorageMock.setItem("routing_id", "stored-token")
      localStorageMock.setItem("routing_tier", "premium")

      const newService = new RoutingService()
      const headers = newService.getRoutingHeaders()

      expect(headers).toEqual({})
    })

    test("should ignore invalid tier values from localStorage", () => {
      localStorageMock.setItem("routing_tier", "invalid-tier")

      const newService = new RoutingService()

      expect(newService.getUserTier()).toBeNull()
    })

    test("should remove tier from localStorage on clear", () => {
      routingService.updateRoutingInfo(createPremiumUser())
      expect(localStorageMock.getItem("routing_tier")).toBe("premium")

      routingService.clearRoutingInfo()

      expect(localStorageMock.getItem("routing_tier")).toBeNull()
    })
  })

  describe("isRoutingInfoStale", () => {
    test("should return true when no routing info exists", () => {
      expect(routingService.isRoutingInfoStale()).toBe(true)
    })

    test("should return false immediately after updating routing info", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingInfo(premiumUser)

      expect(routingService.isRoutingInfoStale()).toBe(false)
    })
  })

  describe("premium-unreachable listeners", () => {
    test("emit invokes every registered listener with the detail", () => {
      const a = jest.fn()
      const b = jest.fn()
      routingService.onPremiumUnreachable(a)
      routingService.onPremiumUnreachable(b)

      routingService.emitPremiumUnreachable({ url: "/foo", status: 503 })

      expect(a).toHaveBeenCalledWith({ url: "/foo", status: 503 })
      expect(b).toHaveBeenCalledWith({ url: "/foo", status: 503 })
    })

    test("unsubscribe stops further invocations", () => {
      const listener = jest.fn()
      const unsubscribe = routingService.onPremiumUnreachable(listener)

      routingService.emitPremiumUnreachable({ status: 502 })
      unsubscribe()
      routingService.emitPremiumUnreachable({ status: 503 })

      expect(listener).toHaveBeenCalledTimes(1)
    })

    test("a throwing listener does not block the others", () => {
      const good = jest.fn()
      routingService.onPremiumUnreachable(() => {
        throw new Error("boom")
      })
      routingService.onPremiumUnreachable(good)

      routingService.emitPremiumUnreachable({ status: 503 })

      expect(good).toHaveBeenCalled()
    })
  })

  describe("premium-reachable listeners", () => {
    test("emit invokes every registered listener with the detail", () => {
      const a = jest.fn()
      const b = jest.fn()
      routingService.onPremiumReachable(a)
      routingService.onPremiumReachable(b)

      routingService.emitPremiumReachable({ url: "/foo", status: 200 })

      expect(a).toHaveBeenCalledWith({ url: "/foo", status: 200 })
      expect(b).toHaveBeenCalledWith({ url: "/foo", status: 200 })
    })

    test("unsubscribe stops further invocations", () => {
      const listener = jest.fn()
      const unsubscribe = routingService.onPremiumReachable(listener)

      routingService.emitPremiumReachable({ status: 200 })
      unsubscribe()
      routingService.emitPremiumReachable({ status: 200 })

      expect(listener).toHaveBeenCalledTimes(1)
    })

    test("a throwing listener does not block the others", () => {
      const good = jest.fn()
      routingService.onPremiumReachable(() => {
        throw new Error("boom")
      })
      routingService.onPremiumReachable(good)

      routingService.emitPremiumReachable({ status: 200 })

      expect(good).toHaveBeenCalled()
    })

    test("reachable and unreachable listener pools are independent", () => {
      const unreachable = jest.fn()
      const reachable = jest.fn()
      routingService.onPremiumUnreachable(unreachable)
      routingService.onPremiumReachable(reachable)

      routingService.emitPremiumUnreachable({ status: 503 })
      expect(unreachable).toHaveBeenCalledTimes(1)
      expect(reachable).not.toHaveBeenCalled()

      routingService.emitPremiumReachable({ status: 200 })
      expect(reachable).toHaveBeenCalledTimes(1)
      expect(unreachable).toHaveBeenCalledTimes(1)
    })

    test("forwards sentAt on both event types", () => {
      const reachable = jest.fn()
      const unreachable = jest.fn()
      routingService.onPremiumReachable(reachable)
      routingService.onPremiumUnreachable(unreachable)

      routingService.emitPremiumReachable({ status: 200, sentAt: 1111 })
      routingService.emitPremiumUnreachable({ status: 503, sentAt: 2222 })

      expect(reachable).toHaveBeenCalledWith({ status: 200, sentAt: 1111 })
      expect(unreachable).toHaveBeenCalledWith({ status: 503, sentAt: 2222 })
    })
  })

  describe("premiumInstanceId", () => {
    test("setPremiumInstanceId / getPremiumInstanceId round-trip", () => {
      expect(routingService.getPremiumInstanceId()).toBeNull()

      routingService.setPremiumInstanceId("abc123hash")
      expect(routingService.getPremiumInstanceId()).toBe("abc123hash")

      routingService.setPremiumInstanceId(null)
      expect(routingService.getPremiumInstanceId()).toBeNull()
    })

    test("persists instance ID to localStorage", () => {
      routingService.setPremiumInstanceId("hash-from-api")
      expect(localStorageMock.getItem("premium_instance_id")).toBe(
        "hash-from-api",
      )
    })

    test("clears instance ID from localStorage when set to null", () => {
      routingService.setPremiumInstanceId("hash-from-api")
      routingService.setPremiumInstanceId(null)
      expect(localStorageMock.getItem("premium_instance_id")).toBeNull()
    })

    test("loads instance ID from localStorage on initialization", () => {
      localStorageMock.setItem("premium_instance_id", "stored-hash")

      const newService = new RoutingService()
      expect(newService.getPremiumInstanceId()).toBe("stored-hash")
    })

    test("clearRoutingInfo clears premium instance ID", () => {
      routingService.setPremiumInstanceId("hash-from-api")
      expect(routingService.getPremiumInstanceId()).toBe("hash-from-api")

      routingService.clearRoutingInfo()

      expect(routingService.getPremiumInstanceId()).toBeNull()
      expect(localStorageMock.getItem("premium_instance_id")).toBeNull()
    })
  })
})
