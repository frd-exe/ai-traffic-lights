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
    case "ai_replay":
      return null;
    default:
      return null;
  }
}

export function dataBanner(ds: DataStatus): Banner | null {
  if (ds.state === "google_live") return null;
  const wording = ds.state === "baseline_only" ? "Google data unavailable: using manual baseline demand" :
    ds.state === "google_cached" ? "Google data unavailable: using cached traffic data" :
    "Google data unavailable: using recorded snapshot";
  return { level: ds.state === "baseline_only" ? "info" : "warn", text: wording };
}

export function dailyCapFallback(cs: ControllerStatus): boolean {
  return cs.state === "ai_limit_reached" && cs.daily_cap > 0 && cs.calls_today >= cs.daily_cap;
}
