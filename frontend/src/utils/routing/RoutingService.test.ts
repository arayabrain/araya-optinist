import { describe, test, expect, beforeEach } from "@jest/globals"

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

    test("should return X-Routing-ID header when token is set", () => {
      routingService.updateRoutingToken("abc123def456")

      const headers = routingService.getRoutingHeaders()

      expect(headers[RoutingHeaders.ROUTING_ID]).toBe("abc123def456")
    })

    test("should include X-User-Tier header for premium users", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingToken("abc123def456")
      routingService.updateRoutingInfo(premiumUser)

      const headers = routingService.getRoutingHeaders()

      expect(headers[RoutingHeaders.ROUTING_ID]).toBe("abc123def456")
      expect(headers[RoutingHeaders.USER_TIER]).toBe(UserTier.PREMIUM)
    })

    test("should include X-User-Tier header for free users", () => {
      const freeUser = createFreeUser()
      routingService.updateRoutingToken("abc123def456")
      routingService.updateRoutingInfo(freeUser)

      const headers = routingService.getRoutingHeaders()

      expect(headers[RoutingHeaders.ROUTING_ID]).toBe("abc123def456")
      expect(headers[RoutingHeaders.USER_TIER]).toBe(UserTier.FREE)
    })

    test("should return both headers required for ALB routing", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingToken("73279292c0867872")
      routingService.updateRoutingInfo(premiumUser)

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

    test("should identify Limit Grace user as premium", () => {
      const graceUser = createPremiumUser({
        subscription_status: SubscriptionStatus.LIMIT_GRACE,
      })
      routingService.updateRoutingInfo(graceUser)

      expect(routingService.getUserTier()).toBe(UserTier.PREMIUM)
      expect(routingService.requiresPremiumRouting()).toBe(true)
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

  describe("clearRoutingInfo", () => {
    test("should clear all routing state", () => {
      const premiumUser = createPremiumUser()
      routingService.updateRoutingToken("test-token")
      routingService.updateRoutingInfo(premiumUser)

      routingService.clearRoutingInfo()

      expect(routingService.getRoutingToken()).toBeNull()
      expect(routingService.getUserTier()).toBeNull()
      expect(routingService.requiresPremiumRouting()).toBe(false)
      expect(routingService.getRoutingHeaders()).toEqual({})
    })

    test("should remove token from localStorage", () => {
      routingService.updateRoutingToken("test-token")
      expect(localStorageMock.getItem("routing_id")).toBe("test-token")

      routingService.clearRoutingInfo()

      expect(localStorageMock.getItem("routing_id")).toBeNull()
    })
  })

  describe("localStorage persistence", () => {
    test("should load token from localStorage on initialization", () => {
      localStorageMock.setItem("routing_id", "stored-token")

      const newService = new RoutingService()

      expect(newService.getRoutingToken()).toBe("stored-token")
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
})
