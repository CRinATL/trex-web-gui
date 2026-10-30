import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { monitorStore } from '../../monitorStore'
import * as state from '../../state'
import idleMsg from '../../__fixtures__/daemonMsg.json'
import stfMsg from '../../__fixtures__/daemonMsgStf.json'
import stateful from '../../__fixtures__/monitorStateful.json'
import metadata from '../../__fixtures__/daemonMetadata.json'
import { resetHostCache } from '../../useHost'
import { forgetPushed } from './daemonForm'
import { DaemonDialog } from './DaemonDialog'

const idle = idleMsg as unknown as api.DaemonMsg
const stfRunning = stfMsg as unknown as api.DaemonMsg
// A TRex the daemon did not start is running: no running_seq, and not ours.
const foreign: api.DaemonMsg = { ...idle, status: { state: 'Running', verbose: 'TRex is Running' }, running_seq: null }
// A stateless run of ours.
const ours: api.DaemonMsg = { ...foreign, running_seq: 68, run: { mode: 'stl', seq: 68, profile: null, duration_s: null, started_at: 1700000000, state: 'running', outcome: null } }
const PREVIEW = 'nice -n -19 ./t-rex-64 --iom 0 --cfg /etc/trex_cfg.yaml -i --no-key'
const STF_PREVIEW = 'nice -n -19 ./t-rex-64 --iom 0 --cfg /etc/trex_cfg.yaml -f cap2/sfr2.yaml -d 20 -m 1.0 --no-key'
const APP = { ...state.initialState, backendOnline: true, daemonHint: { host: '127.0.0.1', port: 8090 }, serverPorts: { sync_port: 4701, async_port: 4600 } }

let app: state.AppState
function setApp(over: Partial<state.AppState>) {
  app = { ...APP, daemon: idle, ...over }
  vi.spyOn(state, 'useAppState').mockImplementation(() => app)
}

beforeEach(() => {
  vi.restoreAllMocks()
  monitorStore.reset()
  forgetPushed()
  resetHostCache()
  vi.spyOn(api, 'daemonConnect').mockResolvedValue({})
  vi.spyOn(api, 'daemonDisconnect').mockResolvedValue(idle)
  vi.spyOn(api, 'daemonStartPreview').mockImplementation((b) => Promise.resolve({ command_preview: b.mode === 'stf' ? STF_PREVIEW : PREVIEW }))
  vi.spyOn(api, 'daemonStfProfiles').mockResolvedValue({ source: 'stock', files: ['cap2/dns.yaml', 'cap2/sfr2.yaml'] })
  vi.spyOn(api, 'daemonStfProfile').mockImplementation((path) => Promise.resolve({ path, duration: 20, templates: ['cap2/Oracle.pcap', 'cap2/smtp.pcap'], errors: [], warnings: [] }))
  vi.spyOn(api, 'monitorConnect').mockResolvedValue({} as api.MonitorMsg)
  vi.spyOn(api, 'daemonLog').mockResolvedValue({ which: 'trex', text: 'log line' })
})
afterEach(() => { vi.useRealTimers() })

// Renders the dialog and waits for the first command preview, so Start is ready.
async function open(over: Partial<state.AppState> = {}, props: Partial<React.ComponentProps<typeof DaemonDialog>> = {}) {
  setApp(over)
  const r = render(<DaemonDialog onClose={() => undefined} {...props} />)
  await screen.findByText(PREVIEW)
  return r
}
const dialog = (name: string) => screen.getByRole('dialog', { name })
const pickStateful = async () => {
  await userEvent.click(screen.getByRole('radio', { name: /Stateful run/ }))
  await userEvent.selectOptions(await screen.findByLabelText('Profile'), 'cap2/sfr2.yaml')
  await vi.waitFor(() => expect(screen.getByLabelText('Duration -d (s)')).toHaveValue(20))
  await screen.findByText(STF_PREVIEW)
}

describe('DaemonDialog', () => {
  it('Start confirm shows the command', async () => {
    const start = vi.spyOn(api, 'daemonStart').mockResolvedValue({ seq: 7, command_preview: PREVIEW, run: ours.run! })
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    const confirm = dialog('Start TRex')
    expect(within(confirm).getByText(PREVIEW).tagName).toBe('CODE')
    expect(within(confirm).getByText(/127\.0\.0\.1/)).toBeInTheDocument()
    expect(start).not.toHaveBeenCalled()
    await userEvent.click(within(confirm).getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledWith({ cfg_path: '/etc/trex_cfg.yaml', options: {}, trex_args: '', mode: 'stl', timeout: 40, confirm: true })
    expect(screen.queryByRole('dialog', { name: 'Start TRex' })).toBeNull()
  })

  it('a refusal stays in the dialog', async () => {
    vi.spyOn(api, 'daemonStart').mockRejectedValue(new api.ApiError(409, 'foreign_trex', 'A TRex this daemon did not start is running: pid 2662'))
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    await userEvent.click(within(dialog('Start TRex')).getByRole('button', { name: 'Start' }))
    expect(await within(dialog('Start TRex')).findByRole('alert')).toHaveTextContent('A TRex this daemon did not start is running: pid 2662')
  })

  it('Force stop only when running without our seq', async () => {
    const { rerender } = await open()
    expect(screen.queryByRole('button', { name: 'Force stop' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
    setApp({ daemon: ours }); rerender(<DaemonDialog onClose={() => undefined} />)
    expect(screen.queryByRole('button', { name: 'Force stop' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled()
    setApp({ daemon: foreign }); rerender(<DaemonDialog onClose={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Force stop' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
    // a running TRex lights Start with its state (RC1-20); Force stop is a stop button
    expect(screen.getByRole('button', { name: 'TRex running' })).toHaveAttribute('data-on')
    expect(screen.getByRole('button', { name: 'TRex running' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Force stop' })).toHaveAttribute('data-run', 'stop')
    expect(screen.queryByRole('button', { name: 'Start' })).toBeNull()
  })

  it('Force stop asks again when the owners are unknown', async () => {
    const stop = vi.spyOn(api, 'daemonStop').mockRejectedValueOnce(new api.ApiError(409, 'owners_unknown', 'Cannot read who owns the ports'))
      .mockResolvedValue({ running: false, stop_s: 1.2 })
    await open({ daemon: foreign })
    await userEvent.click(screen.getByRole('button', { name: 'Force stop' }))
    expect(within(dialog('Force stop TRex')).getByText(/This TRex was not started by this GUI\. Force stop kills the daemon's TRex on 127\.0\.0\.1 whoever started it; it is still refused while another user owns a port\./)).toBeInTheDocument()
    await userEvent.click(within(dialog('Force stop TRex')).getByRole('button', { name: 'Force stop' }))
    const again = await screen.findByRole('dialog', { name: 'Force stop anyway?' })
    expect(again).toHaveTextContent("TRex's command port did not answer, so the GUI cannot see who owns its ports. Another user may be running traffic on this TRex. Force stop anyway?")
    expect(stop).toHaveBeenLastCalledWith({ rpc_port: 4701, confirm: true, force: true, owners_unknown_ack: false })
    await userEvent.click(within(again).getByRole('button', { name: 'Force stop anyway' }))
    expect(stop).toHaveBeenLastCalledWith({ rpc_port: 4701, confirm: true, force: true, owners_unknown_ack: true })
    expect(stop).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('dialog', { name: /Force stop/ })).toBeNull()
  })

  it('Force stop of a foreign stateful run says it has no command port', async () => {
    vi.spyOn(api, 'daemonStop').mockRejectedValueOnce(new api.ApiError(409, 'owners_unknown', 'Cannot tell who runs it'))
    const foreignStf = { ...foreign, trex_cmds: [{ pid: 7, cmd: './t-rex-64 --iom 0 --cfg /etc/trex_cfg.yaml -f cap2/sfr2.yaml -d 20 -m 1.0 --no-key' }] } as api.DaemonMsg
    await open({ daemon: foreignStf })
    await userEvent.click(screen.getByRole('button', { name: 'Force stop' }))
    await userEvent.click(within(dialog('Force stop TRex')).getByRole('button', { name: 'Force stop' }))
    const again = await screen.findByRole('dialog', { name: 'Force stop anyway?' })
    expect(again).toHaveTextContent('A stateful (-f) run has no command port, so the GUI cannot see who is using it. Another user may be running traffic on this TRex. Force stop anyway?')
    expect(again).not.toHaveTextContent('did not answer')
  })

  it('a Stop refused with owners_unknown shows an error line, not a second confirm', async () => {
    vi.spyOn(api, 'daemonStop').mockRejectedValue(new api.ApiError(409, 'owners_unknown', 'Cannot read who owns the ports'))
    await open({ daemon: ours })
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(within(dialog('Stop TRex')).getByText(/Stop the TRex this GUI started on 127\.0\.0\.1\? Every client connected to it, including this GUI's session, loses its connection\./)).toBeInTheDocument()
    await userEvent.click(within(dialog('Stop TRex')).getByRole('button', { name: 'Stop' }))
    expect(await within(dialog('Stop TRex')).findByRole('alert')).toHaveTextContent('Cannot read who owns the ports')
    expect(screen.queryByRole('dialog', { name: 'Force stop anyway?' })).toBeNull()
  })

  it('command port follows the pushed config', async () => {
    const pushed = { path: '/tmp/trex_files/lab.yaml', values: { zmq_rpc_port: 4601 } }
    await open({}, { initialPushed: pushed })
    expect(screen.getByLabelText('Config path')).toHaveValue('/tmp/trex_files/lab.yaml')
    expect(screen.getByLabelText('TRex command port')).toHaveValue('4601')
    fireEvent.change(screen.getByLabelText('Config path'), { target: { value: '/etc/trex_cfg.yaml' } })
    expect(screen.getByLabelText('TRex command port')).toHaveValue('4701')   // serverPorts.sync_port
  })

  it('command port is 4501 when neither a push nor the Control connect says', async () => {
    await open({ serverPorts: null })
    expect(screen.getByLabelText('TRex command port')).toHaveValue('4501')
  })

  it('mode switches between stateless and a stateful run', async () => {
    const preview = vi.mocked(api.daemonStartPreview)
    await open()
    expect(preview).toHaveBeenLastCalledWith({ cfg_path: '/etc/trex_cfg.yaml', options: {}, trex_args: '', mode: 'stl' })
    expect(screen.getByRole('radio', { name: /Stateless/ })).toBeChecked()
    expect(screen.getByLabelText('Extra arguments')).toBeInTheDocument()
    expect(screen.getByLabelText('TRex command port')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Stateful run' })).toBeNull()
    await pickStateful()
    expect(screen.queryByLabelText('Extra arguments')).toBeNull()
    expect(screen.queryByLabelText('TRex command port')).toBeNull()
    expect(screen.getByRole('group', { name: 'Stateful run' })).toBeInTheDocument()
    expect(preview).toHaveBeenLastCalledWith({ cfg_path: '/etc/trex_cfg.yaml', options: {}, trex_args: '', mode: 'stf', stf: { profile: 'cap2/sfr2.yaml', d: 20, m: 1, l: 0, rx_check: 0 } })
    await userEvent.click(screen.getByRole('radio', { name: /Stateless/ }))
    expect(await screen.findByLabelText('Extra arguments')).toBeInTheDocument()
  })

  it('stateful start confirm shows the command and the duration', async () => {
    await open()
    await pickStateful()
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    const confirm = dialog('Start TRex')
    expect(within(confirm).getByText(STF_PREVIEW).tagName).toBe('CODE')
    expect(within(confirm).getByText('It runs as root on 127.0.0.1 for 20 s. A stateful run has no command port, so Control cannot connect to it; watch it on the Monitor.')).toBeInTheDocument()
  })

  it('start attaches the Monitor', async () => {
    const stfRun = { mode: 'stf' as const, seq: 44, profile: 'cap2/sfr2.yaml', duration_s: 20, started_at: Date.now() / 1000, state: 'running' as const, outcome: null }
    const start = vi.spyOn(api, 'daemonStart').mockResolvedValue({ seq: 44, command_preview: STF_PREVIEW, run: stfRun })
    const setLastStfRun = vi.fn()
    vi.spyOn(state, 'useDaemonActions').mockReturnValue({ setLastStfRun })
    const { rerender } = await open()
    await pickStateful()
    const confirmStart = async () => {
      await userEvent.click(screen.getByRole('button', { name: 'Start' }))
      await userEvent.click(within(dialog('Start TRex')).getByRole('button', { name: 'Start' }))
    }
    await confirmStart()
    // pub port: serverPorts.async_port, the config path not being a pushed one
    expect(start).toHaveBeenCalledWith(expect.objectContaining({ mode: 'stf', trex_args: '', timeout: 40, confirm: true }))
    expect(api.monitorConnect).toHaveBeenCalledWith('127.0.0.1', 4600)
    expect(setLastStfRun).toHaveBeenCalledWith({ host: '127.0.0.1', endsAt: expect.any(Number) })
    const ends = setLastStfRun.mock.calls[0][0].endsAt as number
    expect(ends - Date.now()).toBeGreaterThan(19000)
    setApp({ daemon: { ...stfRunning, run: stfRun } }); rerender(<DaemonDialog onClose={() => undefined} />)
    expect(screen.getByText(/ends in about 00:(19|20)/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Monitor' })).toHaveAttribute('href', '/monitor')

    // the Monitor already on that pair: no second connect
    vi.mocked(api.monitorConnect).mockClear()
    act(() => monitorStore.push({ ...(stateful as unknown as api.MonitorMsg), target: { host: '127.0.0.1', port: 4600 } }))
    setApp({ daemon: idle }); rerender(<DaemonDialog onClose={() => undefined} />)
    await screen.findByText(STF_PREVIEW)
    await confirmStart()
    expect(start).toHaveBeenCalledTimes(2)
    expect(api.monitorConnect).not.toHaveBeenCalled()
  })

  it('countdown then ended then the final log', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'] })
    vi.setSystemTime(new Date(1700000000_000))
    setApp({ daemon: stfRunning })
    const { rerender } = render(<DaemonDialog onClose={() => undefined} />)
    expect(screen.getByText('ends in about 00:20')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByText('ends in about 00:15')).toBeInTheDocument()
    expect(api.daemonLog).not.toHaveBeenCalled()
    const ended: api.DaemonMsg = { ...idle, run: { ...stfRunning.run!, state: 'ended', outcome: 'TRex finished.' } }
    setApp({ daemon: ended }); rerender(<DaemonDialog onClose={() => undefined} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    expect(screen.getByText('Ended: TRex finished.')).toBeInTheDocument()
    expect(screen.queryByText(/ends in about/)).toBeNull()
    expect(api.daemonLog).toHaveBeenCalledTimes(1)
    expect(api.daemonLog).toHaveBeenCalledWith('trex')
    expect(screen.getByRole('heading', { name: 'Final log' })).toBeInTheDocument()
    expect(screen.getByText('log line').tagName).toBe('PRE')
    // another message about the same run does not fetch again
    setApp({ daemon: { ...ended, last_error: null, busy: true } }); rerender(<DaemonDialog onClose={() => undefined} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    expect(api.daemonLog).toHaveBeenCalledTimes(1)
  })

  it('a stopped stateful run says Stopped', async () => {
    setApp({ daemon: { ...idle, run: { ...stfRunning.run!, state: 'stopped', outcome: 'TRex finished.' } } })
    render(<DaemonDialog onClose={() => undefined} />)
    expect(await screen.findByText('Stopped')).toBeInTheDocument()
  })

  it('stop of our stateful run sends one confirm', async () => {
    const stop = vi.spyOn(api, 'daemonStop').mockResolvedValue({ running: false, stop_s: 1.9 })
    setApp({ daemon: stfRunning })
    render(<DaemonDialog onClose={() => undefined} />)
    expect(screen.queryByRole('button', { name: 'Force stop' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    await userEvent.click(within(dialog('Stop TRex')).getByRole('button', { name: 'Stop' }))
    expect(stop).toHaveBeenCalledTimes(1)
    // the hidden command port keeps its prefill, which Stop sends so the body validates
    expect(stop).toHaveBeenCalledWith({ rpc_port: 4701, confirm: true, force: false, owners_unknown_ack: false })
    expect(screen.queryByRole('dialog', { name: 'Force stop anyway?' })).toBeNull()
    expect(screen.queryByRole('dialog', { name: 'Stop TRex' })).toBeNull()
  })

  it('closing disconnects', async () => {
    const onClose = vi.fn()
    await open({}, { onClose })
    vi.mocked(api.daemonDisconnect).mockClear()
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(api.daemonDisconnect).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalled()
  })

  it('Escape with a confirmation open closes only the confirmation', async () => {
    const onClose = vi.fn()
    await open({}, { onClose })
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Start TRex' })).toBeNull()
    expect(onClose).not.toHaveBeenCalled()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('logs refresh while shown', async () => {
    setApp({})
    render(<DaemonDialog onClose={() => undefined} />)
    await screen.findByText(PREVIEW)
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    fireEvent.click(screen.getByRole('tab', { name: 'Logs' }))
    await act(async () => { await Promise.resolve() })
    expect(api.daemonLog).toHaveBeenCalledTimes(1)
    expect(api.daemonLog).toHaveBeenLastCalledWith('trex')
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(api.daemonLog).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(api.daemonLog).toHaveBeenCalledTimes(3)
    expect(screen.getByLabelText('Log text')).toHaveTextContent('log line')
    fireEvent.change(screen.getByLabelText('Log'), { target: { value: 'daemon' } })
    await act(async () => { await Promise.resolve() })
    expect(api.daemonLog).toHaveBeenLastCalledWith('daemon')
    fireEvent.click(screen.getByRole('tab', { name: 'Run' }))   // hidden: no more refreshes
    const n = vi.mocked(api.daemonLog).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(api.daemonLog).toHaveBeenCalledTimes(n)
  })

  it('shows the root-daemon warning in the connect bar', async () => {
    await open()
    const note = screen.getByRole('note', { name: '' })
    expect(note).toHaveTextContent(/The TRex daemon runs as root with no authentication and binds 0\.0\.0\.0 by default/)
    expect(note).toHaveTextContent(/restrict 8090 .* before using daemon control outside the test VM/)
    expect(screen.getAllByText(/runs as root with no authentication/)).toHaveLength(1)
  })

  it('Connect sends the host and port, and opening reconnects to the last target', async () => {
    setApp({ daemon: null })
    const first = render(<DaemonDialog onClose={() => undefined} />)
    expect(screen.getByLabelText('Host')).toHaveValue('127.0.0.1')
    expect(screen.getByLabelText('Port')).toHaveValue('8090')
    fireEvent.change(screen.getByLabelText('Port'), { target: { value: '8091' } })
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(api.daemonConnect).toHaveBeenLastCalledWith('127.0.0.1', 8091)
    first.unmount()
    vi.mocked(api.daemonConnect).mockClear()
    setApp({ daemon: null })
    render(<DaemonDialog onClose={() => undefined} />)
    expect(screen.getByLabelText('Port')).toHaveValue('8091')
    await vi.waitFor(() => expect(api.daemonConnect).toHaveBeenCalledWith('127.0.0.1', 8091))
  })

  it('a failed connect shows the backend message', async () => {
    vi.mocked(api.daemonConnect).mockRejectedValue(new api.ApiError(502, 'daemon_unreachable', 'nothing answers on 127.0.0.1:8099'))
    setApp({ daemon: null })
    render(<DaemonDialog onClose={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('nothing answers on 127.0.0.1:8099')
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('State: not connected')
  })

  it('the status line and block show the daemon', async () => {
    await open({ daemon: { ...ours, run: { ...ours.run!, started_at: 1700000000 } } })
    const line = screen.getByText(/^State: connected/)
    expect(line).toHaveTextContent('v3.08')
    expect(line).toHaveTextContent('running since')
    expect(screen.getByText('A TRex the daemon did not start blocks Start.')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'TRex processes on the host' })).toHaveTextContent('pid 2662')
  })

  it('the Start confirm sends exactly what it shows, whatever the form does behind it', async () => {
    const start = vi.spyOn(api, 'daemonStart').mockResolvedValue({ seq: 7, command_preview: PREVIEW, run: ours.run! })
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    fireEvent.change(screen.getByLabelText('Cores (-c)'), { target: { value: '8' } })
    fireEvent.change(screen.getByLabelText('Start timeout (s)'), { target: { value: '99' } })
    expect(within(dialog('Start TRex')).getByText(PREVIEW).tagName).toBe('CODE')
    await userEvent.click(within(dialog('Start TRex')).getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledWith({ cfg_path: '/etc/trex_cfg.yaml', options: {}, trex_args: '', mode: 'stl', timeout: 40, confirm: true })
  })

  it('the stateful Start confirm is a snapshot too', async () => {
    const stfRun = { ...stfRunning.run!, started_at: Date.now() / 1000 }
    const start = vi.spyOn(api, 'daemonStart').mockResolvedValue({ seq: 44, command_preview: STF_PREVIEW, run: stfRun })
    await open()
    await pickStateful()
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    fireEvent.change(screen.getByLabelText('Profile'), { target: { value: 'cap2/dns.yaml' } })
    fireEvent.change(screen.getByLabelText('Duration -d (s)'), { target: { value: '300' } })
    const confirm = dialog('Start TRex')
    expect(within(confirm).getByText(STF_PREVIEW)).toBeInTheDocument()
    expect(confirm).toHaveTextContent('for 20 s.')
    await userEvent.click(within(confirm).getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledWith(expect.objectContaining({ mode: 'stf', stf: { profile: 'cap2/sfr2.yaml', d: 20, m: 1, l: 0, rx_check: 0 } }))
  })

  it('a later run counts down from its own end', async () => {
    const run44 = { ...stfRunning.run!, started_at: Date.now() / 1000 }
    vi.spyOn(api, 'daemonStart').mockResolvedValue({ seq: 44, command_preview: STF_PREVIEW, run: run44 })
    const { rerender } = await open()
    await pickStateful()
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    await userEvent.click(within(dialog('Start TRex')).getByRole('button', { name: 'Start' }))
    // a run started elsewhere afterwards: seq 45, 600 s
    setApp({ daemon: { ...stfRunning, running_seq: 45, run: { ...run44, seq: 45, duration_s: 600 } } }); rerender(<DaemonDialog onClose={() => undefined} />)
    expect(screen.getByText(/ends in about (09|10):/)).toBeInTheDocument()
  })

  it('the final log is read only while connected, and a failed read is tried again on reconnect', async () => {
    const ended: api.DaemonMsg = { ...idle, run: { ...stfRunning.run!, state: 'ended', outcome: 'TRex finished.' } }
    const log = vi.mocked(api.daemonLog)
    log.mockRejectedValueOnce(new api.ApiError(502, 'daemon_unreachable', 'gone'))
    setApp({ daemon: { ...ended, state: 'disconnected' } })
    const { rerender } = render(<DaemonDialog onClose={() => undefined} />)
    expect(log).not.toHaveBeenCalled()
    setApp({ daemon: ended }); rerender(<DaemonDialog onClose={() => undefined} />)
    expect(await screen.findByText(/Could not read the log: gone/)).toBeInTheDocument()
    expect(log).toHaveBeenCalledTimes(1)
    setApp({ daemon: { ...ended, busy: true } }); rerender(<DaemonDialog onClose={() => undefined} />)
    expect(log).toHaveBeenCalledTimes(1)   // not on every render
    setApp({ daemon: { ...ended, state: 'disconnected' } }); rerender(<DaemonDialog onClose={() => undefined} />)
    setApp({ daemon: ended }); rerender(<DaemonDialog onClose={() => undefined} />)
    expect(await screen.findByText('log line')).toBeInTheDocument()
    expect(log).toHaveBeenCalledTimes(2)
  })

  it('a bad command port typed in Stateless mode does not block Stop of a stateful run', async () => {
    const stop = vi.spyOn(api, 'daemonStop').mockResolvedValue({ running: false, stop_s: 1 })
    setApp({ daemon: stfRunning })
    render(<DaemonDialog onClose={() => undefined} />)
    fireEvent.change(screen.getByLabelText('TRex command port'), { target: { value: 'x' } })
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()   // visible, so the error is too
    expect(screen.getByText('Command port must be 1–65535')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('radio', { name: /Stateful run/ }))
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    await userEvent.click(within(dialog('Stop TRex')).getByRole('button', { name: 'Stop' }))
    expect(stop).toHaveBeenCalledWith({ rpc_port: 4701, confirm: true, force: false, owners_unknown_ack: false })
  })

  it('a Push survives closing the dialog: the reopened Run tab still follows the pushed file and its ports', async () => {
    const pushedValues = { version: 2, zmq_rpc_port: 4999, zmq_pub_port: 4888 }
    vi.spyOn(api, 'daemonConnect').mockResolvedValue({ metadata, metadata_version: 'v1', config_yaml: '', files_path: '/tmp/trex_files', devices: [] })
    vi.spyOn(api, 'daemonConfigPreview').mockResolvedValue({ yaml: '- version: 2\n', values: pushedValues, errors: [] })
    vi.spyOn(api, 'daemonConfigPush').mockResolvedValue({ path: '/tmp/trex_files/lab.yaml' })
    const first = await open()
    expect(screen.getByLabelText('Config path')).toHaveValue('/etc/trex_cfg.yaml')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await userEvent.click(screen.getByRole('tab', { name: 'YAML' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Push…' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Push…' }))
    await userEvent.click(within(dialog('Push config')).getByRole('button', { name: 'Push' }))
    // the open Run tab follows at once
    await userEvent.click(screen.getByRole('tab', { name: 'Run' }))
    expect(screen.getByLabelText('Config path')).toHaveValue('/tmp/trex_files/lab.yaml')
    expect(screen.getByLabelText('TRex command port')).toHaveValue('4999')
    first.unmount()
    // reopened: a new dialog, the pushed state kept outside it
    setApp({})
    render(<DaemonDialog onClose={() => undefined} />)
    expect(screen.getByLabelText('Config path')).toHaveValue('/tmp/trex_files/lab.yaml')
    expect(screen.getByLabelText('TRex command port')).toHaveValue('4999')
    await userEvent.click(screen.getByRole('radio', { name: /Stateful run/ }))
    expect(screen.getByLabelText('Monitor port')).toHaveValue(4888)
  })

  it('a Push to one daemon does not prefill another daemon\'s Run tab', async () => {
    vi.spyOn(api, 'daemonConnect').mockResolvedValue({ metadata, metadata_version: 'v1', config_yaml: '', files_path: '/tmp/trex_files', devices: [] })
    vi.spyOn(api, 'daemonConfigPreview').mockResolvedValue({ yaml: '- version: 2\n', values: { version: 2, zmq_rpc_port: 4999 }, errors: [] })
    vi.spyOn(api, 'daemonConfigPush').mockResolvedValue({ path: '/tmp/trex_files/lab.yaml' })
    const first = await open()
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await userEvent.click(screen.getByRole('tab', { name: 'YAML' }))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Push…' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Push…' }))
    await userEvent.click(within(dialog('Push config')).getByRole('button', { name: 'Push' }))
    first.unmount()
    setApp({ daemon: { ...idle, target: { host: '10.0.0.9', port: 8090 } } })
    render(<DaemonDialog onClose={() => undefined} />)
    expect(await screen.findByLabelText('Config path')).toHaveValue('/etc/trex_cfg.yaml')
    expect(screen.getByLabelText('TRex command port')).toHaveValue('4701')   // the Control connect's port, not 4999
  })

  it('opening while connected with no metadata reconnects to read it', async () => {
    vi.spyOn(api, 'daemonConnect').mockResolvedValue({ metadata, metadata_version: 'v1', config_yaml: '', files_path: '/tmp/trex_files', devices: [] })
    await open()
    await vi.waitFor(() => expect(api.daemonConnect).toHaveBeenCalledWith('127.0.0.1', 8090))
    await userEvent.click(screen.getByRole('tab', { name: 'Config' }))
    expect(await screen.findByLabelText('Port limit')).toBeInTheDocument()
    expect(screen.queryByText(/Connect to a daemon/)).toBeNull()
  })

  it('the command-port note names the trex-web-gui host, or the computer running trex-web-gui serve', async () => {
    vi.spyOn(api, 'getAbout').mockResolvedValue({ app_version: '1', trex_pin: { upstream_sha: 'x', patches: [] }, supported: 's', host: 'trex-host' })
    await open()
    expect(await screen.findByText(/^the port trex-host reaches the daemon's TRex on/)).toBeInTheDocument()
  })

  it('the command-port note falls back while the host is unknown', async () => {
    vi.spyOn(api, 'getAbout').mockReturnValue(new Promise(() => {}))
    await open()
    expect(screen.getByText(/^the port the computer running trex-web-gui serve reaches the daemon's TRex on/)).toBeInTheDocument()
  })
})
