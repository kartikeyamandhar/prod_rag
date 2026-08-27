// Incident 4 driver: sustained reads plus a burst while a reindex/re-embed
// runs server-side. The reindex is started separately (updater tick or reload
// script); this supplies concurrent read traffic and measures what readers
// experience.
//
// v2 (audit B10): this documented driver is what actually runs (v1 ran
// sustained.js and the README implied this one); gracefulStop 120s so the
// blocked-read tail completes rather than being censored (v1's 4 dropped
// iterations hid the worst reads); no pass/fail thresholds — the headline
// number is the maximum read-block duration, read from the latency Trends in
// the exported artifact, not a threshold verdict.
//
// Run: TAG=during_reindex GIT_SHA=$(git rev-parse HEAD) k6 run load/reindex_under_load.js
import { postTicket, summaryFor } from "./common.js";
import exec from "k6/execution";

export const options = {
  scenarios: {
    background: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 2),
      timeUnit: "1s",
      duration: __ENV.DURATION || "45s",
      preAllocatedVUs: 20,
      maxVUs: 40,
      gracefulStop: "120s",
    },
    bursts: {
      executor: "constant-arrival-rate",
      rate: 10,
      timeUnit: "1s",
      duration: "1s",
      startTime: "15s",
      preAllocatedVUs: 10,
      maxVUs: 10,
      gracefulStop: "120s",
    },
  },
  thresholds: {},
};

export default function () {
  postTicket(exec.scenario.iterationInTest);
}

export const handleSummary = summaryFor("incident4");
