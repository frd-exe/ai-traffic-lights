import type { AiResetResponse, AreaResponse, DemandResolveRequest, DemandResolveResponse, ErrorResponse,
  HealthResponse, SimDemandRequest, SimDemandResponse, SimStartRequest, SimStartResponse, SimStopResponse,
  SimTick } from "../types/contract.gen";

/** Mock-backend scenarios (ignored by the real backend, which uses GEMINI_FAKE_FAIL instead). */
export type Scenario = "none" | "ai_limit" | "ai_replay";
const base = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");
export const apiUrl = (path: string) => `${base}${path}`;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const response = await fetch(apiUrl(path), { method, signal: AbortSignal.timeout(20000),
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (json as ErrorResponse | null)?.error;
    throw new ApiError(response.status, error?.code ?? "http_error", error?.message ?? response.statusText);
  }
  if (json === null) throw new Error("The server returned an empty response.");
  return json as T;
}

const query = (scenario: Scenario) => scenario === "none" ? "" : `?scenario=${scenario}`;
export const api = {
  health: () => call<HealthResponse>("GET", "/api/health"),
  demoArea: () => call<AreaResponse>("GET", "/api/demo-area"),
  resolveDemand: (req: DemandResolveRequest) => call<DemandResolveResponse>("POST", "/api/demand/resolve", req),
  start: (req: SimStartRequest, scenario: Scenario = "none") => call<SimStartResponse>("POST", `/api/sim/start${query(scenario)}`, req),
  changeDemand: (req: SimDemandRequest) => call<SimDemandResponse>("POST", "/api/sim/demand", req),
  stop: (session_id: string) => call<SimStopResponse>("POST", "/api/sim/stop", { session_id }),
  aiReset: () => call<AiResetResponse>("POST", "/api/ai/reset"),
};

export function openSimSocket(sessionId: string, scenario: Scenario,
  onTick: (tick: SimTick) => void, onError: (message: string) => void, onClose: () => void): () => void {
  const url = new URL(apiUrl("/ws/sim"), location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("session_id", sessionId);
  if (scenario !== "none") url.searchParams.set("scenario", scenario);
  const socket = new WebSocket(url);
  let intentional = false;
  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data as string) as SimTick | ErrorResponse;
      if ("error" in message) onError(message.error.message);
      else if (message.session_id === sessionId && Number.isFinite(message.t)) onTick(message);
      else onError("Unexpected simulation frame.");
    } catch { onError("Could not read a simulation frame."); }
  };
  socket.onerror = () => { if (!intentional) onError("Simulation connection failed. Check the backend, then stop and restart."); };
  socket.onclose = (event) => {
    if (!intentional) {
      if (event.code !== 1000) onError(`Simulation disconnected (${event.code}). Stop and restart to reconnect.`);
      onClose();
    }
  };
  return () => { intentional = true; socket.close(); };
}
