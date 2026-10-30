/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { contrastRatio } from './wcag'


// vitest empties `?raw` imports of .css, so read the file, beside this one (jsdom makes import.meta.url an http URL, so use dirname) whatever the working directory.
const css = readFileSync(resolve(import.meta.dirname, 'index.css'), 'utf8')

// The declarations of the block opened by `selector {`, up to its first `}` (no block here nests another).
function block(selector: string): Record<string, string> {
  const at = css.indexOf(selector + ' {')
  if (at < 0) throw new Error(`no block ${selector}`)
  const open = css.indexOf('{', at)
  const close = css.indexOf('}', open)
  const out: Record<string, string> = {}
  for (const m of css.slice(open + 1, close).matchAll(/(--[\w-]+|color-scheme)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim()
  return out
}

const BLOCKS: Record<string, Record<string, string>> = {
  light: block(':root'),
  'system-dark': block(':root:not([data-theme])'),
  dark: block(':root[data-theme="dark"]'),
  contrast: block(':root[data-theme="contrast"]'),
}
const TEXT = ['--fg', '--muted', '--ok', '--bad', '--warn', '--accent']
const SERIES = Array.from({ length: 8 }, (_, i) => `--series-${i + 1}`)
const ALL = ['--bg', '--fg', '--muted', '--border', '--panel', '--accent', '--ok', '--bad', '--warn', ...SERIES, '--chart-grid', 'color-scheme']

function check(scheme: string, tokens: string[], bar: number) {
  const b = BLOCKS[scheme]
  for (const t of tokens) for (const bg of ['--bg', '--panel']) {
    const r = contrastRatio(b[t], b[bg])
    expect(r, `${scheme} ${t} ${b[t]} on ${bg} ${b[bg]} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(bar)
  }
}

describe('theme contrast', () => {
  it('every block defines all 18 custom properties and color-scheme', () => {
    for (const [name, b] of Object.entries(BLOCKS)) for (const k of ALL) expect(b[k], `${name} ${k}`).toBeTruthy()
  })
  it('every text token >= 4.5 against bg and panel in light, system-dark and dark', () => {
    for (const s of ['light', 'system-dark', 'dark']) check(s, TEXT, 4.5)
  })
  it('every text token >= 7 in contrast', () => { check('contrast', TEXT, 7) })
  it('every series token >= 3 against bg and panel in light, system-dark and dark', () => {
    for (const s of ['light', 'system-dark', 'dark']) check(s, SERIES, 3)
  })
  it('every series token >= 4.5 in contrast', () => { check('contrast', SERIES, 4.5) })
})

// The unsaved-edit marker (RC1-31): a teal dashed border on a pale teal fill, and an `edited` tag in the same teal.
describe('edited marker contrast', () => {
  const ratio = (b: Record<string, string>, fg: string, bg: string) => contrastRatio(b[fg], b[bg])
  const textBar = (scheme: string) => (scheme === 'contrast' ? 7 : 4.5)
  it('every block defines --edited and --edited-bg', () => {
    for (const [name, b] of Object.entries(BLOCKS)) for (const k of ['--edited', '--edited-bg']) expect(b[k], `${name} ${k}`).toBeTruthy()
  })
  it('the border --edited >= 3 on the fill --edited-bg it borders', () => {
    for (const [name, b] of Object.entries(BLOCKS)) {
      const r = ratio(b, '--edited', '--edited-bg')
      expect(r, `${name} --edited ${b['--edited']} on --edited-bg ${b['--edited-bg']} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(3)
    }
  })
  it('--edited as text meets the text bar on --bg, --panel and --edited-bg', () => {
    for (const [name, b] of Object.entries(BLOCKS)) for (const bg of ['--bg', '--panel', '--edited-bg']) {
      const r = ratio(b, '--edited', bg)
      expect(r, `${name} --edited ${b['--edited']} on ${bg} ${b[bg]} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(textBar(name))
    }
  })
  it('--fg meets the text bar on --edited-bg', () => {
    for (const [name, b] of Object.entries(BLOCKS)) {
      const r = ratio(b, '--fg', '--edited-bg')
      expect(r, `${name} --fg ${b['--fg']} on --edited-bg ${b['--edited-bg']} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(textBar(name))
    }
  })
  it('--edited is none of --accent, --warn and --attention', () => {
    for (const [name, b] of Object.entries(BLOCKS)) for (const k of ['--accent', '--warn', '--attention']) {
      expect(b['--edited'].toLowerCase(), `${name} --edited vs ${k}`).not.toBe(b[k].toLowerCase())
    }
  })
})

// An edited check box keeps its focus ring: an author `outline` on the control replaces the browser's :focus-visible ring and
// the contrast theme's 2px accent one, whatever the specificity, so the marker may set outline only while no ring shows.
describe('edited marker and the focus ring', () => {
  const rules = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((m) => ({ selectors: m[1].trim().split(',').map((x) => x.trim()), body: m[2] }))
    .filter((r) => r.selectors.some((x) => /\.edited(?![\w-])/.test(x)))
  it('finds the marker rules', () => { expect(rules.length).toBeGreaterThan(0) })
  it('a rule that outlines an edited control never applies while it is focus-visible', () => {
    const outlined = rules.filter((r) => /(^|[;\s])outline(-[\w]+)?\s*:/.test(r.body))
    for (const r of outlined) for (const x of r.selectors) expect(x).toMatch(/\.edited:not\(:focus-visible\)$/)
  })
  it('the marker sets no border width, border shorthand, padding or margin, so a field keeps its size when it becomes edited', () => {
    for (const r of rules) expect(r.body, r.selectors.join(', ')).not.toMatch(/(^|[;\s])(border(-(top|right|bottom|left))?(-width)?|padding(-\w+)?|margin(-\w+)?)\s*:/)
  })
  it('an edited check box and radio are still marked when they are not focused', () => {
    for (const type of ['checkbox', 'radio']) {
      const r = rules.find((x) => x.selectors.includes(`input[type="${type}"].edited:not(:focus-visible)`))
      expect(r?.body, type).toMatch(/outline\s*:[^;]*dashed[^;]*var\(--edited\)/)
    }
  })
})
