import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { test } from 'node:test'
import assert from 'node:assert/strict'

const source = readFileSync(new URL('./ContactVerification.tsx', import.meta.url), 'utf8')
const script = source.split('ContactVerification.afterDOMLoaded = `')[1].split('`')[0]

test('nested actions placement, fail-closed submission, expiry, reset and cleanup', () => {
  const events = {}
  const actions = {}
  const button = { disabled: false, closest: () => actions }
  const form = {
    action: 'https://api.verdify.ai/api/v1/public/contact', dataset: {}, isConnected: true,
    querySelector: () => button,
    insertBefore: (_slot, before) => assert.equal(before, actions),
    addEventListener: (name, handler) => { events[name] = handler },
    removeEventListener: (name) => { delete events[name] },
  }
  let options, cleaned, reset = 0, removed = 0
  const document = {
    readyState: 'complete',
    getElementById: (id) => id === 'verdify-contact-form' ? form : null,
    createElement: () => ({ setAttribute() {} }), addEventListener() {},
  }
  const window = {
    turnstile: {
      render: (_slot, opts) => { options = opts; return 'widget' },
      reset: () => { reset++ }, remove: () => { removed++ },
    },
    addCleanup: (fn) => { cleaned = fn },
  }
  runInNewContext(script, { document, window, URL, location: { href: 'https://lab.verdify.ai/start/contact' }, queueMicrotask: (fn) => fn() })
  assert.equal(button.disabled, true)
  let prevented = 0
  const event = { preventDefault: () => prevented++, stopImmediatePropagation() {} }
  events.submit(event)
  assert.equal(prevented, 1)
  assert.equal(options.action, 'contact')
  assert.equal(options['response-field-name'], 'turnstile_token')
  options.callback()
  assert.equal(button.disabled, false)
  events.submit(event)
  assert.equal(prevented, 1)
  options['expired-callback']()
  assert.equal(button.disabled, true)
  events.reset()
  assert.equal(reset, 1)
  cleaned()
  assert.equal(removed, 1)
  assert.equal(events.submit, undefined)
})
