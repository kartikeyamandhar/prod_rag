// Incident 1 driver: ticket storm, 10 tickets arriving in the same second.
import { postTicket } from "./common.js";
import exec from "k6/execution";

export const options = {
  scenarios: {
    storm: {
      executor: "constant-arrival-rate",
      rate: 10,
      timeUnit: "1s",
      duration: "1s",
      preAllocatedVUs: 10,
      maxVUs: 10,
    },
  },
  thresholds: {
    http_req_failed: ["rate==0"],
    checks: ["rate==1.0"],
  },
};

export default function () {
  postTicket(exec.scenario.iterationInTest);
}
