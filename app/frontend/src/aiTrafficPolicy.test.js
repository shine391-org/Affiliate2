import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const source = readFileSync(new URL("./components/settings/AIProviderSection.jsx", import.meta.url), "utf8")

test("AI policy is opt-in and retains the conservative bootstrap values", () => {
  assert.match(source, /\[trafficPolicyEnabled, setTrafficPolicyEnabled\] = useState\(false\)/)
  assert.match(source, /\[minGap, setMinGap\] = useState\(10\)/)
  assert.match(source, /\[cooldownInitial, setCooldownInitial\] = useState\(30\)/)
  assert.match(source, /\[cooldownMax, setCooldownMax\] = useState\(300\)/)
  assert.ok(source.includes("NOT an upstream quota guarantee"))
})

test("saved enabled policy controls remain editable after reload", () => {
  assert.match(source, /<Collapse in=\{trafficPolicyEnabled\}/)
  assert.ok(source.includes("cfg.traffic_policy"))
})

test("unsaved probe and save both send the current explicit policy", () => {
  const probe = source.split("const probePayload = () =>")[1].split("const save =")[0]
  assert.match(probe, /traffic_policy: trafficPolicy\(\)/)
  const save = source.split("const save =")[1].split("const activate =")[0]
  assert.match(save, /traffic_policy: trafficPolicy\(\)/)
  assert.match(source, /const trafficPolicy = \(\) => trafficPolicyEnabled \? \{/)
  assert.match(source, /resource_group: resourceGroup.trim\(\) \|\| null/)
})
