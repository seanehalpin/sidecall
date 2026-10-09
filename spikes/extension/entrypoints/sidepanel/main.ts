import { getBlob } from '@/lib/store';
import type { EchoCapture, EchoResult, MicError, Record5Result, Variant } from '@/lib/messages';

const $ = (id: string) => document.getElementById(id)!;
const OFFSCREEN_URL = browser.runtime.getURL('/offscreen.html');

async function refreshStatus() {
  const st = await navigator.permissions.query({ name: 'microphone' as PermissionName });
  $('perm').textContent = st.state;
  st.onchange = () => ($('perm').textContent = st.state);
  try {
    const s = (await browser.runtime.sendMessage({ target: 'background', type: 'sw-status' })) as {
      bootId: string;
      wsState: number;
      uptime: number;
    };
    $('sw').textContent = `boot ${s.bootId}, ws ${['connecting', 'open', 'closing', 'closed'][s.wsState] ?? 'none'}, up ${s.uptime}s`;
  } catch (e) {
    $('sw').textContent = `n/a (${String(e)})`;
  }
}

async function hasOffscreen(): Promise<boolean> {
  const off = browser.offscreen as unknown as { hasDocument?: () => Promise<boolean> };
  if (typeof off.hasDocument === 'function') return off.hasDocument(); // Chrome 150+
  const ctxs = await browser.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT' as Browser.runtime.ContextType],
    documentUrls: [OFFSCREEN_URL],
  });
  return ctxs.length > 0;
}

let creating: Promise<void> | null = null;
async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  // Only one offscreen doc per extension; guard against concurrent create calls.
  creating ??= browser.offscreen
    .createDocument({
      url: OFFSCREEN_URL,
      // NOT AUDIO_PLAYBACK: that reason auto-closes the doc after 30 s of silence.
      reasons: ['USER_MEDIA', 'WEB_RTC'] as Browser.offscreen.Reason[],
      justification: 'Microphone capture, WebRTC call audio and agent audio playback',
    })
    .finally(() => (creating = null));
  await creating;
}

async function toOffscreen<T>(msg: object): Promise<T> {
  await ensureOffscreen();
  return (await browser.runtime.sendMessage({ target: 'offscreen', ...msg })) as T;
}

async function player(key: string, label: string): Promise<HTMLElement> {
  const wrap = document.createElement('div');
  const blob = await getBlob(key);
  if (!blob) {
    wrap.textContent = `${label}: (missing)`;
    return wrap;
  }
  const url = URL.createObjectURL(blob);
  wrap.innerHTML = `<div>${label} · <a download="${key}.wav" href="${url}">download</a></div>`;
  const a = document.createElement('audio');
  a.controls = true;
  a.src = url;
  wrap.append(a);
  return wrap;
}

browser.runtime.onMessage.addListener((msg: { target?: string; type?: string; text?: string }) => {
  if (msg?.target === 'panel' && msg.type === 'progress') $('progress').textContent = msg.text ?? '';
  return false;
});

$('open-options').addEventListener('click', () => browser.runtime.openOptionsPage());

$('close-offscreen').addEventListener('click', async () => {
  if (await hasOffscreen()) await browser.offscreen.closeDocument();
  $('record5-out').textContent = 'offscreen closed';
});

$('record5').addEventListener('click', async () => {
  $('record5-out').textContent = 'working…';
  $('record5-player').innerHTML = '';
  const t0 = performance.now();
  try {
    const r = await toOffscreen<Record5Result | MicError>({ type: 'record5' });
    $('record5-out').textContent = JSON.stringify(r, null, 2);
    $('record5-out').dataset.result = r.ok ? 'ok' : `error:${r.name}`;
    if (r.ok) $('record5-player').append(await player(r.key, `5 s capture (${r.rmsDb.toFixed(1)} dBFS RMS)`));
  } catch (e) {
    $('record5-out').textContent = `panel error: ${String(e)}`;
    $('record5-out').dataset.result = 'panel-error';
  }
  $('record5-out').textContent += `\n(${Math.round(performance.now() - t0)} ms)`;
  refreshStatus();
});

$('panel-mic').addEventListener('click', async () => {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    const label = s.getAudioTracks()[0].label;
    s.getTracks().forEach((t) => t.stop());
    $('record5-out').textContent = `side panel getUserMedia OK (${label})`;
  } catch (e) {
    const err = e as DOMException;
    $('record5-out').textContent = `side panel getUserMedia failed: ${err.name}: ${err.message}`;
  }
  refreshStatus();
});

function verdict(on: EchoCapture, off: EchoCapture): { text: string; cls: string } {
  const reduction = off.leakDb - on.leakDb;
  if (off.leakDb < 10) return { text: `inconclusive — speaker too quiet (AEC-off leakage only ${off.leakDb.toFixed(1)} dB above floor)`, cls: 'bad' };
  if (on.leakDb < 6 || reduction >= 20) return { text: `cancelled (−${reduction.toFixed(1)} dB)`, cls: 'good' };
  if (reduction >= 8) return { text: `partially cancelled (−${reduction.toFixed(1)} dB)`, cls: 'bad' };
  return { text: `NOT cancelled (−${reduction.toFixed(1)} dB)`, cls: 'bad' };
}

$('echo').addEventListener('click', async () => {
  $('echo-out').textContent = '';
  $('echo-summary').innerHTML = '';
  $('echo-players').innerHTML = '';
  const variants: Variant[] = ['element', 'webaudio', 'webrtc'];
  try {
    const r = await toOffscreen<EchoResult | MicError>({ type: 'echo-test', variants });
    $('echo-out').textContent = JSON.stringify(r, null, 2);
    $('echo-out').dataset.result = r.ok ? 'ok' : `error:${r.name}`;
    if (!r.ok) return;
    const rows = r.captures
      .map(
        (c) =>
          `<tr><td>${c.variant}</td><td>${c.aec}</td><td>${c.prerollDb.toFixed(1)}</td><td>${c.playDb.toFixed(1)}</td><td>${c.leakDb.toFixed(1)}</td><td>${c.xcorr.toFixed(2)}</td></tr>`,
      )
      .join('');
    let html = `<table><tr><th>path</th><th>AEC</th><th>floor dBFS</th><th>during dBFS</th><th>leak dB</th><th>xcorr</th></tr>${rows}</table>`;
    for (const v of variants) {
      const off = r.captures.find((c) => c.variant === v && c.aec === 'off');
      if (!off) continue;
      for (const on of r.captures.filter((c) => c.variant === v && c.aec !== 'off')) {
        const vd = verdict(on, off);
        const label = on.aec === 'on' ? 'AEC true' : `AEC "${on.aec}"`;
        html += `<div class="verdict ${vd.cls}">${v} · ${label}: ${vd.text}</div>`;
      }
    }
    $('echo-summary').innerHTML = html;
    $('echo-players').append(await player('reference', 'reference clip'));
    for (const c of r.captures) $('echo-players').append(await player(c.key, `${c.variant} / AEC ${c.aec}`));
  } catch (e) {
    $('echo-out').textContent = `panel error: ${String(e)}`;
  }
});

refreshStatus();
