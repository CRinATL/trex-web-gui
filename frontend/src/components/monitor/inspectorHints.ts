// What a key means, for the ones whose raw value says nothing by itself (trex-core src/main_dpdk.cpp, the publisher's "ts").
export const KEY_HINTS: Record<string, string> = {
  ts: 'The server\'s high-resolution tick counter when it sent this (value) and its ticks per second (freq): value / freq is seconds on the server\'s clock, not wall-clock time.',
}
