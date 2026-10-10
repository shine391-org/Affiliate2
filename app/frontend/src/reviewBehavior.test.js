// SPDX-License-Identifier: AGPL-3.0-only
// Mounted React behavior with controlled HTTP/UI primitives, not browser QA.
import assert from "node:assert/strict"
import test from "node:test"
import { createRequire } from "node:module"
import { pathToFileURL } from "node:url"
import { build } from "esbuild"
import React from "react"
import TestRenderer, { act } from "react-test-renderer"

const require = createRequire(import.meta.url)
let serial = 0
async function load(entry, http, state = {}) {
  globalThis.__reviewHttp = http
  globalThis.__reviewStore = { showSnackbar() {}, startJob() {}, ...state }
  const components = ["Alert", "Box", "Button", "Checkbox", "Chip", "FormControl", "FormControlLabel", "InputLabel", "MenuItem", "Select", "Stack", "Typography"]
  const output = await build({ entryPoints: [new URL(entry, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")],
    bundle: true, write: false, platform: "node", format: "esm", jsx: "automatic",
    plugins: [{ name: "controlled-imports", setup(build) {
      build.onResolve({ filter: /^react(?:\/.*)?$/ }, ({ path }) => ({ path: pathToFileURL(require.resolve(path)).href, external: true }))
      build.onResolve({ filter: /(?:api\/http|store\/appStore|utils\/localizeUiMessage|hooks\/useDocumentTitle|components\/PageHero)$/ }, ({ path }) => ({ path, namespace: "qa" }))
      build.onResolve({ filter: /^@mui\// }, ({ path }) => ({ path, namespace: "qa" }))
      build.onResolve({ filter: /^react-router-dom$/ }, ({ path }) => ({ path, namespace: "qa" }))
      build.onLoad({ filter: /.*/, namespace: "qa" }, ({ path }) => {
        if (path.endsWith("api/http")) return { contents: "export default globalThis.__reviewHttp" }
        if (path.endsWith("store/appStore")) return { contents: "export default selector => selector(globalThis.__reviewStore)" }
        if (path.endsWith("localizeUiMessage")) return { contents: "export const localizeUiMessage = x => x" }
        if (path.endsWith("useDocumentTitle")) return { contents: "export default () => {}" }
        if (path === "@mui/material") return { contents: `import React from 'react'; ${components.map(name => `export const ${name}=props=>React.createElement('${name}',props);`).join("")}` }
        if (path === "react-router-dom") return { contents: "import React from 'react'; export const Link=props=>React.createElement('RouterLink',props)" }
        return { contents: "import React from 'react'; export default props=>React.createElement('Presentation',props)" }
      })
    } }],
  })
  return (await import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text + `\n//${serial++}`).toString("base64")}`)).default
}
const deferred = () => {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const defaults = { media: "all", origins: [], producer: "all", q: "", sort: "newest", view: "tree", page: 1, pageSize: 48 }
const item = i => ({ key: `job:${i}`, id: String(i), title: `Video ${i}` })

for (const [families, loners] of [[0, 0], [0, 1], [0, 48], [0, 49], [0, 100], [1, 100], [100, 1], [49, 100]]) {
  test(`tree paging: families=${families}, loners=${loners}`, async () => {
    const http = { get: async (url, { params } = {}) => ({ data: url.endsWith("families") ? {
      family_total: families, loner_total: loners,
      families: Array.from({ length: families }, (_, i) => ({ root: item(i), children: [] })).slice(params.offset, params.offset + params.limit),
      loners: Array.from({ length: loners }, (_, i) => item(i)).slice(params.offset, params.offset + params.limit),
    } : { items: [], total: families + loners } }) }
    const useIndex = await load("./hooks/useLibraryIndex.js", http)
    let current, renderer
    function Probe(props) { current = useIndex(props); return null }
    try {
      await act(async () => { renderer = TestRenderer.create(React.createElement(Probe, defaults)) })
      const pages = Math.max(1, Math.ceil(Math.max(families, loners) / 48))
      assert.equal(current.pageCount, pages)
      for (let page = 1; page <= pages; page++) {
        await act(async () => renderer.update(React.createElement(Probe, { ...defaults, page })))
        assert.equal(current.loners.length, Math.min(48, Math.max(0, loners - (page - 1) * 48)))
        assert.equal(current.families.length, Math.min(48, Math.max(0, families - (page - 1) * 48)))
      }
    } finally { if (renderer) act(() => renderer.unmount()) }
  })
}

test("tree ignores out-of-order replies and refresh updates final-page count", async () => {
  const requests = []
  const http = { get: (url, options) => {
    if (!url.endsWith("families")) return Promise.resolve({ data: {} })
    const pending = deferred(); requests.push({ ...pending, options }); return pending.promise
  } }
  const useIndex = await load("./hooks/useLibraryIndex.js", http)
  let current, renderer
  function Probe(props) { current = useIndex(props); return null }
  try {
    await act(async () => { renderer = TestRenderer.create(React.createElement(Probe, defaults)) })
    await act(async () => renderer.update(React.createElement(Probe, { ...defaults, media: "video", page: 3 })))
    await act(async () => requests[1].resolve({ data: { family_total: 0, loner_total: 100, loners: [item(96)], matched_keys: ["job:96"] } }))
    await act(async () => requests[0].resolve({ data: { family_total: 99, loner_total: 1, loners: [item(0)] } }))
    assert.equal(current.pageCount, 3)
    assert.equal(current.loners[0].key, "job:96")
    assert.deepEqual([...current.matchedKeys], ["job:96"])
    await act(async () => current.refresh())
    await act(async () => requests[2].resolve({ data: { family_total: 0, loner_total: 96, loners: [] } }))
    assert.equal(current.pageCount, 2)
    await act(async () => renderer.update(React.createElement(Probe, { ...defaults, media: "audio", page: 1 })))
    await act(async () => requests[3].resolve({ data: { family_total: 0, loner_total: 1, loners: [item(1)] } }))
    assert.equal(current.pageCount, 1)
    assert.equal(current.loners[0].key, "job:1")
  } finally { if (renderer) act(() => renderer.unmount()) }
})

for (const failure of [new Error("offline"), { response: { status: 500, data: { detail: "settings failed" } } }]) {
  test(`batch recovers profile load: ${failure.message || failure.response.status}`, async () => {
    let reads = 0
    const started = [], submitted = []
    const profile = { id: "fast", name: "Fast QA", audio_mode: "fast_duck" }
    const http = {
      get: async () => { if (++reads === 1) throw failure; return { data: { profiles: [profile], default_profile_id: "fast" } } },
      post: async (url, form) => { submitted.push([url, form]); return { data: { jobs: [{ job_id: "job/new", filename: "clip.mp4" }] } } },
    }
    const Batch = await load("./pages/LocalizationBatch.jsx", http, { startJob: (...args) => started.push(args) })
    let renderer
    try {
      await act(async () => { renderer = TestRenderer.create(React.createElement(Batch, { embedded: true })) })
      const buttons = () => renderer.root.findAllByType("Button")
      assert.ok(renderer.root.findAllByType("Alert").some(node => node.props.severity === "error"))
      const retry = buttons().find(node => node.props.children === "Tải lại hồ sơ")
      assert.ok(retry)
      await act(async () => retry.props.onClick())
      assert.equal(renderer.root.findAllByType("Alert").filter(node => node.props.severity === "error").length, 0)
      const profileSelect = () => renderer.root.findAllByType("Select").find(node => node.props.label === "Hồ sơ")
      assert.equal(profileSelect().props.value, "fast")
      await act(async () => buttons().find(node => node.props.children === "Tải lại hồ sơ").props.onClick())
      assert.equal(profileSelect().props.value, "fast")
      await act(async () => renderer.root.findByType("input").props.onChange({ target: { files: [new Blob(["video"])], value: "" } }))
      const run = buttons().find(node => node.props.children === "Chạy hàng loạt")
      assert.equal(run.props.disabled, false)
      const repair = renderer.root.findByType("FormControlLabel").props.control
      assert.equal(repair.props.checked, false)
      await act(async () => repair.props.onChange({ target: { checked: true } }))
      await act(async () => buttons().find(node => node.props.children === "Chạy hàng loạt").props.onClick())
      assert.equal(submitted[0][1].get("auto_repair"), "true")
      assert.equal(submitted[0][1].get("profile_id"), "fast")
      assert.equal(started[0][0], "job/new")
      assert.match(started[0][2], /Fast/)
      const link = buttons().find(node => node.props.to)
      assert.ok(link)
      assert.equal(link.props.to, "/studio/localize?job=job%2Fnew")
    } finally { if (renderer) act(() => renderer.unmount()) }
  })
}
