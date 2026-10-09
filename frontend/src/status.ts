import type { ControllerStatus, DataStatus } from "./types/contract.gen";

export type Banner = { level: "warn" | "info"; text: string };

/** Banner text for controller fallbacks (wording fixed by docs/CONTRACT.md "AI limit behavior"). */
export function controllerBanner(cs: ControllerStatus): Banner | null {
  const since = `since t=${Math.round(cs.since_t)} s`;
  switch (cs.state) {
    case "ai_limit_reached":
      return { level: "warn", text: `AI limit reached: signals reverted to traditional fixed timers (${since})` };
    case "ai_unavailable":
      return { level: "warn", text: `AI unavailable: signals reverted to traditional fixed timers (${since}). ${cs.message}` };
    default:
      return null;
  }
}

/** Demand is always simulated since contract 0.2.0 ("Simulated demand"): no banner. The other states
 *  are reserved; if a backend ever sends one, show its own message. */
export function dataBanner(ds: DataStatus): Banner | null {
  if (ds.state === "baseline_only") return null;
  return { level: "info", text: ds.message };
}

export function dailyCapFallback(cs: ControllerStatus): boolean {
  return cs.state === "ai_limit_reached" && cs.daily_cap > 0 && cs.calls_today >= cs.daily_cap;
}
