import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import vm from "node:vm"

const source = readFileSync(new URL("./hooks/useWebSocket.js", import.meta.url), "utf8")
  .replace(/^import .*$/gm, "")
  .replace("export default function", "function")

for (const event of ["job_attention", "job_complete", "job_failed", "none", "restart"]) {
  test(`late restore cannot resurrect a job after ${event}`, async () => {
    const handlers = new Map()
    const state = { activeJobs: {} }
    let resolve
    const timers = []
    const pending = new Promise(r => { resolve = r })
    const actions = new Proxy({
      startJob: id => { state.activeJobs[id] = { status: "running" } },
      removeJob: id => { delete state.activeJobs[id] },
      completeJob: id => {
        if (state.activeJobs[id]) state.activeJobs[id] = { status: "success" }
      },
    }, { get: (obj, key) => obj[key] || (() => {}) })
    const store = Object.assign(() => actions, { getState: () => state })
    vm.runInNewContext(source + "\nuseWebSocket()", {
      useEffect: fn => fn(), useRef: value => ({ current: value }),
      useAppStore: store, localizeUiMessage: text => text,
      http: { get: () => pending },
      ws: { connect() {}, on(type, fn) { handlers.set(type, fn); return () => {} } },
      setTimeout: fn => timers.push(fn), cancelAnimationFrame() {},
    })
    if (event === "restart") {
      handlers.get("job_started")({ job_id: "j1" })
      handlers.get("job_complete")({ job_id: "j1" })
      handlers.get("job_started")({ job_id: "j1" })
      timers.forEach(fn => fn())
    } else if (event !== "none") handlers.get(event)({ job_id: "j1" })
    resolve({ data: { jobs: [{ id: "j1", job_type: "tool:translate" }] } })
    await new Promise(r => setImmediate(r))
    if (["none", "restart"].includes(event)) assert.equal(state.activeJobs.j1?.status, "running")
    else assert.equal(state.activeJobs.j1, undefined)
  })
}
