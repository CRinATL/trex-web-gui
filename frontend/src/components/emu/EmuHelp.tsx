import { Info } from '../../ui/Info'

// How to get an EMU server to talk to, what the STL ports need, and that EMU has no owner.
export function EmuHelp() {
  return (
    <Info label="EMU">
      <p>EMU (the TRex emulation server) listens on port 4510 of the TRex host. Start TRex with it: <code>./t-rex-64 -i --emu</code>, or add <code>--emu</code> to the extra arguments of the Daemon dialog.</p>
      <p>EMU answers through a TRex port in any service state on the AF_PACKET test guest; on hardware NICs use service filtered mode with mask 29 (the console&apos;s <code>service --emu</code>).</p>
      <p>EMU has no ownership: every client of the server sees and changes the same namespaces and clients, and nothing here marks a namespace as yours unless this page loaded it.</p>
    </Info>
  )
}
