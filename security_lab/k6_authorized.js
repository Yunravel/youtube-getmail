import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    concurrent_quota_probe: {
      executor: "shared-iterations",
      vus: 40,
      iterations: 80,
      maxDuration: "30s",
    },
  },
  thresholds: {
    checks: ["rate>0.99"],
    http_req_failed: ["rate<0.01"],
  },
};

const baseUrl = __ENV.BASE_URL || "http://127.0.0.1:8088";
const token = __ENV.LAB_TOKEN || "lab-token-alice";

export default function () {
  const requestId = `k6-${__VU}-${__ITER}-${Date.now()}-${Math.random()}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    "X-Device-ID": "k6-device",
    "X-Session-ID": "k6-session",
    "X-Request-ID": requestId,
  };
  const response = http.post(
    `${baseUrl}/view-email`,
    JSON.stringify({ channel_id: "channel-001" }),
    { headers, responseCallback: http.expectedStatuses(200, 429) },
  );
  let body = {};
  try {
    body = response.json();
  } catch (_) {
    body = {};
  }
  check(response, {
    "only success or captcha response": (r) => r.status === 200 || r.status === 429,
    "blocked response never contains email": (r) => r.status !== 429 || !("email" in body),
    "success contains mock email": (r) => r.status !== 200 || body.email === "creator1@example.test",
  });
}

