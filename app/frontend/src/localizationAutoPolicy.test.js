import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(new URL('./components/settings/LocalizationProfilesSection.jsx', import.meta.url), 'utf8')

test('Fast creation preserves AI routing but not inherited strict automation', () => {
  const body = source.slice(source.indexOf('  const addFastProfile = () => {'), source.indexOf('  const remove = async () => {'))
  assert.ok(body.includes('const addFastProfile'))
  const base = { id: 'hq', translation_automation: 'strict', translation_model: 'saved-model', translation_routes: [{ connection_id: 'local' }] }
  const sandbox = {
    store: { presets: { fast: { id: 'fast', translation_automation: 'auto', asr_model: 'small', tts_provider: 'edge_tts' } }, profiles: [base], default_profile_id: 'hq' },
    clone: value => JSON.parse(JSON.stringify(value)), normalizeProfile: value => value,
    glossaryToText: () => '', setSelectedId: () => {}, setGlossaryText: () => {},
    setDraft: value => { sandbox.saved = value },
  }
  vm.runInNewContext(body + '\naddFastProfile()', sandbox)
  assert.equal(sandbox.saved.translation_automation, 'auto')
  assert.equal(sandbox.saved.translation_model, 'saved-model')
  assert.equal(sandbox.saved.translation_routes[0].connection_id, 'local')
  assert.equal(sandbox.saved.tts_provider, 'edge_tts')
})

test('Settings exposes an explicit automation switch and honest review warning', () => {
  assert.match(source, /value=\{draft\.translation_automation \|\| "strict"\}/)
  assert.match(source, /patch\("translation_automation", e\.target\.value\)/)
  assert.match(source, /<MenuItem value="strict">/)
  assert.match(source, /<MenuItem value="auto">/)
  assert.match(source, /không tự publish/)
})
