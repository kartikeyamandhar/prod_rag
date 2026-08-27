// Incident 1 driver: ticket storm, 10 tickets arriving in the same second.
//
// v2 (audit B5): gracefulStop 120s so slow before-arm requests complete
// instead of being censored at the 30s default (v1's "p95 30s" was the
// instrument's cutoff, not a measurement); outcomes and latency are recorded
// per class in common.js; run with EXPECT_DEGRADED=none for the before arm
// (no admission control -> nothing should degrade) and leave it unset for the
// after arm (admission control degrades overflow BY DESIGN; the artifact
// records how many).
//
// Run: TAG=before EXPECT_DEGRADED=none GIT_SHA=$(git rev-parse HEAD) \
//      k6 run load/storm.js
import { postTicket, summaryFor } from "./common.js";
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
      gracefulStop: "120s",
    },
  },
  // Shape checks only; outcome distribution is measured, not thresholded.
  thresholds: {},
};

export default function () {
  postTicket(exec.scenario.iterationInTest);
}

export const handleSummary = summaryFor("incident1");
