import type { ControllerStatus } from "../types/contract.gen";
import { dailyCapFallback } from "../status";

export function ControllerBadge({ status }: { status?: ControllerStatus }) {
  if (!status) return <span className="badge">Waiting for stream</span>;
  const text = { ai_active: "AI active", ai_limit_reached: status.effective_controller === "fixed" ? "Fixed timers (last resort)" : "Adaptive fallback", ai_unavailable: status.effective_controller === "fixed" ? "Fixed timers (last resort)" : "AI unavailable · adaptive fallback",
    ai_replay: "Replayed AI plans (not live)", traditional: status.effective_controller === "fixed" ? "Fixed timers" : status.effective_controller }[status.state];
  return <span className={`badge ${status.state === "ai_active" ? "green" : dailyCapFallback(status) ? "amber" : ""}`}>{text}
    {status.state === "ai_active" && <span> · {status.calls_today} / {status.daily_cap} calls</span>}</span>;
}
