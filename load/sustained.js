// Sustained background load; rate and duration overridable for incident runs.
import { postTicket } from "./common.js";
import exec from "k6/execution";

export const options = {
  scenarios: {
    sustained: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 2),
      timeUnit: "1s",
      duration: __ENV.DURATION || "60s",
      preAllocatedVUs: 20,
      maxVUs: 40,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  postTicket(exec.scenario.iterationInTest);
}
