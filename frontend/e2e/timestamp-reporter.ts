import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestResult,
} from "@playwright/test/reporter"

/**
 * Stamps every test with a wall-clock time.
 *
 * Playwright's console reporters print durations but never a clock - `list`
 * takes only PLAYWRIGHT_LIST_PRINT_STEPS and
 * PLAYWRIGHT_LIST_PRINT_FAILURES_INLINE - and a saved run log is what a
 * sign-off is signed against. Without a timestamp it cannot be tied to
 * anything else that happened.
 *
 * UTC first, local second. Every AWS record these lanes are read against -
 * CloudWatch datapoints, ASG activities, the scheduled start/stop - is UTC, so
 * a UTC-stamped log lines up with them without conversion. The local clock is
 * the runner machine's own and is only as accurate as that machine; the zone
 * is named once in the header rather than assumed per line.
 *
 * The per-test output is a SUFFIX, not a line of its own: it is written on
 * `list`'s status line, which carries the result and the duration already and
 * ends without a newline. That is also why this reporter must be registered
 * AFTER `list` - see playwright.config.ts. On a terminal too narrow to hold
 * both, the suffix wraps onto its own row rather than corrupting anything.
 */

const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone

function stamp(): string {
  const now = new Date()
  return `${now.toISOString().slice(0, 19)}Z | ${now.toTimeString().slice(0, 8)}`
}

function elapsed(ms: number): string {
  return ms >= 60_000
    ? `${(ms / 60_000).toFixed(1)}m`
    : `${(ms / 1000).toFixed(1)}s`
}

export default class TimestampReporter implements Reporter {
  private started = Date.now()

  onBegin(_config: FullConfig, suite: Suite) {
    this.started = Date.now()
    console.log(
      `[${stamp()}] RUN START ${suite.allTests().length} test(s); ` +
        `local times are ${LOCAL_TZ}`,
    )
  }

  onTestEnd(_test: unknown, result: TestResult) {
    // Only the clock: everything else on this line is already `list`'s.
    // The retry index is not, and two attempts read as duplicates without it.
    const retry = result.retry ? ` retry ${result.retry}` : ""
    console.log(`  @ ${stamp()}${retry}`)
  }

  onEnd(result: FullResult) {
    console.log(
      `[${stamp()}] RUN END ${result.status} after ` +
        `${elapsed(Date.now() - this.started)}`,
    )
  }
}
