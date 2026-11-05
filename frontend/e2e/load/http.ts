import * as http from "http"

// One HTTP/1.1 client per virtual user. Six sockets is what a browser opens
// per host on this plain-HTTP listener, so a user's parallel requests queue
// the way a real tab's do. The global fetch would share one pool across every
// user instead.
export const BROWSER_SOCKETS_PER_HOST = 6

export function userAgent(): http.Agent {
  return new http.Agent({
    keepAlive: true,
    maxSockets: BROWSER_SOCKETS_PER_HOST,
  })
}

export type HttpResult = {
  status: number
  headers: http.IncomingHttpHeaders
  // Only when the body was read
  body?: Buffer
  bytes: number
}

export type SendOptions = {
  headers?: Record<string, string>
  json?: unknown
  // Resolve on the response headers and drop the body unread: the server
  // builds the body before it sends them, so this is the server's time, not
  // the load generator's link. bytes is then the Content-Length it announced.
  ttfb?: boolean
  // Whole-request limit, headers and body
  timeoutMs: number
  // Holds the request while it is in flight, so its owner can end it: an
  // agent's destroy() closes open sockets, but a request still queued for a
  // socket would then get a new one
  inflight?: Set<http.ClientRequest>
}

export class RequestTimeout extends Error {
  constructor() {
    super("Timeout")
    this.name = "Timeout"
  }
}

export function send(
  agent: http.Agent,
  base: URL,
  method: string,
  path: string,
  opts: SendOptions,
): Promise<HttpResult> {
  const payload =
    opts.json === undefined ? undefined : Buffer.from(JSON.stringify(opts.json))
  const headers: Record<string, string> = { ...opts.headers }
  if (payload) headers["Content-Type"] = "application/json"
  // An explicit length: Node would otherwise send a bodiless POST chunked
  if (payload || method !== "GET") {
    headers["Content-Length"] = String(payload?.length ?? 0)
  }
  return new Promise((resolve, reject) => {
    const req = http.request({
      agent,
      host: base.hostname,
      port: base.port,
      path,
      method,
      headers,
    })
    opts.inflight?.add(req)
    const timer = setTimeout(
      () => req.destroy(new RequestTimeout()),
      opts.timeoutMs,
    )
    const settle = () => {
      clearTimeout(timer)
      opts.inflight?.delete(req)
    }
    const fail = (e: Error) => {
      settle()
      reject(e)
    }
    req.on("error", fail)
    req.on("response", (res) => {
      const status = res.statusCode ?? 0
      if (opts.ttfb) {
        settle()
        const bytes = Number(res.headers["content-length"] ?? 0)
        res.destroy()
        resolve({ status, headers: res.headers, bytes })
        return
      }
      const chunks: Buffer[] = []
      res.on("data", (chunk: Buffer) => chunks.push(chunk))
      res.on("error", fail)
      res.on("aborted", () => fail(new Error("aborted")))
      res.on("end", () => {
        settle()
        const body = Buffer.concat(chunks)
        resolve({ status, headers: res.headers, body, bytes: body.length })
      })
    })
    req.end(payload)
  })
}

export function header(res: HttpResult, name: string): string | null {
  const value = res.headers[name.toLowerCase()]
  if (value === undefined) return null
  return Array.isArray(value) ? value[0] : value
}

// The value of one cookie the response sets, without a cookie jar
export function setCookie(res: HttpResult, name: string): string | null {
  for (const line of res.headers["set-cookie"] ?? []) {
    const [pair] = line.split(";")
    const eq = pair.indexOf("=")
    if (eq > 0 && pair.slice(0, eq).trim() === name) {
      return pair.slice(eq + 1).trim()
    }
  }
  return null
}

export function jsonBody<T>(res: HttpResult): T {
  return JSON.parse((res.body ?? Buffer.alloc(0)).toString("utf-8")) as T
}

export function errorName(e: unknown): string {
  const err = e as NodeJS.ErrnoException
  return err?.code || err?.name || String(e)
}
