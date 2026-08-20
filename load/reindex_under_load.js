// Incident 4 driver: storm bursts while a reindex/re-embed runs server-side.
// The reindex itself is started separately (updater tick or reindex script);
// this script supplies the concurrent read traffic and measures its latency.
import { postTicket } from "./common.js";
import exec from "k6/execution";

export const options = {
  scenarios: {
    background: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 2),
      timeUnit: "1s",
      duration: __ENV.DURATION || "30s",
      preAllocatedVUs: 20,
      maxVUs: 40,
    },
    bursts: {
      executor: "constant-arrival-rate",
      rate: 10,
      timeUnit: "1s",
      duration: "1s",
      startTime: "10s",
      preAllocatedVUs: 10,
      maxVUs: 10,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  postTicket(exec.scenario.iterationInTest);
}
