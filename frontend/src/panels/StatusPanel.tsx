import type { ControllerStatus, DataStatus } from "../types/contract.gen";
import { dailyCapFallback } from "../status";

export function ControllerBadge({ status }: { status?: ControllerStatus }) {
  if (!status) return <span className="badge">Waiting for stream</span>;
  const text = { ai_active: "AI active", ai_limit_reached: "Fixed fallback", ai_unavailable: "AI unavailable",
    ai_replay: "Replayed AI plans (not live)", traditional: status.effective_controller === "fixed" ? "Traditional timers" : status.effective_controller }[status.state];
  return <span className={`badge ${status.state === "ai_active" ? "green" : dailyCapFallback(status) ? "amber" : ""}`}>{text}
    {status.state === "ai_active" && <span> · {status.calls_today} / {status.daily_cap} calls</span>}</span>;
}

export function DataBadge({ status }: { status: DataStatus }) {
  return <div className="data-badge"><span className={`status-dot ${status.state === "google_live" ? "green" : "amber"}`} />
    <strong>{status.state}</strong><span>{status.calls_today} / {status.daily_cap} calls today</span>
    <small>Updated {status.last_update ? new Date(status.last_update).toLocaleString() : "— manual demand"}</small>
  </div>;
}
