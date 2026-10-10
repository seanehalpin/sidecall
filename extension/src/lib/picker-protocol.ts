// The contract between the three halves of the picker (SPEC §5.2). Types only.
//
//   page (MAIN world)  <--window.postMessage-->  bridge (isolated world)
//                                                   <--chrome.runtime / tabs.sendMessage-->  service worker
//
// The MAIN-world script can't call chrome.* APIs, so every message goes through the
// bridge. All page <-> bridge traffic is wrapped in a PageEnvelope carrying a
// per-session nonce (a stray-message guard, not a secret: any script on the page can
// read it, and that's accepted because the page being prototyped is the user's own).
//
// Coordinates: `Rect`s are viewport CSS pixels (like getBoundingClientRect).

import type { ElementInfo, FrameworkInfo, PageInfo, Pick, Rect, RegionInfo, Repeat } from './protocol.gen';

/** What the page builds for a pick. The worker adds id, createdAt and screenshots. */
export type PickDraft = Omit<Pick, 'id' | 'createdAt' | 'screenshots'>;

/**
 * How to find a picked element again after an HMR update or reload (SPEC §5.2
 * "Re-anchoring"): source location first, then selector, then text fingerprint.
 */
export interface Anchor {
  tag: string;
  /** Raw source location as reported by the framework (not column-normalised). */
  source: { file: string; line: number; column: number } | null;
  selector: string;
  altSelectors: string[];
  /** Normalised leading text of the element (lowercased, whitespace-collapsed, <= 80 chars). */
  textFingerprint: string;
  /** Index among elements sharing the same source location, if any (0-based). */
  repeatIndex: number | null;
  /** For region picks: the rect it covered, when no element applies. In DOCUMENT coordinates (viewport rect + scroll at pick time), so the pin follows scrolling. */
  region: Rect | null;
}

export interface ViewportInfo {
  w: number;
  h: number;
  dpr: number;
}

/** A pin the page should draw. */
export interface PinSpec {
  id: number;
  anchor: Anchor;
  /** Hover/tooltip label, e.g. "CardHeader  src/lib/Card.svelte:14". */
  label: string;
  /** Picked in an earlier turn: drawn dimmed (still referenceable). */
  dimmed: boolean;
}

// --- page -> extension --------------------------------------------------------------

export type PageToExt =
  /** The picker is up in this document (first load, reload, or full-page navigation). */
  | { t: 'ready'; url: string }
  /** ⌥-click or ⌥-drag completed. */
  | { t: 'pick'; localId: string; draft: PickDraft; anchor: Anchor; rect: Rect; viewport: ViewportInfo }
  /** Esc: clear the current turn's picks. */
  | { t: 'escape' }
  /** Re-anchoring result for one pin (after HMR / DOM change / restore). */
  | { t: 'pin-status'; id: number; status: 'ok' | 'stale'; rect: Rect | null }
  /** Acknowledge overlay-hide / overlay-show so the worker can capture a clean screenshot. */
  | { t: 'overlay-ack'; reqId: string; visible: boolean };

// --- extension -> page --------------------------------------------------------------

export type ExtToPage =
  /** A pick was accepted and given an id: draw its pin. */
  | { t: 'pin-add'; localId: string; pin: PinSpec }
  | { t: 'pin-remove'; id: number }
  | { t: 'pins-clear' }
  /** After 'ready' on a reloaded page: redraw and re-anchor every live pin. */
  | { t: 'pins-restore'; pins: PinSpec[] }
  /** Dim the given pins (a turn was sent) / un-dim. */
  | { t: 'pin-dim'; ids: number[]; dimmed: boolean }
  /** The rail is hovering a chip: highlight that pin (null = none). */
  | { t: 'pin-highlight'; id: number | null }
  /** Hide or show all picker UI so captureVisibleTab doesn't include it. */
  | { t: 'overlay-set'; reqId: string; visible: boolean };

// --- window.postMessage wrapper (page <-> bridge) ------------------------------------

export const PICKER_CHANNEL = 'sidecall-picker';

export type PageEnvelope =
  | { channel: typeof PICKER_CHANNEL; nonce: string; dir: 'page-to-ext'; msg: PageToExt }
  | { channel: typeof PICKER_CHANNEL; nonce: string; dir: 'ext-to-page'; msg: ExtToPage };

/** The page announces its nonce once, before anything else, so the bridge can adopt it. */
export type PageHandshake = { channel: typeof PICKER_CHANNEL; dir: 'handshake'; nonce: string };

// --- bridge <-> service worker (chrome.runtime.sendMessage / tabs.sendMessage) -------

export const PICKER_RUNTIME_KIND = 'sidecall-picker-msg';

/** Bridge -> worker. The worker knows the tab from the sender. */
export interface PickerRuntimeUp {
  kind: typeof PICKER_RUNTIME_KIND;
  msg: PageToExt;
}

/** Worker -> bridge (via chrome.tabs.sendMessage). */
export interface PickerRuntimeDown {
  kind: typeof PICKER_RUNTIME_KIND;
  msg: ExtToPage;
}

// --- rail-facing pick chips -----------------------------------------------------------

/** What the side panel shows for one pick (built by the service worker). */
export interface PickChip {
  id: number;
  kind: 'element' | 'region';
  /** "div.card-header · CardHeader" */
  label: string;
  /** Tooltip: "src/lib/Card.svelte:14:3 (element) · repeated 12×, instance 3". */
  tooltip: string;
  /** Component name, if known (chip shows it first). */
  component: string | null;
  source: { file: string; line: number; precision: 'element' | 'component' } | null;
  repeat: Repeat | null;
  stale: boolean;
  /** Picked in an earlier turn (still referenceable, shown dimmed). */
  dimmed: boolean;
  /** Screenshot upload still in flight / failed. */
  status: 'capturing' | 'uploading' | 'ready' | 'failed';
}

/** Rail -> worker commands for picks (carried inside PageCommand, see messaging.ts). */
export type PickCommand =
  | { cmd: 'pickRemove'; id: number }
  | { cmd: 'picksClear' }
  | { cmd: 'pickHighlight'; id: number | null };

export type { ElementInfo, FrameworkInfo, PageInfo, RegionInfo };
