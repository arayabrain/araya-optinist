import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
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
 * Interleaves cleanly when the output is redirected to a file, which is how
 * evidence runs are captured. In an interactive TTY the `list` reporter
 * repaints its own lines, so the two outputs compete.
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

  onTestBegin(test: TestCase) {
    // Printed as well as the end line: a row that polls for half an hour is
    // silent until it finishes, and "when did this start" is the question a
    // long run raises first.
    console.log(`[${stamp()}] START     ${test.title}`)
  }

  onTestEnd(test: TestCase, result: TestResult) {
    // Without the retry index two attempts of the same row read as duplicates.
    const retry = result.retry ? ` (retry ${result.retry})` : ""
    console.log(
      `[${stamp()}] ${result.status.toUpperCase().padEnd(9)} ${test.title} ` +
        `${elapsed(result.duration)}${retry}`,
    )
  }

  onEnd(result: FullResult) {
    console.log(
      `[${stamp()}] RUN END ${result.status} after ` +
        `${elapsed(Date.now() - this.started)}`,
    )
  }
}
