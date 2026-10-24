import { defineConfig } from "@playwright/test";

// One server: FastAPI on :8010 serving the built web app (dist) — what the iPad gets in `make server`.
// Models are off here (TIER1_ENABLED=false, TUTOR_PROVIDER=none): these tests are about ink transport.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:8010", viewport: { width: 1500, height: 700 }, browserName: "chromium" },
  webServer: {
    command:
      "npm run build && cd ../.. && TIER1_ENABLED=false TUTOR_PROVIDER=none DATA_DIR=$(mktemp -d) " +
      "uv run uvicorn inktutor_server.app:create_app --factory --host 127.0.0.1 --port 8010",
    url: "http://127.0.0.1:8010/healthz",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
