import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RunButton } from './RunButton'

afterEach(cleanup)

describe('RunButton', () => {
  it('available: its kind, not lit, named by its action, clickable', async () => {
    const onClick = vi.fn()
    render(<RunButton kind="go" litLabel="Running" onClick={onClick}>Start</RunButton>)
    const b = screen.getByRole('button', { name: 'Start' })
    expect(b).toHaveAttribute('data-run', 'go')
    expect(b).not.toHaveAttribute('data-on')
    expect(b).toBeEnabled()
    await userEvent.click(b)
    expect(onClick).toHaveBeenCalledOnce()
  })
  it('lit: disabled, reads its state with the glyph hidden from the name, so colour is never the only cue', () => {
    render(<RunButton kind="go" lit litLabel="Transmitting">Start</RunButton>)
    const b = screen.getByRole('button', { name: 'Transmitting' })
    expect(b).toHaveAttribute('data-on')
    expect(b).toBeDisabled()
    expect(b.querySelector('.run-label > :first-child [data-glyph]')).toHaveTextContent('●')
  })
  it('keeps the width of its widest label: the other labels sit in the same cell, hidden and unnamed', () => {
    render(<RunButton kind="hold" lit litLabel="Paused" fits={['Transmitting']}>Start</RunButton>)
    const b = screen.getByRole('button', { name: 'Paused' })
    const ghosts = [...b.querySelectorAll('.run-ghost')]
    expect(ghosts.map((g) => g.textContent)).toEqual(['Start', '❚❚ Paused', '❚❚ Transmitting'])
    for (const g of ghosts) expect(g).toHaveAttribute('aria-hidden', 'true')
    expect(b.querySelector('.run-label > :first-child [data-glyph]')).toHaveTextContent('❚❚')
  })
  it('a plain run button (no lit form) is just its label', () => {
    render(<RunButton kind="stop" disabled>Stop</RunButton>)
    const b = screen.getByRole('button', { name: 'Stop' })
    expect(b).toHaveAttribute('data-run', 'stop')
    expect(b).toBeDisabled()
    expect(b.querySelector('.run-ghost')).toBeNull()
  })
})
