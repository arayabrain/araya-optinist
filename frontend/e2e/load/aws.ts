import { awsJson, FREE_LOG_GROUP, PUBLIC_LOG_GROUP } from "../helpers"

import { LogHit } from "./report"
import { Target } from "./summary"

// Read-only AWS checks around a load run: before it, between its steps and
// after it. Every call is a describe, a list or a metric read on development's
// resources; nothing here changes AWS.

const CLUSTER = "development-optinist-cloud-cluster"
export const SERVICES: Record<Target, string> = {
  free: "development-optinist-cloud-service",
  public: "development-public-optinist-cloud-service",
}
const TARGET_GROUPS: Record<Target, string> = {
  free: "development-optinist-tg",
  public: "development-optinist-public-tg",
}
const ASGS: Record<Target, string> = {
  free: "development-optinist-asg",
  public: "development-optinist-public-asg",
}
const LOG_GROUPS: Record<Target, string> = {
  free: FREE_LOG_GROUP,
  public: PUBLIC_LOG_GROUP,
}
// Budget, not health: it fires on monthly spend and drives nothing
const IGNORED_ALARMS = ["development-monthly-cost-high"]
const TARGETS: Target[] = ["free", "public"]

type TargetGroup = { arn: string; lbArn: string }

const groups = new Map<Target, TargetGroup>()
function targetGroup(target: Target): TargetGroup {
  const known = groups.get(target)
  if (known) return known
  const tg = awsJson<{ arn: string; lb: string }>(
    `elbv2 describe-target-groups --names ${TARGET_GROUPS[target]} ` +
      `--query 'TargetGroups[0].{arn:TargetGroupArn,lb:LoadBalancerArns[0]}'`,
  )
  const found = { arn: tg.arn, lbArn: tg.lb }
  groups.set(target, found)
  return found
}

export function targetStates(target: Target): string[] {
  return awsJson<string[]>(
    `elbv2 describe-target-health --target-group-arn ${targetGroup(target).arn} ` +
      `--query 'TargetHealthDescriptions[].TargetHealth.State'`,
  )
}

function serviceState(target: Target): {
  desired: number
  running: number
  deployments: number
} {
  return awsJson(
    `ecs describe-services --cluster ${CLUSTER} --services ${SERVICES[target]} ` +
      `--query 'services[0].{desired:desiredCount,running:runningCount,deployments:length(deployments)}'`,
  )
}

function taskDigests(target: Target): string[] {
  const arns = awsJson<string[]>(
    `ecs list-tasks --cluster ${CLUSTER} --service-name ${SERVICES[target]} ` +
      `--desired-status RUNNING --query taskArns`,
  )
  if (!arns.length) return []
  return awsJson<string[]>(
    `ecs describe-tasks --cluster ${CLUSTER} --tasks ${arns.join(" ")} ` +
      `--query 'tasks[].containers[].imageDigest'`,
  )
}

function asgSize(target: Target): { min: number; desired: number } {
  return awsJson(
    `autoscaling describe-auto-scaling-groups --auto-scaling-group-names ${ASGS[target]} ` +
      `--query 'AutoScalingGroups[0].{min:MinSize,desired:DesiredCapacity}'`,
  )
}

function alarmsInAlarm(): string[] {
  return awsJson<string[]>(
    `cloudwatch describe-alarms --alarm-name-prefix development- --state-value ALARM ` +
      `--query 'MetricAlarms[].AlarmName'`,
  ).filter((name) => !IGNORED_ALARMS.includes(name))
}

function unhealthyTargets(target: Target): string | null {
  const states = targetStates(target)
  const bad = states.filter((s) => s !== "healthy")
  if (!states.length) return `${TARGET_GROUPS[target]} has no target`
  return bad.length ? `${TARGET_GROUPS[target]}: ${bad.join(", ")}` : null
}

export type Activity = {
  group: string
  start: string
  description: string
  cause: string
}

export function activitiesSince(since: Date): Activity[] {
  return TARGETS.flatMap((target) =>
    awsJson<{ start: string; description: string; cause: string }[]>(
      `autoscaling describe-scaling-activities --auto-scaling-group-name ${ASGS[target]} ` +
        `--max-items 20 --query 'Activities[].{start:StartTime,description:Description,cause:Cause}'`,
    )
      .filter((a) => new Date(a.start) >= since)
      .map((a) => ({ group: ASGS[target], ...a })),
  )
}

// Refuse to start unless the services under load run the image the requester
// named, at rest: development often runs a teammate's test image, and a
// scaled-out or rolling service would not measure one instance.
export function preCheckProblems(expectedDigest: string): string[] {
  const problems: string[] = []
  for (const target of TARGETS) {
    const svc = serviceState(target)
    if (svc.running !== svc.desired) {
      problems.push(
        `${target} service: ${svc.running} of ${svc.desired} tasks running`,
      )
    }
    if (svc.deployments !== 1)
      problems.push(`${target} service: a deployment is rolling`)
    const digests = taskDigests(target)
    const other = digests.filter((d) => d !== expectedDigest)
    if (!digests.length) problems.push(`${target} service: no running task`)
    if (other.length) {
      problems.push(
        `${target} service: ${other.length} of ${digests.length} task(s) run ` +
          `${other[0].slice(0, 19)}…, not LOAD_IMAGE_DIGEST`,
      )
    }
    const bad = unhealthyTargets(target)
    if (bad) problems.push(bad)
  }
  const free = asgSize("free")
  if (free.desired !== free.min) {
    problems.push(
      `the free tier runs ${free.desired} instances, not its idle ${free.min}`,
    )
  }
  const freeService = serviceState("free")
  if (freeService.desired !== free.min) {
    problems.push(
      `the free service wants ${freeService.desired} tasks, not its idle ${free.min}`,
    )
  }
  const alarms = alarmsInAlarm()
  if (alarms.length) problems.push(`in ALARM: ${alarms.join(", ")}`)
  return problems
}

// Before each step: a target the last step left unhealthy, or an instance
// replaced or added since the run began, ends the run
export function betweenStepProblems(since: Date): string[] {
  const problems = TARGETS.map(unhealthyTargets).filter((p): p is string => !!p)
  for (const a of activitiesSince(since))
    problems.push(`${a.group}: ${a.description}`)
  return problems
}

// ---------------------------------------------------------------------------
// After the run
// ---------------------------------------------------------------------------

function iso(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z")
}

function metric(
  namespace: string,
  name: string,
  dimensions: [string, string][],
  statistic: "Maximum" | "Sum",
  from: Date,
  to: Date,
): number[] {
  const dims = dimensions.map(([k, v]) => `Name=${k},Value=${v}`).join(" ")
  return awsJson<number[]>(
    `cloudwatch get-metric-statistics --namespace ${namespace} --metric-name ${name} ` +
      `--dimensions ${dims} --start-time ${iso(from)} --end-time ${iso(to)} ` +
      `--period 60 --statistics ${statistic} --query 'Datapoints[].${statistic}'`,
  )
}

// The service's 1-minute CPU maximum over a window, % of the task's units
export function cpuMax(target: Target, from: Date, to: Date): number | null {
  const start = new Date(Math.floor(from.getTime() / 60_000) * 60_000)
  const values = metric(
    "AWS/ECS",
    "CPUUtilization",
    [
      ["ClusterName", CLUSTER],
      ["ServiceName", SERVICES[target]],
    ],
    "Maximum",
    start,
    to,
  )
  return values.length ? Math.round(Math.max(...values)) : null
}

// ALB metrics name a target group and a load balancer by their ARNs' tails
function dimensionOf(
  arn: string,
  kind: "targetgroup" | "loadbalancer",
): string {
  const tail = arn.split(":").pop() as string
  return kind === "loadbalancer" ? tail.replace(/^loadbalancer\//, "") : tail
}

// The target group's slowest answer over a window (1-minute maxima), for every
// client. A probe that waited past its threshold while this stayed short was
// held up on the load generator's side, not by the service.
export function responseMax(
  target: Target,
  from: Date,
  to: Date,
): number | null {
  const tg = targetGroup(target)
  const values = metric(
    "AWS/ApplicationELB",
    "TargetResponseTime",
    [
      ["TargetGroup", dimensionOf(tg.arn, "targetgroup")],
      ["LoadBalancer", dimensionOf(tg.lbArn, "loadbalancer")],
    ],
    "Maximum",
    new Date(Math.floor(from.getTime() / 60_000) * 60_000),
    to,
  )
  return values.length ? Math.max(...values) : null
}

// 5xx the targets answered, and 5xx the ALB answered itself (502 / 503 / 504
// when no target could), for every client, not only this run's
export function albFiveXx(
  from: Date,
  to: Date,
): Record<Target | "alb", number> {
  const out = {} as Record<Target | "alb", number>
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0)
  for (const target of TARGETS) {
    const tg = targetGroup(target)
    out[target] = sum(
      metric(
        "AWS/ApplicationELB",
        "HTTPCode_Target_5XX_Count",
        [
          ["TargetGroup", dimensionOf(tg.arn, "targetgroup")],
          ["LoadBalancer", dimensionOf(tg.lbArn, "loadbalancer")],
        ],
        "Sum",
        from,
        to,
      ),
    )
  }
  out.alb = sum(
    metric(
      "AWS/ApplicationELB",
      "HTTPCode_ELB_5XX_Count",
      [
        [
          "LoadBalancer",
          dimensionOf(targetGroup("free").lbArn, "loadbalancer"),
        ],
      ],
      "Sum",
      from,
      to,
    ),
  )
  return out
}

export type LogEvent = { timestamp: number; message: string }

const LOG_PAGE_LIMIT = 10_000

function logEvents(
  target: Target,
  pattern: string,
  from: Date,
  to: Date,
): LogEvent[] {
  return awsJson<LogEvent[]>(
    `logs filter-log-events --log-group-name ${LOG_GROUPS[target]} ` +
      `--start-time ${from.getTime()} --end-time ${to.getTime()} ` +
      `--filter-pattern '${pattern}' --max-items ${LOG_PAGE_LIMIT} ` +
      `--query 'events[].{timestamp:timestamp,message:message}'`,
  )
}

// What broke the Dashboard on 2026-11-02 left these behind: the DB pool timing
// out in the auth dependency, tracebacks, a deadlock in the activity update
export const LOG_TERMS = ["QueuePool", "Traceback", "Deadlock"]
// 401 is how a pool timeout reached users (and signed them out); 5xx the rest
export const ACCESS_STATUSES = ["401", "500", "502", "503", "504"]

const ACCESS_LINE = /"(\w+) (\S+) HTTP\/[\d.]+" (\d{3})\s*$/

// An access-log line's method, path and status, or null for any other line
export function accessLine(
  message: string,
): { route: string; status: string } | null {
  const m = message.match(ACCESS_LINE)
  return m ? { route: `${m[1]} ${m[2].split("?")[0]}`, status: m[3] } : null
}

// Every hit in the run's window. A status term also matches inside client
// ports ("10.2.3.4:40123"), so the line's own status decides.
export function logHits(
  from: Date,
  to: Date,
): { hits: LogHit[]; truncated: string[] } {
  const hits: LogHit[] = []
  const truncated: string[] = []
  for (const target of TARGETS) {
    for (const term of LOG_TERMS) {
      const events = logEvents(target, `"${term}"`, from, to)
      if (events.length >= LOG_PAGE_LIMIT) truncated.push(`${target} ${term}`)
      for (const e of events)
        hits.push({ target, kind: term, timestamp: e.timestamp })
    }
    for (const status of ACCESS_STATUSES) {
      const events = logEvents(target, `"uvicorn.access" "${status}"`, from, to)
      if (events.length >= LOG_PAGE_LIMIT) truncated.push(`${target} ${status}`)
      for (const e of events) {
        const line = accessLine(e.message)
        if (line?.status === status) {
          hits.push({
            target,
            kind: status,
            route: line.route,
            timestamp: e.timestamp,
          })
        }
      }
    }
  }
  return { hits, truncated }
}
