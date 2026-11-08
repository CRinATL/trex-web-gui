/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// vitest empties `?raw` imports of .css, so read the file, beside this one, whatever the working directory.
const css = readFileSync(resolve(import.meta.dirname, 'index.css'), 'utf8')

interface Rule { selectors: string[]; body: string }
// Every `selectors { body }` rule, comments dropped. The selectors are split on top-level commas only (`:where(a, b)` is one).
function splitTop(list: string): string[] {
  const out: string[] = []
  let depth = 0, cur = ''
  for (const ch of list) {
    if (ch === '(' || ch === '[') depth++
    if (ch === ')' || ch === ']') depth--
    if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = '' } else cur += ch
  }
  out.push(cur.trim())
  return out
}
const rules: Rule[] = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
  .map((m) => ({ selectors: splitTop(m[1].trim()), body: m[2] }))
const withSelector = (sel: string) => rules.filter((r) => r.selectors.includes(sel))
const only = (sel: string) => {
  const found = withSelector(sel)
  expect(found.length, `rules for ${sel}`).toBe(1)
  return found[0]
}
// The base rules: those whose selector list is a single :where(...) group of controls.
const baseRules = rules.filter((r) => r.selectors.some((s) => /^:where\(.*\b(button|select|textarea|input)\b/.test(s)))

describe('control tokens and base rule', () => {
  it('defines both densities', () => {
    const root = only(':root').body
    expect(root).toContain('--control-h: 32px')
    expect(root).toContain('--control-px: 12px')
    expect(root).toContain('--control-radius: 4px')
    const compact = only(':root[data-density="compact"]').body
    expect(compact).toContain('--control-h: 26px')
    expect(compact).toContain('--control-px: 8px')
  })
  it('the base rule is zero-specificity', () => {
    expect(baseRules.length).toBeGreaterThanOrEqual(5)
    for (const r of baseRules) for (const s of r.selectors) expect(s.startsWith(':where('), s).toBe(true)
  })
  it('the base rule sets the control height', () => {
    const height = baseRules.filter((r) => r.selectors.every((s) => s.startsWith(':where(') && /\bbutton\b/.test(s) && /\bselect\b/.test(s)
      && !/\btextarea\b/.test(s) && s.includes('input:not(')))   // the height rule leaves textarea to its own rows
    expect(height.length, 'the :where rule naming button, select and the text input').toBeGreaterThanOrEqual(1)
    for (const r of height) {
      expect(r.body).toContain('min-height: var(--control-h)')
      expect(r.body).toContain('padding: 0 var(--control-px)')
    }
  })
  it('no unwrapped element rule for controls', () => {
    // Drop each :where( ... ) group (balanced), then the selector may not be a bare button, select, input or textarea.
    const unwrap = (sel: string) => {
      let out = '', depth = 0, i = 0
      while (i < sel.length) {
        if (depth === 0 && sel.startsWith(':where(', i)) { depth = 1; i += 7; continue }
        const ch = sel[i++]
        if (depth > 0) { if (ch === '(') depth++; else if (ch === ')') depth--; continue }
        out += ch
      }
      return out
    }
    const bare = /^(button|select|input|textarea)(:[\w-]+(\([^()]*\))?)*$/
    for (const r of rules) for (const s of r.selectors) expect(unwrap(s).trim(), s).not.toMatch(bare)
  })
  it('the base rule spares check boxes, radios and sliders', () => {
    const text = 'input:not([type="checkbox"], [type="radio"], [type="range"], [type="color"], [type="file"])'
    for (const r of baseRules) for (const s of r.selectors) {
      const bare = s.split(text).join('')
      expect(bare, s).not.toMatch(/\binput\b/)
    }
    expect(baseRules.some((r) => r.selectors.some((s) => s.includes(text)))).toBe(true)
  })
  it('a run button is as tall as a plain button', () => {
    const run = only('button[data-run]')
    expect(run.body).toContain('--run: var(--fg)')
    expect(run.body).toContain('min-height: var(--control-h)')
    expect(run.body).not.toContain('padding: 2px 7px')
    expect(css.indexOf('button[data-run] {')).toBeLessThan(css.indexOf('button[data-run="go"] {'))
  })
  it("the packet editor's tabs opt out", () => {
    const tabs = only('.editor-tabs button')
    expect(tabs.body).toContain('min-height: 0')
    expect(tabs.body).toContain('background: none')
  })
  it('bare buttons opt out of the control height', () => {
    const bare = ['button.th-sort', '.context-menu button', '.tabs [role="tab"]', 'button.chip', 'button.field-range', 'button.unsaved-count',
      '.hex-byte', '.browse-list li button', '.legend-chip', '.breadcrumb-layer', '.layer-title', '.layer-menu', '.field-reset',
      '.setups-menu-list button', 'button.expander']
    for (const sel of bare) expect(only(sel).body, sel).toContain('min-height: 0')
    expect(only('button.info').body).toContain('min-height: 24px')
  })
  it('the Columns panel keeps each label on one line', () => {
    expect(only('.columns-panel label').body).toContain('white-space: nowrap')
  })
  it('each Columns label clears the floated legend, so the first one is not pushed beside it and out of the panel', () => {
    expect(only('.columns-panel legend').body).toContain('float: left')
    expect(only('.columns-panel label').body).toContain('clear: both')
  })
})
