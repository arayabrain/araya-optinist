import * as fs from "fs"
import * as http from "http"
import { AddressInfo } from "net"
import * as os from "os"
import * as path from "path"
import * as zlib from "zlib"

import { expect, test } from "@playwright/test"

import {
  accessLine,
  dataviewRoutingProblems,
  ListenerRule,
  targetStateProblems,
} from "./aws"
import {
  accountProblems,
  inUseBy,
  laneSkipReason,
  newcomers,
  parseAccounts,
  readConfig,
  targetProblems,
} from "./config"
import { header, send, setCookie, userAgent } from "./http"
import {
  cutoffAt,
  decideStep,
  loadPlan,
  Plan,
  planProblems,
  ScreenPlan,
  targetStep,
} from "./plan"
import {
  bucket,
  differences,
  fmtS,
  improvement,
  readSteps,
  renderReport,
  RunReport,
} from "./report"
import { devIsUp } from "../helpers"

import {
  fiveXxReason,
  leftoverDebts,
  owe,
  probeLoop,
  prober,
  probeStreak,
  watchStreak,
} from "./runner"
import {
  CaptureRow,
  checkDashboardCapture,
  CaptureChanged,
  dialogSpec,
  dialogUser,
  itemName,
  publicSpec,
  routeOf,
  StepContext,
  VirtualUser,
} from "./screens"
import {
  describeStop,
  failuresOf,
  figuresOf,
  pct,
  ProbeRecord,
  RequestRecord,
  StepFigures,
  StepSummary,
  summarize,
} from "./summary"

// The load lane's logic, without a browser or a deployed environment. The
// HTTP cases run against a server on localhost.

const DEV = "http://development-optinist-lb-1.example.com:8080"

function summary(overrides: Partial<StepSummary> = {}): StepSummary {
  return {
    screen: "dialog",
    vus: 1,
    abort_reason: null,
    free_instance_hash: "abc",
    free_served_by: ["abc"],
    vu_done: 1,
    shown: { p50: null, p95: null, max: null },
    done: { p50: 1, p95: 1, max: 1 },
    requests: {
      reproduce: {
        count: 1,
        ok: 1,
        non_200: [],
        p50: 0.2,
        p95: 0.2,
        max: 0.2,
        bytes_total: 10,
      },
    },
    probes: {
      public: { count: 20, failed: 0, p50: 0.03, max: 0.05 },
      free: { count: 20, failed: 0, p50: 0.03, max: 0.1 },
    },
    release_at: 10.5,
    ...overrides,
  }
}

function figures(overrides: Partial<StepFigures> = {}): StepFigures {
  return { ...figuresOf(summary()), ...overrides }
}

test.describe("load lane: figures", () => {
  test("percentiles round the index half to even, as the reference tool does", () => {
    const ten = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    // index 4.5 -> 4: the lower middle value, where Math.round would take 6
    expect(pct(ten, 50)).toBe(5)
    expect(pct(ten, 95)).toBe(10)
    expect(pct([3, 1, 2], 50)).toBe(2)
    expect(pct([], 50)).toBeNull()
  })

  test("a step summary counts answers, failures and the instances that answered", () => {
    const requests: RequestRecord[] = [
      {
        vu: 0,
        name: "users_me",
        route: "public",
        start: 0,
        status: 200,
        dur: 1.2,
        bytes: 5,
        encoding: "gzip",
      },
      {
        vu: 1,
        name: "users_me",
        route: "public",
        start: 0,
        status: 401,
        dur: 0.3,
        bytes: 3,
      },
      {
        vu: 2,
        name: "users_me",
        route: "public",
        start: 0,
        status: null,
        dur: 9,
        error: "stopped",
      },
      {
        vu: 0,
        name: "refresh_storage",
        route: "free",
        start: 0,
        status: 200,
        dur: 2,
        served_by: "h1",
      },
      {
        vu: 1,
        name: "refresh_storage",
        route: "free",
        start: 0,
        status: 200,
        dur: 3,
        served_by: "h2",
      },
    ]
    const s = summarize({
      screen: "dashboard",
      vus: 3,
      abortReason: null,
      freeHash: "h1",
      requests,
      probes: [{ target: "public", t: 1, status: 200, dur: 0.05 }],
      vuRecords: [
        { vu: 0, shown: 1.5, done: 4 },
        { vu: 1, aborted: true },
      ],
      releaseAt: 10,
    })
    expect(s.requests.users_me).toEqual({
      count: 3,
      ok: 1,
      non_200: ["401", "stopped"],
      p50: 1.2,
      p95: 1.2,
      max: 1.2,
      bytes_total: 8,
      encoded: 1,
    })
    expect(s.free_served_by).toEqual(["h1", "h2"])
    expect(s.vu_done).toBe(1)
    expect(s.shown.max).toBe(1.5)
    expect(s.probes.free).toEqual({ count: 0, failed: 0, p50: null, max: null })
    expect(failuresOf(s)).toEqual([
      "1 / 3 users done",
      "users_me: 401, stopped",
      "1 other free instance(s) answered",
    ])
  })

  test("the anonymous refresh must be refused, and anything else must answer 200", () => {
    const anon = (non_200: string[], ok: number) =>
      summary({
        requests: {
          auth_refresh_anon: {
            count: 2,
            ok,
            non_200,
            p50: null,
            p95: null,
            max: null,
            bytes_total: 0,
          },
        },
      })
    expect(failuresOf(anon(["401"], 0))).toEqual([])
    expect(failuresOf(anon(["401"], 1))).toEqual(["auth_refresh_anon: 200"])
  })

  test("a stop reads in figures, without the probe's instance hash", () => {
    // The reference tool's Python form, as in the 2026-11-05 data
    const python = summary({
      abort_reason:
        "probe public: {'target': 'public', 't': 12.398, 'status': None, 'dur': 5.002, " +
        "'error': 'ReadTimeout'}",
      release_at: 10.709,
    })
    expect(describeStop(python)).toBe(
      "public probe ≥ 5.00 s, 1.7 s after the release",
    )
    expect(figuresOf(python).stopped_by).toBe("public")
    const lane = summary({
      abort_reason:
        'probe free: {"target":"free","t":3,"status":503,"dur":0.1,"served_by":"secret-hash"}',
    })
    expect(describeStop(lane)).toBe(
      "free probe answered 503, before the release",
    )
    expect(failuresOf(lane).join()).not.toContain("secret-hash")
    expect(describeStop(summary({ abort_reason: "5xx 3/40" }))).toBe("5xx 3/40")
  })

  test("5xx stop the step only past 5 % of at least 20 answers", () => {
    const rows = (ok: number, five: number) =>
      [...Array(ok).fill(200), ...Array(five).fill(503)].map((status) => ({
        vu: 0,
        name: "x",
        route: "free" as const,
        start: 0,
        status,
        dur: 0.1,
      }))
    expect(fiveXxReason(rows(18, 1))).toBeNull()
    expect(fiveXxReason(rows(19, 1))).toBeNull()
    expect(fiveXxReason(rows(18, 2))).toBe("5xx 2/20")
  })

  test("a cool-down ends the run on a service's second bad probe in a row, not on a lone one", () => {
    const limits = { public: 5, free: 5 }
    const bad = { public: 0, free: 0 }
    const probe = (
      target: "public" | "free",
      dur: number,
      status: number | null = 200,
    ): ProbeRecord => ({ target, t: 0, status, dur })
    // Lone bad probes on either service, then free's second bad one in a row
    expect(watchStreak(bad, probe("free", 5.001, null), limits)).toBeNull()
    expect(watchStreak(bad, probe("public", 1.968), limits)).toBeNull()
    expect(watchStreak(bad, probe("free", 0.073), limits)).toBeNull()
    expect(watchStreak(bad, probe("free", 5.2), limits)).toBeNull()
    expect(watchStreak(bad, probe("public", 5.0), limits)).toBeNull()
    expect(watchStreak(bad, probe("free", 0.1, 503), limits)).toMatch(
      /^probe free: /,
    )
  })

  test("a probe at its threshold is a slow service, two failed under it one that stopped answering", () => {
    const limits = { public: 5, free: 5 }
    const probe = (dur: number, status: number | null): ProbeRecord => ({
      target: "free",
      t: 12,
      status,
      dur,
      error: status === null ? "Timeout" : undefined,
    })
    const slow = probeStreak({ public: 0, free: 0 }, probe(5.01, null), limits)
    const fails = { public: 0, free: 0 }
    expect(probeStreak(fails, probe(0.05, 401), limits)).toBeNull()
    const down = probeStreak(fails, probe(0.05, 401), limits)
    const by = (reason: string | null) =>
      figuresOf(summary({ abort_reason: reason })).stopped_by
    expect(by(slow)).toBe("free")
    expect(by(down)).toBe("free_down")
    expect(by("deadline: 120 s passed")).toBe("deadline")
    expect(describeStop(summary({ abort_reason: down }))).toBe(
      "free stopped answering: two probes in a row failed, 1.5 s after the release",
    )
  })
})

test.describe("load lane: plan", () => {
  const plan = (): Plan => JSON.parse(JSON.stringify(loadPlan("default")))
  const screenOf = (p: Plan, name: string) =>
    p.screens.find((s) => s.screen === name) as ScreenPlan

  test("the committed plans pass their own checks", () => {
    expect(planProblems(loadPlan("default"))).toEqual([])
    expect(planProblems(loadPlan("parity"))).toEqual([])
  })

  test("a plan that would outrun the health checks is refused", () => {
    const p = plan()
    p.probe_stop_s.public = 10
    p.cooldown_s = 60
    screenOf(p, "dialog").steps.reverse()
    screenOf(p, "dashboard").steps[0].when = ["previous.free_probe_max < 3"]
    screenOf(p, "public").steps[3].when = ["previous.public_probe_max<3"]
    const problems = planProblems(p).join("\n")
    expect(problems).toContain(
      "plan: public threshold must be above 0 and at most 5 s",
    )
    expect(problems).toContain("cooldown_s must be at least 240")
    expect(problems).toContain("dialog: steps must rise")
    expect(problems).toContain(
      "dashboard: the first step has no previous step to test",
    )
    expect(problems).toContain(
      'public 20: condition "previous.public_probe_max<3"',
    )
  })

  test("by default each screen is held to the largest count measured on 2026-11-05", () => {
    const gates = { goAhead: false }
    const targets = plan().screens.map((s) => [
      s.screen,
      targetStep(s, gates)?.users,
    ])
    expect(targets).toEqual([
      ["dashboard", 50],
      ["usersme", 50],
      ["dialog", 20],
      ["public", 30],
    ])
  })

  test("a step above the default cap needs both the raised cap and the go-ahead", () => {
    const dashboard = screenOf(plan(), "dashboard")
    const hundred = dashboard.steps[3]
    expect(
      decideStep(dashboard, hundred, null, { goAhead: true }),
    ).toMatchObject({
      run: false,
      reason: "above the cap of 50",
    })
    expect(
      decideStep(dashboard, hundred, null, {
        capOverride: 100,
        goAhead: false,
      }),
    ).toMatchObject({
      run: false,
      reason: "needs LOAD_GO_AHEAD=1",
    })
    expect(
      decideStep(dashboard, hundred, null, { capOverride: 100, goAhead: true }),
    ).toEqual({ run: true })
    expect(
      targetStep(dashboard, { capOverride: 100, goAhead: true })?.users,
    ).toBe(100)
  })

  test("a stopped step ends its screen unless the next step names that stop", () => {
    const p = plan()
    const gates = { goAhead: false }
    const dashboard = screenOf(p, "dashboard")
    const dialog = screenOf(p, "dialog")
    const freeStop = figures({ screen: "dashboard", stopped_by: "free" })
    const publicStop = figures({ screen: "dashboard", stopped_by: "public" })
    // 2026-11-05: the 10-user Dashboard stopped on the free probe and 50 still ran
    expect(decideStep(dashboard, dashboard.steps[2], freeStop, gates)).toEqual({
      run: true,
    })
    expect(
      decideStep(dashboard, dashboard.steps[2], publicStop, gates),
    ).toMatchObject({ run: false })
    // A free instance that stopped answering is not a slow one
    const freeDown = figures({ screen: "dashboard", stopped_by: "free_down" })
    expect(
      decideStep(dashboard, dashboard.steps[2], freeDown, gates),
    ).toMatchObject({ run: false })
    expect(decideStep(dialog, dialog.steps[1], freeStop, gates)).toMatchObject({
      run: false,
      reason: "previous step: not previous.stopped_by == none",
    })
    expect(decideStep(dialog, dialog.steps[1], figures(), gates)).toEqual({
      run: true,
    })
  })

  test("PublicDataview goes on to 20 and 30 only while the public probe stays under 3 s", () => {
    const pub = screenOf(plan(), "public")
    const gates = { goAhead: false }
    const at = (max: number | null) =>
      figures({ screen: "public", public_probe_max: max })
    expect(decideStep(pub, pub.steps[3], at(2.5), gates)).toEqual({ run: true })
    expect(decideStep(pub, pub.steps[3], at(3), gates)).toMatchObject({
      run: false,
    })
    // A figure nobody measured cannot pass a condition
    expect(decideStep(pub, pub.steps[3], at(null), gates)).toMatchObject({
      run: false,
    })
  })

  test("the cut-off is the plan's daily time or the window's end, whichever comes first", () => {
    const p = plan()
    const morning = new Date("2026-11-10T01:00:00Z")
    expect(
      cutoffAt(p, morning, new Date("2026-11-11T00:00:00Z")).toISOString(),
    ).toBe("2026-11-10T12:40:00.000Z")
    expect(
      cutoffAt(p, morning, new Date("2026-11-10T05:00:00Z")).toISOString(),
    ).toBe("2026-11-10T05:00:00.000Z")
    // Past today's cut-off there is none left, not tomorrow's
    const evening = new Date("2026-11-10T12:50:00Z")
    expect(
      cutoffAt(p, evening, new Date("2026-11-12T00:00:00Z")).toISOString(),
    ).toBe("2026-11-10T12:40:00.000Z")
    // Started after Sunday's 23:00 UTC start: Monday's stop
    const sunday = new Date("2026-11-08T23:30:00Z")
    expect(
      cutoffAt(p, sunday, new Date("2026-11-10T00:00:00Z")).toISOString(),
    ).toBe("2026-11-09T12:40:00.000Z")
  })

  test("a plan cannot run past the scheduled stop, above 100 users, without a step, or raise its own caps", () => {
    const p = plan()
    expect(planProblems({ ...p, cutoff_utc: "12:55" })).toEqual([
      "cutoff_utc 12:55 leaves no room for the last step, the settle and the post-checks before the 13:00 UTC scheduled stop",
    ])
    expect(planProblems({ ...p, cutoff_utc: "13:30" })).toHaveLength(1)
    expect(planProblems({ ...p, name: "../x" })).toEqual([
      "name must be lower-case letters, digits and dashes",
    ])
    const big = plan()
    screenOf(big, "dialog").steps.push({ users: 500 })
    expect(planProblems(big)).toContain(
      "dialog: 500 users is above the 100 the lane allows",
    )
    const empty = plan()
    screenOf(empty, "usersme").steps = []
    expect(planProblems(empty)).toContain("usersme: no step")
    // A local file's own cap does not count: the default's does
    const local = plan()
    local.name = "local"
    screenOf(local, "dialog").cap = 100
    const file = path.join(os.tmpdir(), `load-plan-${process.pid}.json`)
    fs.writeFileSync(file, JSON.stringify(local))
    try {
      const loaded = loadPlan(file)
      expect(screenOf(loaded, "dialog").cap).toBe(20)
      expect(loaded.local).toBe(file)
      expect(loadPlan("default").local).toBeUndefined()
    } finally {
      fs.rmSync(file)
    }
  })
})

test.describe("load lane: configuration", () => {
  test("the lane only asks to run when RUN_LOAD_AWS is set", () => {
    expect(laneSkipReason({})).toContain("RUN_LOAD_AWS=1")
    expect(laneSkipReason({ RUN_LOAD_AWS: "1" })).toBe("")
  })

  test("only development's ALB origin on :8080 is a target", () => {
    expect(targetProblems({ BASE_URL: DEV })).toEqual([])
    expect(
      targetProblems({ BASE_URL: "https://www.araya-optinist.com" }),
    ).toHaveLength(1)
    expect(targetProblems({ BASE_URL: DEV.replace(":8080", "") })).toHaveLength(
      1,
    )
    expect(
      targetProblems({ BASE_URL: DEV, HEALTH_ENV: "subscr" }),
    ).toHaveLength(1)
    // Matched as a parsed origin, not as text: each of these would send the
    // accounts somewhere else, or ask for a path
    for (const other of [
      "http://development-optinist@production.example:8080",
      "http://production.example:8080/development-optinist",
      "http://production.example/?x=development-optinist:8080",
      "https://development-optinist-lb-1.example.com:8080",
      `${DEV}/api`,
    ]) {
      expect(targetProblems({ BASE_URL: other }), other).toHaveLength(1)
    }
  })

  test("a flag is on only for 1 or true", () => {
    expect(laneSkipReason({ RUN_LOAD_AWS: "false" })).not.toBe("")
    expect(laneSkipReason({ RUN_LOAD_AWS: "no" })).not.toBe("")
    expect(laneSkipReason({ RUN_LOAD_AWS: "true" })).toBe("")
  })

  test("an account that becomes active during the run is a newcomer; the run's own are not", () => {
    const accounts = [{ email: "load1@x", password: "p" }]
    expect(
      newcomers(["early@x"], ["early@x", "Load1@x", "late@x"], accounts),
    ).toEqual(["late@x"])
  })

  test("a malformed accounts file is named by line, never by content", () => {
    expect(parseAccounts("a@x\tpw1\n\nb@x\tpw2\n")).toEqual([
      { email: "a@x", password: "pw1" },
      { email: "b@x", password: "pw2" },
    ])
    let message = ""
    try {
      parseAccounts("a@x\tpw1\nb@x pw-secret\n")
    } catch (e) {
      message = (e as Error).message
    }
    expect(message).toBe("accounts file line 2 is not email<TAB>password")
  })

  test("more than five distinct accounts, the suite's own user included, is refused", () => {
    const five = Array.from({ length: 5 }, (_, i) => ({
      email: `u${i}@x`,
      password: "p",
    }))
    expect(accountProblems(five, { TEST_USER_EMAIL: "U0@x" })).toEqual([])
    expect(
      accountProblems(five, { TEST_USER_EMAIL: "other@x" }).join(),
    ).toContain("6 distinct accounts")
    // globalSetup signs the admin in when its password is set
    expect(
      accountProblems(five, {
        TEST_USER_EMAIL: "U0@x",
        TEST_ADMIN_EMAIL: "admin@x",
        TEST_ADMIN_PASSWORD: "p",
      }).join(),
    ).toContain("6 distinct accounts")
    expect(accountProblems([five[0], five[0]], {}).join()).toContain("repeats")
  })

  test("a shared load account active before the start counts, labelled", () => {
    expect(
      inUseBy(["U1@x", "colleague@x"], [{ email: "u1@x", password: "p" }]),
    ).toEqual(["U1@x (a load account)", "colleague@x"])
  })

  test("every missing or malformed setting is listed at once", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "load-config-"))
    const accounts = path.join(dir, "accounts.tsv")
    fs.writeFileSync(accounts, "owner@x\tpw\n")
    const ok = {
      BASE_URL: DEV,
      LOAD_ACCOUNTS_FILE: accounts,
      LOAD_DIALOG_RECORD: "7/6ab411e3",
      LOAD_DIALOG_OWNER: "Owner@x",
      LOAD_WINDOW_END: new Date(Date.now() + 3_600_000).toISOString(),
      LOAD_IMAGE_DIGEST: `sha256:${"a".repeat(64)}`,
    }
    const cfg = readConfig(ok)
    expect(cfg.dialogOwner.email).toBe("owner@x")
    expect(cfg.publicRecord).toEqual({ workspaceId: 7, uid: "6ab411e3" })
    let message = ""
    try {
      readConfig({
        ...ok,
        LOAD_WINDOW_END: "2026-11-10T17:00",
        LOAD_DIALOG_OWNER: "x@x",
        LOAD_IMAGE_DIGEST: "abc",
      })
    } catch (e) {
      message = (e as Error).message
    }
    expect(message).toContain("LOAD_WINDOW_END needs its UTC offset")
    expect(message).toContain(
      "LOAD_DIALOG_OWNER is not one of the accounts file's accounts",
    )
    expect(message).toContain("LOAD_IMAGE_DIGEST must be sha256:<64 hex>")
    expect(message).not.toContain("pw")
  })

  // Run with RUN_LOAD_AWS=1: playwright.config.ts must count the lane among
  // the mutating flags, which switch retries off
  test("RUN_LOAD_AWS switches retries off", () => {
    test.skip(
      !process.env.RUN_LOAD_AWS,
      "checks the config under RUN_LOAD_AWS=1",
    )
    expect(test.info().project.retries).toBe(0)
  })
})

test.describe("load lane: request sets", () => {
  const row = (
    phase: string,
    method: string,
    p: string,
    extra: Partial<CaptureRow> = {},
  ): CaptureRow => ({
    phase,
    method,
    path: p,
    type: "xhr",
    status: 200,
    ...extra,
  })
  const dashboard = [
    row("dashboard_new_tab", "POST", "/auth/refresh"),
    row("dashboard_new_tab", "GET", "/is_standalone"),
    row("dashboard_new_tab", "GET", "/users/me"),
    row("dashboard_new_tab", "GET", "/users/me"),
    row("dashboard_new_tab", "GET", "/storage-limit-alerts/limit-warning"),
    row("dashboard_new_tab", "POST", "/log-report/frontend-errors"),
    row("dashboard_new_tab", "POST", "/workspaces/refresh-storage"),
    row("dashboard_new_tab", "GET", "/announcement", { type: "fetch" }),
    row("dashboard_new_tab", "GET", "/static/js/main.js", { type: "script" }),
  ]

  test("labels keep the output's node without its random tail, and drop database ids", () => {
    expect(
      itemName(
        "/api/visualizations/image/7/u/suite2p_registration_e4kbyoqliv/meanImg.json?workspace_id=7",
      ),
    ).toBe("image:suite2p_registration/meanImg.json")
    expect(
      itemName(
        "/api/visualizations/image/7/u/suite2p_file_convert_bwrzk3a80i/meanImg.json?workspace_id=7",
      ),
    ).toBe("image:suite2p_file_convert/meanImg.json")
    expect(
      itemName(
        "/api/visualizations/image/7/u/suite2p_registration_e4kbyoqliv/tiff/mc_images/mc_images.tif",
      ),
    ).toBe("image:suite2p_registration/mc_images.tif")
    expect(
      itemName(
        "/api/visualizations/inittimedata/7/u/suite2p_roi_0cw3ccyc1w/fluorescence",
      ),
    ).toBe("inittimedata:suite2p_roi/fluorescence")
    expect(itemName("/api/visualizations/thumbnail/382/79ceea1d/input")).toBe(
      "thumbnail:input",
    )
    expect(itemName("/api/dataview/hidden_outputs/992")).toBe("hidden_outputs")
    expect(routeOf("/users/me")).toBe("public")
    expect(routeOf("/api/dataview/workflow/reproduce/7/u")).toBe("free")
  })

  test("the Dashboard's capture must still be the replay's seven requests", () => {
    expect(() => checkDashboardCapture(dashboard)).not.toThrow()
    const withoutAnnouncement = dashboard.filter(
      (r) => r.path !== "/announcement",
    )
    expect(() => checkDashboardCapture(withoutAnnouncement)).toThrow(
      /now sends/,
    )
    const more = [
      ...dashboard,
      row("dashboard_new_tab", "GET", "/users/me/premium/status"),
    ]
    expect(() => checkDashboardCapture(more)).toThrow(CaptureChanged)
    expect(() => checkDashboardCapture(dashboard.slice(1))).toThrow(/now sends/)
  })

  test("the owner's OutputsView replays the recorded open, not the warm-up", () => {
    const record = { workspaceId: 7, uid: "6ab411e3" }
    const rows = [
      row(
        "dialog_warmup",
        "GET",
        "/api/visualizations/image/7/6ab411e3/n/old.json",
      ),
      row("dialog", "GET", "/api/dataview/workflow/reproduce/7/6ab411e3"),
      row("dialog", "GET", "/api/visualizations/image/7/6ab411e3/n/Vcorr.json"),
      row("dialog", "GET", "/api/dataview/hidden_outputs/992"),
      row("dialog", "POST", "/log-report/frontend-errors"),
    ]
    expect(dialogSpec(rows, record)).toEqual({
      reproduce: "/api/dataview/workflow/reproduce/7/6ab411e3",
      items: [
        "/api/visualizations/image/7/6ab411e3/n/Vcorr.json",
        "/api/dataview/hidden_outputs/992",
      ],
    })
    expect(() => dialogSpec(rows, { workspaceId: 7, uid: "other" })).toThrow(
      CaptureChanged,
    )
  })

  test("the PublicDataview visit keeps each item's routing header and leaves the warm-up and the filter out", () => {
    const record = { workspaceId: 7, uid: "6ab411e3" }
    const pub = { request_headers: { dataview_public_request: "true" } }
    const rows = [
      row("warmup_list", "GET", "/api/public/dataview?offset=0&limit=50"),
      row("public_list", "POST", "/auth/refresh", { status: 401 }),
      row("public_list", "GET", "/is_standalone"),
      row("public_list", "GET", "/api/public/dataview?offset=0&limit=50"),
      row(
        "public_list",
        "GET",
        "/api/visualizations/thumbnail/7/6ab411e3/input",
        pub,
      ),
      row(
        "filter",
        "GET",
        "/api/public/dataview?uid=6ab411e3&offset=0&limit=50",
      ),
      row(
        "dialog",
        "GET",
        "/api/public/dataview/workflow/reproduce/7/6ab411e3",
      ),
      row("dialog", "GET", "/api/visualizations/thumbnail/2/e5c5d07b/roi", pub),
      row(
        "dialog",
        "GET",
        "/api/visualizations/image/7/6ab411e3/n/max_proj.json",
        pub,
      ),
    ]
    const spec = publicSpec(rows, record)
    expect(spec.list).toBe("/api/public/dataview?offset=0&limit=50")
    expect(spec.thumbnails.map((t) => t.path)).toEqual([
      "/api/visualizations/thumbnail/7/6ab411e3/input",
      "/api/visualizations/thumbnail/2/e5c5d07b/roi",
    ])
    expect(spec.items).toEqual([
      {
        path: "/api/visualizations/image/7/6ab411e3/n/max_proj.json",
        headers: {
          Accept: "application/json",
          DATAVIEW_PUBLIC_REQUEST: "true",
        },
      },
    ])
    expect(() =>
      publicSpec(
        rows.filter((r) => !r.path.includes("reproduce")),
        record,
      ),
    ).toThrow(CaptureChanged)
  })

  test("a PublicDataview visit with a request the replay does not send fails", () => {
    const record = { workspaceId: 7, uid: "6ab411e3" }
    const rows = [
      row("public_list", "POST", "/auth/refresh", { status: 401 }),
      row("public_list", "GET", "/is_standalone"),
      row("public_list", "GET", "/api/public/dataview?offset=0&limit=50"),
      row("public_list", "GET", "/api/visualizations/thumbnail/7/6ab411e3/i"),
      row(
        "dialog",
        "GET",
        "/api/public/dataview/workflow/reproduce/7/6ab411e3",
      ),
      row("dialog", "GET", "/api/visualizations/image/7/6ab411e3/n/a.json"),
    ]
    expect(() => publicSpec(rows, record)).not.toThrow()
    const more = [
      ...rows,
      row("public_list", "GET", "/announcement", { type: "fetch" }),
    ]
    expect(() => publicSpec(more, record)).toThrow(/now sends .*announcement/)
    expect(() =>
      publicSpec(
        rows.filter((r) => r.path !== "/is_standalone"),
        record,
      ),
    ).toThrow(CaptureChanged)
  })
})

test.describe("load lane: HTTP", () => {
  let server: http.Server
  let base: URL
  let open = 0
  let peak = 0
  const held: http.ServerResponse[] = []
  let acceptEncoding: string | undefined
  let connections = 0
  const json = Buffer.from(JSON.stringify({ items: "x".repeat(4_000) }))

  test.beforeAll(async () => {
    server = http.createServer((req, res) => {
      open++
      peak = Math.max(peak, open)
      res.on("close", () => open--)
      if (req.url === "/hold") {
        held.push(res)
        return
      }
      if (req.url === "/file") {
        // As a FileResponse: the headers first, the body after them
        res.setHeader("Content-Length", "5")
        res.flushHeaders()
        setTimeout(() => res.end("bytes"), 200)
        return
      }
      if (req.url === "/json") {
        // As the API answers: gzip only for a client that accepts it
        acceptEncoding = req.headers["accept-encoding"]
        const gz = /\bgzip\b/.test(acceptEncoding ?? "")
        const body = gz ? zlib.gzipSync(json) : json
        if (gz) res.setHeader("Content-Encoding", "gzip")
        res.setHeader("Content-Length", String(body.length))
        res.end(body)
        return
      }
      res.setHeader("Set-Cookie", [
        "AWSALBCORS=x; Path=/",
        "AWSALB=pin-value; Expires=Thu; Path=/",
      ])
      res.setHeader("Content-Length", "11")
      res.end("hello world")
    })
    server.on("connection", () => connections++)
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
    base = new URL(`http://127.0.0.1:${(server.address() as AddressInfo).port}`)
  })

  test.afterAll(async () => {
    for (const res of held) res.destroy()
    await new Promise((resolve) => server.close(resolve))
  })

  test("an item is timed to its headers and to its last byte, read through on one kept connection", async () => {
    const agent = userAgent()
    const before = connections
    for (let i = 0; i < 3; i++) {
      const res = await send(agent, base, "GET", "/file", {
        discardBody: true,
        timeoutMs: 5_000,
      })
      expect(res.bytes).toBe(5)
      expect(res.body).toBeUndefined()
      expect(res.endAt - res.headersAt).toBeGreaterThan(150)
    }
    expect(connections - before).toBe(1)
    const full = await send(agent, base, "GET", "/", { timeoutMs: 5_000 })
    expect(full.body?.toString()).toBe("hello world")
    expect(setCookie(full, "AWSALB")).toBe("pin-value")
    agent.destroy()
  })

  test("an OutputsView user is done at the last item's headers and read at its last byte", async () => {
    const ctx: StepContext = {
      base,
      pin: "pin",
      t0: performance.now(),
      deadlineMs: 5_000,
      requests: [],
      vuRecords: [],
      abortReason: null,
      cancelled: false,
      released: Promise.resolve(true),
    }
    await dialogUser(
      ctx,
      new VirtualUser(0),
      {},
      { reproduce: "/", items: ["/file", "/file"] },
    )
    const v = ctx.vuRecords[0]
    expect((v.done_body as number) - (v.done as number)).toBeGreaterThan(0.15)
    const item = ctx.requests.find((r) => r.name !== "reproduce")
    expect((item?.full as number) - (item?.dur as number)).toBeGreaterThan(0.15)
    expect(item?.bytes).toBe(5)
  })

  test("a user accepts gzip as a browser does, reads the body decoded and counts the bytes as sent", async () => {
    const agent = userAgent()
    const full = await send(agent, base, "GET", "/json", { timeoutMs: 5_000 })
    expect(acceptEncoding).toBe("gzip, deflate")
    expect(header(full, "content-encoding")).toBe("gzip")
    expect(full.body?.equals(json)).toBe(true)
    expect(full.bytes).toBe(zlib.gzipSync(json).length)
    expect(full.bytes).toBeLessThan(json.length / 10)
    const item = await send(agent, base, "GET", "/json", {
      discardBody: true,
      timeoutMs: 5_000,
    })
    expect(item.bytes).toBe(full.bytes)
    agent.destroy()
  })

  test("a user holds at most six connections, and stopping it ends the queued requests too", async () => {
    peak = 0
    const vu = new VirtualUser(0)
    const sent = Array.from({ length: 8 }, () =>
      send(vu.agent, base, "GET", "/hold", {
        timeoutMs: 30_000,
        inflight: vu.inflight,
      }).then(
        () => "answered",
        (e: Error) => e.message,
      ),
    )
    await expect.poll(() => open, { timeout: 5_000 }).toBe(6)
    vu.stop()
    expect(await Promise.all(sent)).toEqual(Array(8).fill("stopped"))
    expect(peak).toBe(6)
  })
})

test.describe("load lane: report", () => {
  const dialog = (vus: number, extra: Partial<StepFigures>) =>
    figures({ screen: "dialog", vus, ...extra })

  test("each row says which way it went, as the 2026-11-05 comment's tables do", () => {
    const headline = (f: StepFigures) => f.done_max
    // 1 user: 4.40 s -> 0.62 s
    expect(
      improvement(
        dialog(1, { done_max: 4.404 }),
        dialog(1, { done_max: 0.619 }),
        headline,
      ),
    ).toBe("86 % shorter")
    expect(
      improvement(
        dialog(5, { stopped_by: "free" }),
        dialog(5, { done_max: 2.7 }),
        headline,
      ),
    ).toBe("stopped → no stop")
    expect(improvement(undefined, dialog(20, {}), headline)).toBe("no before")
    expect(
      improvement(
        dialog(1, { done_max: 0.565 }),
        dialog(1, { done_max: 0.527 }),
        headline,
      ),
    ).toBe("unchanged")
    expect(improvement(dialog(1, {}), undefined, headline)).toBe("not run")
    const before = dialog(10, {
      done_max: 7.9,
      probe_stop_s: { public: 10, free: 10 },
    })
    const after = dialog(10, {
      stopped_by: "free",
      probe_stop_s: { public: 5, free: 5 },
    })
    expect(improvement(before, after, headline)).toBe(
      "no stop → stopped (free probe); public threshold 10 → 5 s, free threshold 10 → 5 s",
    )
    expect(fmtS(10.189)).toBe("10.2 s")
    expect(fmtS(2.747)).toBe("2.75 s")
    // Half up on the digits as written, as the comment's 0.57 s and 3.47 s read
    expect(fmtS(0.565)).toBe("0.57 s")
    expect(fmtS(3.465)).toBe("3.47 s")
  })

  test("a step whose users did not all finish or that answered errors is not compared; a deadline is a stop", () => {
    const headline = (f: StepFigures) => f.done_max
    const before = dialog(20, { vus: 20, vu_done: 20, done_max: 10.2 })
    // 17 of 20 cut at the deadline read as 71 % shorter before
    expect(
      improvement(
        before,
        dialog(20, { vus: 20, vu_done: 3, done_max: 2.9 }),
        headline,
      ),
    ).toBe("not compared (after: 3 / 20 users done)")
    // Every item answered 423 at once
    const locked = dialog(20, {
      vus: 20,
      vu_done: 20,
      done_max: 0.05,
      requests: {
        "image:Vcorr.json": {
          count: 20,
          ok: 0,
          non_200: ["423"],
          p50: null,
          p95: null,
          max: null,
          bytes_total: 0,
        },
      },
    })
    expect(improvement(before, locked, headline)).toBe(
      "not compared (after: error answers)",
    )
    expect(
      improvement(
        before,
        dialog(20, { vus: 20, vu_done: 3, stopped_by: "deadline" }),
        headline,
      ),
    ).toBe("no stop → stopped (deadline)")
  })

  test("the report states the differences it has to be read with", () => {
    const req = (bytes: number) => ({
      count: 1,
      ok: 1,
      non_200: [],
      p50: 1,
      p95: 1,
      max: 1,
      bytes_total: bytes,
    })
    const before = [
      dialog(1, {
        requests: {
          reproduce: req(10),
          "image:Vcorr.json": req(15_000_000),
          "image:roi.json": req(5_000_000),
        },
      }),
    ]
    const after = [
      dialog(1, {
        done_body_max: 2,
        requests: {
          reproduce: req(10),
          "image:Vcorr.json": req(9_000_000),
          // Timed out: no bytes, and no sign of a smaller record
          "image:roi.json": { ...req(0), ok: 0, non_200: ["Timeout"] },
          hidden_outputs: req(50),
        },
      }),
    ]
    const notes = differences(before, after).join("\n")
    expect(notes).toContain("at most 6 connections")
    expect(notes).toContain(
      "OutputsView from Dataview, owner: the record's items that answered in both runs move",
    )
    expect(notes).toContain("15.00 MB → 9.00 MB")
    expect(notes).toContain("now also `hidden_outputs`")
    expect(notes).toContain('The "read" columns end at the last byte')
  })

  test("a changed item set or a changed PublicDataview list is not read as another record", () => {
    const req = (bytes: number) => ({
      count: 1,
      ok: 1,
      non_200: [],
      p50: 1,
      p95: 1,
      max: 1,
      bytes_total: bytes,
    })
    const before = [
      dialog(1, {
        requests: {
          reproduce: req(10),
          "image:suite2p_registration/meanImg.json": req(15_000_000),
        },
      }),
      dialog(1, {
        screen: "public",
        requests: {
          public_list: req(1_000),
          "thumbnail:input": req(100_000),
          "image:suite2p_roi/Vcorr.json": req(5_000_000),
        },
      }),
    ]
    const after = [
      dialog(1, {
        requests: {
          reproduce: req(10),
          hidden_outputs: req(50),
          "image:suite2p_file_convert/meanImg.json": req(3_600_000),
        },
      }),
      dialog(1, {
        screen: "public",
        requests: {
          public_list: req(4_000),
          "thumbnail:input": req(900_000),
          "image:suite2p_roi/Vcorr.json": req(5_000_000),
        },
      }),
    ]
    const notes = differences(before, after).join("\n")
    expect(notes).not.toContain("fixture differs")
    expect(notes).toContain(
      "no longer `image:suite2p_registration/meanImg.json`",
    )
  })

  test("items compressed in only one run are not read as another record, and the bytes are said to compare the transfer", () => {
    const req = (bytes: number, encoded = 0) => ({
      count: 1,
      ok: 1,
      non_200: [],
      p50: 1,
      p95: 1,
      max: 1,
      bytes_total: bytes,
      encoded,
    })
    const before = [
      dialog(1, {
        requests: {
          reproduce: req(10),
          "image:suite2p_roi/Vcorr.json": req(15_000_000),
          "timeseries:suite2p_roi/fluorescence": req(2_000_000),
        },
      }),
    ]
    const after = [
      dialog(1, {
        requests: {
          reproduce: req(10),
          "image:suite2p_roi/Vcorr.json": req(5_800_000, 1),
          "timeseries:suite2p_roi/fluorescence": req(2_000_000),
        },
      }),
    ]
    const notes = differences(before, after).join("\n")
    expect(notes).not.toContain("fixture differs")
    expect(notes).toContain(
      "OutputsView from Dataview, owner: 1 of the record's items came compressed in only one of the runs",
    )
    expect(notes).toContain("(17.00 MB → 7.80 MB) compare the transfer")
    expect(notes).toContain(
      "the fixture check reads the items sent the same way",
    )
    // An item sent the same way in both still tells another record
    after[0].requests["timeseries:suite2p_roi/fluorescence"] = req(3_000_000)
    expect(differences(before, after).join("\n")).toContain("fixture differs")
    // Every item compressed in only one run: nothing left to check by bytes
    const compressed = [
      dialog(1, {
        requests: {
          reproduce: req(5, 1),
          "image:suite2p_roi/Vcorr.json": req(5_800_000, 1),
        },
      }),
    ]
    expect(differences(before, compressed).join("\n")).toContain(
      "the fixture is not checked by bytes",
    )
  })

  test("log lines go to the step whose window holds them", () => {
    const hits = [
      { target: "public" as const, kind: "QueuePool", timestamp: 150 },
      {
        target: "public" as const,
        kind: "401",
        route: "GET /users/me",
        timestamp: 250,
      },
      { target: "free" as const, kind: "Traceback", timestamp: 50 },
    ]
    const byStep = bucket(hits, [
      { key: "a", from: 100, to: 200 },
      { key: "b", from: 200, to: Number.POSITIVE_INFINITY },
    ])
    expect(byStep.a.map((h) => h.kind)).toEqual(["QueuePool"])
    expect(byStep.b.map((h) => h.kind)).toEqual(["401"])
  })

  test("an access-log line gives its route and status; other lines give nothing", () => {
    const line =
      "2026-11-05 18:13:23,249 INFO:     [uvicorn.access] (pid:70) (task:1d) (client:default) send():478 - " +
      '10.2.21.166:40119 - "GET /users/me?x=1 HTTP/1.1" 200\n'
    expect(accessLine(line)).toEqual({ route: "GET /users/me", status: "200" })
    expect(
      accessLine("QueuePool limit of size 8 overflow 8 reached"),
    ).toBeNull()
  })

  test("saved steps prefer the plain folder of a count, and a report carries no hash", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "load-steps-"))
    const write = (name: string, s: StepSummary) => {
      fs.mkdirSync(path.join(dir, name))
      fs.writeFileSync(path.join(dir, name, "summary.json"), JSON.stringify(s))
    }
    write("dialog-n1", summary({ done: { p50: 1, p95: 1, max: 0.6 } }))
    write("dialog-n1-cold", summary({ done: { p50: 1, p95: 1, max: 37 } }))
    write(
      "dashboard-n1-dry",
      summary({ screen: "dashboard", free_instance_hash: "f04ebc058a3326be" }),
    )
    const steps = readSteps(dir)
    expect(steps.map((s) => s.key).sort()).toEqual([
      "dashboard-n1-dry",
      "dialog-n1",
    ])
    write("dashboard-n1-cold", summary({ screen: "dashboard" }))
    expect(() => readSteps(dir)).toThrow(/all claim dashboard-n1/)

    const run: RunReport = {
      plan: "default",
      startedAt: "s",
      endedAt: "e",
      baselineLabel: "baseline",
      overrides: [],
      endedEarly: null,
      notRun: [],
      steps: steps.map(({ key, summary: s }) => ({
        key,
        summary: s,
        figures: figuresOf(s),
        failures: failuresOf(s),
        stop: describeStop(s),
      })),
    }
    const report = renderReport(
      { measured_on: "2026-11-05", note: "", steps: [] },
      run,
    )
    expect(report).toContain("## OutputsView from Dataview, owner")
    expect(report).not.toContain("f04ebc058a3326be")
    // Logs nobody read do not read as a clean step
    expect(report).toContain(
      "| Dashboard after sign-in, 1 | — | — | — | — | — |",
    )
  })

  test("AWS's own words reach the report without instance ids or ARNs", () => {
    const run: RunReport = {
      plan: "default",
      startedAt: "s",
      endedAt: "e",
      baselineLabel: "baseline",
      overrides: [],
      endedEarly:
        "before public 1: development-optinist-asg: Launching a new EC2 instance: i-0e00e922192203715",
      notRun: [],
      steps: [],
      post: {
        targets: { free: ["healthy"], public: ["healthy", "healthy"] },
        activities: [
          {
            group: "development-optinist-public-asg",
            start: "2026-11-05T08:10:55Z",
            description: "Terminating EC2 instance: i-07f023597ebff338d",
            cause:
              "taken out of service for arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:targetgroup/x/y",
          },
        ],
        alb5xx: { free: 0, public: 0, alb: 0 },
        logTruncated: [],
      },
    }
    const report = renderReport(null, run)
    expect(report).toContain("Terminating EC2 instance: <instance>")
    expect(report).not.toMatch(/i-0e00e9|i-07f023|123456789012/)
  })

  test("each step's window shows the ALB's slowest answer, to tell a service stop from the generator's", () => {
    const s = summary({ screen: "public" })
    const f = figuresOf(s)
    f.alb_response_max_s = { free: 0.215, public: 12.258 }
    const run: RunReport = {
      plan: "parity",
      startedAt: "s",
      endedAt: "e",
      baselineLabel: "baseline",
      overrides: [],
      endedEarly: null,
      notRun: [],
      steps: [
        {
          key: "public-n5",
          summary: s,
          figures: f,
          failures: [],
          stop: describeStop(s),
          logs: [],
        },
      ],
      post: {
        targets: { free: ["healthy"], public: ["healthy"] },
        activities: [],
        alb5xx: { free: 0, public: 0, alb: 0 },
        logTruncated: [],
      },
    }
    expect(renderReport(null, run)).toContain(
      "| public-n5 | 0 | 0 | 0 | 0 (0 on `/auth/refresh`) | 0 | 0.22 s / 12.3 s |",
    )
    expect(
      renderReport(null, {
        ...run,
        settleUnhealthy: 'probe free: {"target":"free"}',
      }),
    ).toContain(
      "- The watch after the last step: a service did not settle (probe free)",
    )
  })
})

test.describe("load lane: safety gates", () => {
  const PUBLIC_TG =
    "arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:targetgroup/development-optinist-public-tg/a1"
  const OTHER_TG =
    "arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:targetgroup/development-optinist-pubdv-tg/b2"
  const rule = (
    priority: string,
    conditions: ListenerRule["Conditions"],
    tg: string,
  ): ListenerRule => ({
    Priority: priority,
    Conditions: conditions,
    Actions: [{ Type: "forward", TargetGroupArn: tg }],
  })
  const path300 = {
    Field: "path-pattern",
    PathPatternConfig: {
      Values: ["/api/public/dataview", "/api/public/dataview/*"],
    },
  }
  const header280 = {
    Field: "http-header",
    HttpHeaderConfig: {
      HttpHeaderName: "DATAVIEW_PUBLIC_REQUEST",
      Values: ["true"],
    },
  }

  test("PublicDataview's API routed to a tier the lane does not watch is refused", () => {
    const signIn = rule(
      "305",
      [{ Field: "path-pattern", Values: ["/auth/*"] }],
      OTHER_TG,
    )
    expect(
      dataviewRoutingProblems(
        [
          rule("300", [path300], PUBLIC_TG),
          rule("280", [header280], PUBLIC_TG),
          signIn,
        ],
        PUBLIC_TG,
      ),
    ).toEqual([])
    expect(
      dataviewRoutingProblems(
        [rule("300", [path300], OTHER_TG), rule("280", [header280], OTHER_TG)],
        PUBLIC_TG,
      ),
    ).toEqual([
      "rule 300 sends PublicDataview's API to development-optinist-pubdv-tg, a tier the lane does not watch",
      "rule 280 sends PublicDataview's API to development-optinist-pubdv-tg, a tier the lane does not watch",
    ])
  })

  test("a target group with no target is a problem, as an unhealthy one is", () => {
    expect(targetStateProblems({ free: ["healthy"], public: [] })).toEqual([
      "development-optinist-public-tg has no target",
    ])
    expect(
      targetStateProblems({
        free: ["healthy", "draining"],
        public: ["healthy"],
      }),
    ).toEqual(["development-optinist-tg: draining"])
  })

  test("a publish leaves its restore owed on disk until it is paid", () => {
    const results = fs.mkdtempSync(path.join(os.tmpdir(), "load-results-"))
    const run = path.join(results, "20261109T000000Z-default")
    fs.mkdirSync(run)
    expect(leftoverDebts(results)).toEqual([])
    const file = owe(run, { workspaceId: 7, uid: "6ab411e3", id: 42 })
    expect(leftoverDebts(results)).toEqual([
      { file, debt: { workspaceId: 7, uid: "6ab411e3", id: 42 } },
    ])
    fs.rmSync(results, { recursive: true })
  })

  test("development runs 23:00 -> 13:00 UTC on weekdays", () => {
    expect(devIsUp(new Date("2026-11-09T01:00:00Z"))).toBe(true)
    expect(devIsUp(new Date("2026-11-09T13:30:00Z"))).toBe(false)
    expect(devIsUp(new Date("2026-11-08T23:30:00Z"))).toBe(true)
    expect(devIsUp(new Date("2026-11-07T10:00:00Z"))).toBe(false)
  })

  test("a stalled service holds up only its own probe", async () => {
    const server = http.createServer((req, res) => {
      const delay = req.url === "/health" ? 2_500 : 0
      setTimeout(() => res.end("ok"), delay)
    })
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
    const base = new URL(
      `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    )
    const t0 = performance.now()
    const ctx = {
      requests: [],
      abortReason: null,
    } as unknown as StepContext
    const probes: ProbeRecord[] = []
    const p = prober(
      base,
      { accessToken: "a", refreshToken: "r", exToken: null },
      { cookie: "c", hash: "h" },
      t0,
    )
    await probeLoop(
      p,
      ctx,
      probes,
      { public: 5, free: 5 },
      () => performance.now() - t0 < 3_000,
    )
    p.agent.destroy()
    await new Promise((resolve) => server.close(resolve))
    expect(probes.filter((r) => r.target === "free").length).toBeGreaterThan(2)
    expect(probes.filter((r) => r.target === "public").length).toBeLessThan(3)
    expect(ctx.abortReason).toBeNull()
  })
})
