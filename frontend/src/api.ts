import type {
  AreaRequest,
  AreaResponse,
  DemandResolveRequest,
  DemandResolveResponse,
  ErrorResponse,
  HealthResponse,
  SimStartRequest,
  SimStartResponse,
  SimStopResponse,
  SimTick,
} from "./types/contract.gen";

export type Scenario = "none" | "ai_limit" | "ai_replay" | "google_down";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (json as ErrorResponse | null)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? res.statusText);
  }
  return json as T;
}

const q = (scenario: Scenario) => (scenario === "none" ? "" : `?scenario=${scenario}`);

export const api = {
  health: () => call<HealthResponse>("GET", "/api/health"),
  area: (req: AreaRequest) => call<AreaResponse>("POST", "/api/area", req),
  resolveDemand: (req: DemandResolveRequest, scenario: Scenario) =>
    call<DemandResolveResponse>("POST", `/api/demand/resolve${q(scenario)}`, req),
  start: (req: SimStartRequest, scenario: Scenario) =>
    call<SimStartResponse>("POST", `/api/sim/start${q(scenario)}`, req),
  stop: (session_id: string) => call<SimStopResponse>("POST", "/api/sim/stop", { session_id }),
  aiReset: () => call("POST", "/api/ai/reset"),
};

/** Opens WS /ws/sim and calls onTick for each frame. Returns a close function. */
export function openSimSocket(
  sessionId: string,
  scenario: Scenario,
  onTick: (tick: SimTick) => void,
  onError: (msg: string) => void,
): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const scen = scenario === "none" ? "" : `&scenario=${scenario}`;
  const ws = new WebSocket(`${proto}://${location.host}/ws/sim?session_id=${encodeURIComponent(sessionId)}${scen}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data as string) as SimTick | ErrorResponse;
    if ("error" in msg) onError(msg.error.message);
    else onTick(msg);
  };
  ws.onerror = () => onError("WebSocket error");
  return () => ws.close();
}
