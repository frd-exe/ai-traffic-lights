import type { ControllerStatus, DataStatus } from "./types/contract.gen";

export type Banner = { level: "warn" | "info"; text: string };

/** Banner text for controller fallbacks (wording fixed by docs/CONTRACT.md "AI limit behavior"). */
export function controllerBanner(cs: ControllerStatus): Banner | null {
  const since = `since t=${Math.round(cs.since_t)} s`;
  switch (cs.state) {
    case "ai_limit_reached":
      return { level: "warn", text: `AI limit reached: signals reverted to traditional fixed timers (${since})` };
    case "ai_unavailable":
      return { level: "warn", text: `AI unavailable: signals reverted to traditional fixed timers (${since})` };
    case "ai_replay":
      return { level: "info", text: `AI replay: replaying recorded Gemini plans, no live calls (${since})` };
    default:
      return null;
  }
}

export function dataBanner(ds: DataStatus): Banner | null {
  if (ds.state === "google_live") return null;
  return { level: ds.state === "baseline_only" ? "info" : "warn", text: ds.message };
}
