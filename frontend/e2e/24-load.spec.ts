import * as fs from "fs"
import * as path from "path"

import { expect, Page, test } from "@playwright/test"

import {
  filterColumn,
  otherActiveUsers,
  progressLog,
  sqlSkipReason,
} from "./helpers"
import {
  activitiesSince,
  albFiveXx,
  betweenStepProblems,
  cpuMax,
  logHits,
  preCheckProblems,
  responseMax,
  targetStates,
} from "./load/aws"
import { captureOwner, capturePublic } from "./load/capture"
import {
  LoadConfig,
  laneSkipReason,
  othersActive,
  readConfig,
  targetProblems,
} from "./load/config"
import {
  cutoffAt,
  decideStep,
  loadPlan,
  Plan,
  stopThresholds,
  targetStep,
} from "./load/plan"
import {
  Baseline,
  bucket,
  loadBaseline,
  baselineFrom,
  readSteps,
  renderReport,
  RunReport,
  StepOutcome,
  Window,
} from "./load/report"
import {
  findRecord,
  Pin,
  PROBE_BASELINE_S,
  PROBE_TAIL_S,
  PUBLISHED,
  planName,
  RecordState,
  runStep,
  setPublished,
  signIn,
  watch,
  WatchResult,
  writeStep,
  writeWatch,
} from "./load/runner"
import {
  checkDashboardCapture,
  CaptureRow,
  dialogSpec,
  DialogSpec,
  publicSpec,
  PublicSpec,
  Session,
} from "./load/screens"
import {
  describeStop,
  failuresOf,
  figuresOf,
  Screen,
  StepFigures,
  Target,
} from "./load/summary"

// The Web UI capacity measurement as a lane: N users of one screen released
// together against development, step by step, with the before / after report
// against the committed baseline (2026-11-05). See the README's "Web UI load
// lane" for the .env keys, the safety rules and what may be posted.
//
//   RUN_DISRUPTIVE=1 RUN_LOAD_AWS=1 AWS_PROFILE=<dev profile> \
//     yarn test:e2e e2e/24-load.spec.ts -g LOAD-01 --retries 0
//
// It loads the shared environment, so it is @disruptive, opt-in twice, and
// refuses when another account was active in the last 30 minutes unless the
// window was announced. Every AWS call is a read.
//
// LOAD-02 rebuilds a report from saved steps and touches nothing remote:
//
//   LOAD_REPORT_FROM=<results dir> yarn test:e2e e2e/24-load.spec.ts -g LOAD-02

const BASELINE = path.join(__dirname, "load", "baseline-20261105.json")
// Sign-ins, captures and post-checks around the steps
const RUN_OVERHEAD_MS = 20 * 60_000
// The step's own probes and setup, beyond its deadline
const STEP_SETUP_S = 30

type Last = {
  outcome: StepOutcome
  dir: string
  pin: Pin
  session: Session
  thresholds: Record<Target, number>
}

function stamp(d: Date): string {
  return d
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d+Z$/, "Z")
}

function afterOf(w: WatchResult): StepOutcome["after"] {
  return {
    public_max: w.stats.public.max,
    free_max: w.stats.free.max,
    failed: w.stats.public.failed + w.stats.free.failed,
    unhealthy: w.unhealthy ? w.unhealthy.replace(/:.*$/, "") : null,
  }
}

// The uid filter matches across every account; the row is then found by its
// primary key
async function filterTo(page: Page, uid: string): Promise<void> {
  await filterColumn(page, "uid", uid)
  await page.keyboard.press("Escape")
}

test.describe("Web UI load lane @disruptive", () => {
  test("LOAD-01 each screen holds the plan's last step", async ({
    browser,
  }) => {
    const skip = laneSkipReason(process.env)
    test.skip(!!skip, skip)
    // A retry would re-run the load on top of a step that broke something
    expect(test.info().project.retries, "run this lane with --retries 0").toBe(
      0,
    )
    const problems = targetProblems(process.env)
    expect(problems, problems.join("; ")).toEqual([])
    const cfg: LoadConfig = readConfig(process.env)
    const plan: Plan = loadPlan(cfg.plan)
    const log = progressLog("24-load", "LOAD-01")

    // Not knowing who is on is not the same as nobody being on
    const sql = sqlSkipReason()
    test.skip(!!sql, `cannot check whether development is in use: ${sql}`)
    const others = othersActive(otherActiveUsers(), cfg.accounts)
    const overrides: string[] = []
    if (others.length) {
      test.skip(
        !cfg.windowAnnounced,
        `development is in use by ${others.join(", ")} (active within 30 minutes); ` +
          "load it only in an announced window, with LOAD_WINDOW_ANNOUNCED=1",
      )
      overrides.push(
        `LOAD_WINDOW_ANNOUNCED: ${others.length} other account(s) active in the 30 minutes before the start`,
      )
    }
    for (const [screen, max] of Object.entries(cfg.maxUsers)) {
      overrides.push(`cap for ${screen} raised to ${max} for this run`)
    }
    if (cfg.goAhead)
      overrides.push("LOAD_GO_AHEAD: steps above the default caps allowed")
    if (cfg.screens) overrides.push(`screens: ${cfg.screens.join(", ")} only`)

    const started = new Date()
    const end = cutoffAt(plan, started, cfg.windowEnd)
    test.setTimeout(
      end.getTime() -
        started.getTime() +
        plan.settle_s * 1000 +
        RUN_OVERHEAD_MS,
    )

    const pre = preCheckProblems(cfg.imageDigest)
    expect(
      pre,
      `development is not ready to be measured: ${pre.join("; ")}`,
    ).toEqual([])

    const runDir = path.join(cfg.resultsDir, `${stamp(started)}-${plan.name}`)
    fs.mkdirSync(runDir, { recursive: true })
    log.step(`results in ${runDir}`)

    const base = cfg.base
    const sessions = await signIn(base, cfg.accounts)
    // A premium sign-in through the UI would claim a premium instance
    for (const [i, s] of sessions.entries()) {
      const name = await planName(base, s)
      expect(
        name,
        `account ${i + 1} of the accounts file is not on the free plan`,
      ).toBe("Free")
    }
    const owner = cfg.accounts.indexOf(cfg.dialogOwner)
    const wanted = (screen: Screen) =>
      !cfg.screens || cfg.screens.includes(screen)
    const planned = plan.screens.filter((s) => wanted(s.screen))

    let dialog: DialogSpec | undefined
    if (
      planned.some((s) => s.screen === "dashboard" || s.screen === "dialog")
    ) {
      const record = await findRecord(base, sessions[owner], cfg.dialogRecord)
      expect(
        record,
        "LOAD_DIALOG_RECORD is not in LOAD_DIALOG_OWNER's DataView",
      ).not.toBeNull()
      log.step("capturing the top page and the output dialog")
      const rows = await captureOwner(
        browser,
        base,
        cfg.dialogOwner,
        cfg.dialogRecord.workspaceId,
        (record as RecordState).id,
      )
      fs.writeFileSync(
        path.join(runDir, "capture-owner.json"),
        JSON.stringify(rows, null, 1),
      )
      if (planned.some((s) => s.screen === "dashboard"))
        checkDashboardCapture(rows)
      if (planned.some((s) => s.screen === "dialog"))
        dialog = dialogSpec(rows, cfg.dialogRecord)
    }

    const outcomes: StepOutcome[] = []
    const notRun: RunReport["notRun"] = []
    const windows: Window[] = []
    const stepTimes = new Map<string, { from: Date; to: Date }>()
    let endedEarly: string | null = null
    let expectedHash: string | null = null
    let last: Last | null = null
    let publicRecord: RecordState | null = null
    let publishedByUs = false
    let post: RunReport["post"]
    let postError: string | null = null

    // The settle belongs to the run: after an early end the last step has
    // had its cool-down already, and that record stays the step's
    const cooldown = async (
      seconds: number,
      name: "after" | "settle",
    ): Promise<WatchResult | null> => {
      if (!last) return null
      log.step(`watching ${seconds} s after ${last.outcome.key}`)
      const w = await watch(
        base,
        last.session,
        last.pin,
        seconds,
        last.thresholds,
      )
      writeWatch(name === "after" ? last.dir : runDir, w, name)
      last.outcome.after = last.outcome.after ?? afterOf(w)
      return w
    }

    try {
      for (const screen of planned) {
        let previous: StepFigures | null = null
        let ended = false
        let publicSpecReady: PublicSpec | undefined
        for (const step of screen.steps) {
          const skipStep = (reason: string) =>
            notRun.push({ screen: screen.screen, users: step.users, reason })
          if (endedEarly) {
            skipStep("the run ended early")
            continue
          }
          if (ended) {
            skipStep("the screen ended at an earlier step")
            continue
          }
          const d = decideStep(screen, step, previous, {
            capOverride: cfg.maxUsers[screen.screen],
            goAhead: cfg.goAhead,
          })
          if (!d.run) {
            skipStep(d.reason)
            ended = d.endsScreen
            continue
          }
          if (last) {
            const w = await cooldown(plan.cooldown_s, "after")
            if (w?.unhealthy) {
              endedEarly = `the services did not settle after ${last.outcome.key} (${afterOf(w)?.unhealthy})`
              skipStep("the run ended early")
              continue
            }
            const between = betweenStepProblems(started)
            if (between.length) {
              endedEarly = `before ${screen.screen} ${step.users}: ${between.join("; ")}`
              skipStep("the run ended early")
              continue
            }
          }
          const needMs =
            (PROBE_BASELINE_S +
              plan.step_deadline_s +
              PROBE_TAIL_S +
              STEP_SETUP_S) *
            1000
          if (Date.now() + needMs > end.getTime()) {
            endedEarly = `the cut-off at ${end.toISOString()}`
            skipStep("the run ended early")
            continue
          }
          if (screen.screen === "public" && !publicSpecReady) {
            publicRecord = await findRecord(
              base,
              sessions[owner],
              cfg.publicRecord,
            )
            expect(
              publicRecord,
              "LOAD_PUBLIC_RECORD is not in LOAD_DIALOG_OWNER's DataView",
            ).not.toBeNull()
            const rec = publicRecord as RecordState
            if (rec.publishStatus !== PUBLISHED) {
              if (!cfg.publish) {
                skipStep(
                  "the public record is not published; LOAD_PUBLISH=1 publishes it for the run",
                )
                ended = true
                continue
              }
              log.step("publishing the public record for the run")
              await setPublished(base, sessions[owner], rec.id, true)
              publishedByUs = true
            }
            log.step("capturing the public page")
            const rows: CaptureRow[] = await capturePublic(
              browser,
              base,
              rec.id,
              (page) => filterTo(page, cfg.publicRecord.uid),
            )
            fs.writeFileSync(
              path.join(runDir, "capture-public.json"),
              JSON.stringify(rows, null, 1),
            )
            publicSpecReady = publicSpec(rows, cfg.publicRecord)
          }

          const key = `${screen.screen}-n${step.users}`
          log.step(`step ${key}`)
          const from = new Date()
          if (windows.length) windows[windows.length - 1].to = from.getTime()
          windows.push({
            key,
            from: from.getTime(),
            to: Number.POSITIVE_INFINITY,
          })
          const result = await runStep({
            base,
            plan,
            screen: screen.screen,
            step,
            sessions,
            owner,
            dialog,
            public: publicSpecReady,
            expectedHash,
          })
          stepTimes.set(key, { from, to: new Date() })
          expectedHash = expectedHash ?? result.pin.hash
          const dir = path.join(runDir, key)
          writeStep(dir, result)
          const thresholds = stopThresholds(plan, step)
          const outcome: StepOutcome = {
            key,
            summary: result.summary,
            figures: { ...figuresOf(result.summary), probe_stop_s: thresholds },
            failures: failuresOf(result.summary),
            stop: describeStop(result.summary),
          }
          outcomes.push(outcome)
          log.step(
            `${key}: ${outcome.failures.length ? outcome.failures.join("; ") : "passes"}`,
          )
          last = {
            outcome,
            dir,
            pin: result.pin,
            session: result.probeSession,
            thresholds,
          }
          previous = outcome.figures
        }
        if (publishedByUs && publicRecord) {
          await setPublished(base, sessions[owner], publicRecord.id, false)
          publishedByUs = false
        }
      }
      // A public instance has been replaced 2 and 6 minutes after the load
      // that broke it, so the run watches that long before it reads AWS
      await cooldown(plan.settle_s, "settle")
    } finally {
      if (publishedByUs && publicRecord) {
        await setPublished(base, sessions[owner], publicRecord.id, false).catch(
          (e) => console.error(`could not unpublish the public record: ${e}`),
        )
      }
      if (outcomes.length) {
        try {
          const ended = new Date()
          for (const o of outcomes) {
            const t = stepTimes.get(o.key) as { from: Date; to: Date }
            const to = new Date(t.to.getTime() + 60_000)
            o.figures.cpu_max_pct = {
              free: cpuMax("free", t.from, to),
              public: cpuMax("public", t.from, to),
            }
            o.figures.alb_response_max_s = {
              free: responseMax("free", t.from, to),
              public: responseMax("public", t.from, to),
            }
          }
          const { hits, truncated } = logHits(started, ended)
          const byStep = bucket(hits, windows)
          const activities = activitiesSince(started)
          for (const o of outcomes) {
            o.logs = byStep[o.key]
            const w = windows.find((x) => x.key === o.key) as Window
            o.replaced = activities
              .filter((a) => {
                const at = new Date(a.start).getTime()
                return at >= w.from && at < w.to
              })
              .map((a) => a.description)
          }
          post = {
            targets: {
              free: targetStates("free"),
              public: targetStates("public"),
            },
            activities,
            alb5xx: albFiveXx(started, ended),
            logTruncated: truncated,
          }
        } catch (e) {
          postError = (e as Error).message.split("\n")[0]
        }
      }
      const baseline: Baseline = loadBaseline(
        process.env.LOAD_BASELINE || BASELINE,
      )
      const run: RunReport = {
        plan: plan.name,
        startedAt: started.toISOString(),
        endedAt: new Date().toISOString(),
        baselineLabel: path.basename(process.env.LOAD_BASELINE || BASELINE),
        overrides,
        endedEarly:
          endedEarly ?? (postError ? `post-checks failed: ${postError}` : null),
        notRun,
        steps: outcomes,
        post,
      }
      fs.writeFileSync(
        path.join(runDir, "run.json"),
        JSON.stringify(run, null, 1),
      )
      const report = path.join(runDir, "report.md")
      fs.writeFileSync(report, renderReport(baseline, run))
      await test
        .info()
        .attach("report.md", { path: report, contentType: "text/markdown" })
      log.step(`report: ${report}`)
    }

    if (publicRecord) {
      const after = await findRecord(base, sessions[owner], cfg.publicRecord)
      expect(
        after?.publishStatus,
        "the public record's publish state was not restored",
      ).toBe((publicRecord as RecordState).publishStatus)
    }
    for (const screen of planned) {
      const target = targetStep(screen, {
        capOverride: cfg.maxUsers[screen.screen],
        goAhead: cfg.goAhead,
      })
      if (!target) continue
      const key = `${screen.screen}-n${target.users}`
      const outcome = outcomes.find((o) => o.key === key)
      const why = notRun.find(
        (n) => n.screen === screen.screen && n.users === target.users,
      )?.reason
      expect.soft(outcome, `${key} did not run: ${why}`).toBeDefined()
      if (outcome)
        expect
          .soft(outcome.failures, `${key}: ${outcome.failures.join("; ")}`)
          .toEqual([])
    }
    expect.soft(postError, `post-checks: ${postError}`).toBeNull()
    if (post) {
      const unhealthy = [...post.targets.free, ...post.targets.public].filter(
        (s) => s !== "healthy",
      )
      expect
        .soft(unhealthy, "targets not healthy at the end of the run")
        .toEqual([])
      expect
        .soft(
          post.activities.map((a) => `${a.group}: ${a.description}`),
          "the load replaced or added an instance",
        )
        .toEqual([])
    }
  })
})

test.describe("Web UI load lane, offline", () => {
  test("LOAD-02 a report from saved steps", async () => {
    const from = process.env.LOAD_REPORT_FROM
    test.skip(
      !from,
      "rebuilds a report from saved steps; set LOAD_REPORT_FROM=<results dir>",
    )
    const dir = path.resolve(from as string)
    if (process.env.LOAD_BASELINE_OUT) {
      // A new baseline: measured values only. Thresholds and CPU, which the
      // steps do not hold, are added by hand from the run's notes.
      const baseline = baselineFrom(
        dir,
        process.env.LOAD_BASELINE_ON || "",
        process.env.LOAD_BASELINE_NOTE || "",
      )
      fs.writeFileSync(
        path.resolve(process.env.LOAD_BASELINE_OUT),
        JSON.stringify(baseline, null, 1) + "\n",
      )
    }
    const saved = path.join(dir, "run.json")
    const run: RunReport = fs.existsSync(saved)
      ? JSON.parse(fs.readFileSync(saved, "utf-8"))
      : {
          plan: "(saved steps)",
          startedAt: "—",
          endedAt: "—",
          baselineLabel: "",
          overrides: [],
          endedEarly: null,
          notRun: [],
          steps: readSteps(dir).map(({ key, summary }) => ({
            key,
            summary,
            figures: figuresOf(summary),
            failures: failuresOf(summary),
            stop: describeStop(summary),
          })),
        }
    const where = process.env.LOAD_BASELINE || BASELINE
    run.baselineLabel = path.basename(where)
    const out = path.resolve(
      process.env.LOAD_REPORT_OUT || path.join(dir, "report.md"),
    )
    fs.writeFileSync(out, renderReport(loadBaseline(where), run))
    console.log(`report: ${out}`)
  })
})
