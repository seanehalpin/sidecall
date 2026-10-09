// Summarise a spike-3 server JSONL log: connects, disconnects, boots, pongs, gaps.
import { readFileSync } from 'node:fs';

const file = process.argv[2];
const all = readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));
// Only the extension SW's connections (path /sw); the python control probe (/probe) is summarised separately.
const probeConns = new Set(all.filter((r) => r.event === 'connect' && r.path === '/probe').map((r) => r.conn));
const recs = all.filter((r) => !probeConns.has(r.conn));
const probe = all.filter((r) => probeConns.has(r.conn));
const start = recs.find((r) => r.event === 'server_start');
const end = recs[recs.length - 1];
const connects = recs.filter((r) => r.event === 'connect');
const disconnects = recs.filter((r) => r.event === 'disconnect');
const msgs = recs.filter((r) => r.event === 'message').map((r) => ({ t: r.t, ...r.msg }));
const boots = [...new Set(msgs.filter((m) => m.bootId).map((m) => m.bootId))];
const pongs = msgs.filter((m) => m.type === 'pong');
const alarms = msgs.filter((m) => m.event === 'alarm');
const closes = msgs.filter((m) => m.event === 'ws_close');

console.log(`log: ${file}`);
console.log(`mode: ${start?.mode}  duration: ${(end.t / 60).toFixed(1)} min`);
console.log(`connects: ${connects.length}  disconnects: ${disconnects.length}`);
console.log(`distinct SW bootIds: ${boots.length} (${boots.join(', ')})`);
console.log(`pongs: ${pongs.length}  alarm wakes: ${alarms.length}  SW-reported ws_close: ${closes.length}`);
if (connects[0]) console.log(`origin header: ${connects[0].origin}`);
for (const d of disconnects) console.log(`  disconnect at t=${d.t}s conn=${d.conn} code=${d.code}`);
for (const c of connects) console.log(`  connect at t=${c.t}s conn=${c.conn}`);
const maxUp = Math.max(0, ...msgs.filter((m) => typeof m.uptime === 'number').map((m) => m.uptime));
console.log(`max SW uptime reported: ${maxUp}s`);
// Longest gap with no traffic from the SW
let prev = null, gap = 0;
for (const m of msgs) {
  if (prev !== null) gap = Math.max(gap, m.t - prev);
  prev = m.t;
}
console.log(`longest gap between SW messages: ${gap.toFixed(1)}s`);
if (probe.length) {
  const pp = probe.filter((r) => r.event === 'message' && r.msg.type === 'pong');
  console.log(`control probe: pongs ${pp.length}, last at t=${pp.at(-1)?.t}s, disconnects ${probe.filter((r) => r.event === 'disconnect').length}`);
}
const sent = recs.filter((r) => r.event === 'ping_sent');
if (sent.length) console.log(`pings sent to SW: ${sent.length}, pongs received: ${pongs.length}`);
