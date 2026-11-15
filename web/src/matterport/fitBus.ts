// Tiny in-page bus between FitForm and MatterportStage (S4), so the parent doesn't have to wire target picking:
//   pick    FitForm -> stage: true while the user should click a target on the floor
//   target  stage -> FitForm: the clicked floor point {x, z} (Showcase metres), or null
//   result  FitForm -> stage: the latest fit result (used when the stage has no `fitResult` prop)
//   doors   stage -> FitForm: the door store after load / save
import type { DoorStore, FitResult } from './types';

export interface FitBusEvents {
  pick: boolean;
  target: { x: number; z: number } | null;
  result: FitResult | null;
  doors: DoorStore;
}
type K = keyof FitBusEvents;
const subs: { [k in K]?: Set<(v: any) => void> } = {};
const last: Partial<FitBusEvents> = {};

export const fitBus = {
  on<k extends K>(name: k, fn: (v: FitBusEvents[k]) => void): () => void {
    (subs[name] ??= new Set()).add(fn);
    return () => { subs[name]?.delete(fn); };
  },
  emit<k extends K>(name: k, v: FitBusEvents[k]) {
    last[name] = v;
    subs[name]?.forEach((fn) => { try { fn(v); } catch (e) { console.error(e); } });
  },
  last<k extends K>(name: k): FitBusEvents[k] | undefined { return last[name] as FitBusEvents[k] | undefined; },
};
