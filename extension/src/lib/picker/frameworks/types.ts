import type { ChainEntry, FrameworkInfo, Repeat, SourceLocation } from '../../protocol.gen';

export type { ChainEntry, FrameworkInfo, Repeat, SourceLocation };

/** The generated type says object, the schema allows null ("unavailable"). */
export const asProps = (v: Record<string, unknown> | null): FrameworkInfo['props'] =>
  v as unknown as FrameworkInfo['props'];

export interface Resolved {
  framework: FrameworkInfo;
  repeat: Repeat | null;
  /** Nearest component's name, for hover labels. */
  componentName: string | null;
}

export interface ResolveOptions {
  /**
   * Full pick payload (chain with sources, props, repeat) vs the cheap hover subset
   * (source + nearest component name only).
   */
  full: boolean;
}

declare global {
  interface Window {
    __svelte?: { v?: Set<string> };
  }
}
