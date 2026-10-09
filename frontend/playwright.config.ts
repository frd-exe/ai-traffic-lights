import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests", timeout: 45000, workers: 1, fullyParallel: false,
  use: { baseURL: "http://127.0.0.1:5173", viewport: { width: 1500, height: 1120 },
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] }, trace: "retain-on-failure" },
  webServer: [
    { command: "npm run dev -- --host 127.0.0.1", url: "http://127.0.0.1:5173", reuseExistingServer: !process.env.CI },
    { command: process.platform === "win32" ? ".venv\\Scripts\\python.exe -m uvicorn backend.mock_server:app --host 127.0.0.1 --port 8000" : ".venv/bin/python -m uvicorn backend.mock_server:app --host 127.0.0.1 --port 8000",
      cwd: "..", url: "http://127.0.0.1:8000/api/health", reuseExistingServer: !process.env.CI },
  ],
});
