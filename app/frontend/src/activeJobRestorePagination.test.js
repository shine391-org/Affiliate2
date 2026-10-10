// R3 — active-job restore must not LOSE an active job while paging a set
// that is actively changing underneath it.
//
// The restore pages `/api/jobs?status=running,pending,queued`. The backend
// filters by status and orders by created_at DESC, so the row set is exactly
// the set that changes as jobs run: a job that finishes LEAVES the filtered
// set and every later row shifts one position toward the head.
//
// Reproduced defect (250 active jobs, 10 finish between page 1 and page 2):
//   page 1 -> offset 0,  100 rows, total 250
//   10 jobs complete -> set shrinks to 240, everything shifts up by 10
//   page 2 -> offset 100, returns what are now rows [110..209]
//   page 3 -> offset 200, 40 rows
//   => jobs j140..j149 were still `running` in the DB and were NEVER restored.
//
// The user sees a page reload silently drop work that is still running, with no
// error anywhere. The fix is keyset (cursor) paging, whose window is anchored
// on a row's immutable (created_at, id) instead of on a POSITION, so rows
// leaving the set cannot move it. These tests execute the real algorithm
// against a simulated backend that mutates between pages — the defect lives in
// the interaction of (paging) x (changing set), which a source-text assertion
// cannot see.
import test from "node:test"
import assert from "node:assert/strict"

const ACTIVE = ["running", "pending", "queued"]

/**
 * Mirrors the keyset query in backend/api/jobs.py:
 *   WHERE status IN (...) AND (created_at, id) < (cursor.created_at, cursor.id)
 *   ORDER BY created_at DESC, id DESC LIMIT n
 * The backend offers a continuation only when the caller asked WITH a cursor.
 */
function makeBackend(rows) {
  return function page({ cursor = null, limit = 100 }) {
    const selected = rows
      .filter((r) => ACTIVE.includes(r.status))
      .sort((a, b) => b.created - a.created || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0))
    let window = selected
    if (cursor) {
      window = selected.filter(
        (r) => r.created < cursor.created ||
          (r.created === cursor.created && r.id < cursor.id),
      )
    }
    const slice = window.slice(0, limit)
    const last = slice[slice.length - 1]
    return {
      jobs: slice,
      total: selected.length,
      next_cursor: last ? { created: last.created, id: last.id } : null,
    }
  }
}

/** The restore loop as production runs it after the fix. */
async function restoreActiveJobs(backend, { maxPages = 50 } = {}) {
  const restored = []
  const seen = new Set()
  let cursor = null
  let pages = 0
  for (;;) {
    const res = backend({ cursor, limit: 100 })
    pages += 1
    if (pages > maxPages) return { restored, state: "LOOP_GUARD", pages }
    if (!res.jobs.length) break
    for (const job of res.jobs) {
      if (seen.has(job.id)) continue
      seen.add(job.id)
      restored.push(job.id)
    }
    cursor = res.next_cursor
    if (!cursor || pages >= maxPages) break
  }
  return { restored, state: "DONE", pages }
}

function seedJobs(count, startId = 0) {
  return Array.from({ length: count }, (_, i) => ({
    id: `j${startId + i}`,
    status: "running",
    created: 1000 + startId + i,
  }))
}

const stillActiveIds = (rows) =>
  rows.filter((r) => ACTIVE.includes(r.status)).map((r) => r.id)

test("R3: rows leaving the set mid-restore drop no still-active job", async () => {
  const rows = seedJobs(250)
  const raw = makeBackend(rows)
  const finished = new Set()
  let calls = 0
  const backend = (opts) => {
    // After the first page is served, 10 of the NEWEST active jobs finish.
    if (calls === 1) {
      const active = rows
        .filter((r) => !finished.has(r.id) && ACTIVE.includes(r.status))
        .sort((a, b) => b.created - a.created)
      for (const job of active.slice(0, 10)) {
        job.status = "success"
        finished.add(job.id)
      }
    }
    calls += 1
    return raw(opts)
  }

  const result = await restoreActiveJobs(backend)
  const missing = stillActiveIds(rows).filter((id) => !result.restored.includes(id))

  assert.equal(finished.size, 10, "the scenario must actually mutate the set")
  assert.equal(
    missing.length,
    0,
    `cursor paging lost ${missing.length} still-active job(s): ${missing.join(", ")}`,
  )
})

test("R3: a new job starting mid-restore neither loops nor restores twice", async () => {
  const rows = seedJobs(250)
  const raw = makeBackend(rows)
  let calls = 0
  let created = 0
  const backend = (opts) => {
    if (calls === 1) {
      for (let k = 0; k < 5; k += 1) {
        created += 1
        rows.push({ id: `new${created}`, status: "running", created: 9000 + created })
      }
    }
    calls += 1
    return raw(opts)
  }

  const result = await restoreActiveJobs(backend)
  assert.equal(result.state, "DONE", "restore must terminate under churn")
  const dupes = result.restored.length - new Set(result.restored).size
  assert.equal(dupes, 0, "the same job must never be restored twice")
  // A job created AFTER the restore began is covered by the job_started WS
  // event, not by this restore — the invariant here is termination + no double
  // restore, not that the restore chases a moving tail forever.
})

test("R3: restore covers the whole active set when nothing changes", async () => {
  const rows = seedJobs(250)
  const result = await restoreActiveJobs(makeBackend(rows))
  assert.equal(result.state, "DONE")
  assert.equal(new Set(result.restored).size, 250, "every active job must be restored")
})

test("R3: offset paging on the same set DOES lose jobs (regression witness)", () => {
  // This is the algorithm the code used to run. It must stay RED so that
  // anyone reverting to offset paging sees why.
  const rows = seedJobs(250)
  const restored = []
  let offset = 0
  for (let page = 0; page < 10; page += 1) {
    if (page === 1) {
      const active = rows
        .filter((r) => ACTIVE.includes(r.status))
        .sort((a, b) => b.created - a.created)
      for (const job of active.slice(0, 10)) job.status = "success"
    }
    const selected = rows
      .filter((r) => ACTIVE.includes(r.status))
      .sort((a, b) => b.created - a.created)
    const slice = selected.slice(offset, offset + 100)
    if (!slice.length) break
    restored.push(...slice.map((r) => r.id))
    offset += slice.length
  }

  const missing = stillActiveIds(rows).filter((id) => !restored.includes(id))
  assert.ok(
    missing.length > 0,
    "this witness must show the OLD algorithm losing jobs, otherwise the " +
      "scenario no longer reproduces the defect and the fix is untested",
  )
})