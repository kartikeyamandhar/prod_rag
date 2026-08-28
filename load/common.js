// Shared payloads, classified request helper, and summary export for k6 scenarios.
//
// v2 (audit B5): every request is classified by outcome (llm_completed /
// degraded_completed / http_error) with a latency Trend per class, because a
// degraded 200 and an LLM 200 are different outputs and averaging them
// produced v1's misleading "median 365ms". Checks assert protocol shape only;
// arm expectations (EXPECT_DEGRADED=none|any) are asserted when supplied.
import http from "k6/http";
import { check } from "k6";
import { Counter, Trend } from "k6/metrics";

export const API = __ENV.API_URL || "http://127.0.0.1:8080";

export const llmCompleted = new Counter("llm_completed");
export const degradedCompleted = new Counter("degraded_completed");
export const httpErrors = new Counter("http_error");
export const llmLatency = new Trend("llm_latency_ms", true);
export const degradedLatency = new Trend("degraded_latency_ms", true);

// Realistic ticket shapes across the three SIG domains; tenant ids spread out.
export const PAYLOADS = [
  { title: "kube-proxy conntrack entries leak after LoadBalancer service deletion", body: "After deleting a LoadBalancer service on v1.31, DNS lookups fail intermittently. Stale conntrack entries point at removed endpoints. Reproduced on three clusters.", tenant_id: 41 },
  { title: "Pods stuck Pending after preemption with topology spread constraints", body: "Scheduler preempts victims but the preemptor stays Pending. Only happens with topology spread constraints plus node affinity on v1.30.", tenant_id: 3 },
  { title: "CSI volume mount timeout after node reboot", body: "PVCs stay attached to the old node object; kubelet retries mount for 10 minutes before giving up. Storage class is ebs-csi with WaitForFirstConsumer.", tenant_id: 18 },
  { title: "Ingress path routing sends traffic to terminating endpoints", body: "During rolling updates requests hit pods in Terminating for several seconds. readinessProbe is configured. Using ingress-nginx with EndpointSlices on v1.29.", tenant_id: 7 },
  { title: "PVC deletion hangs with finalizer kubernetes.io/pv-protection", body: "Deleting a released PV hangs forever. Reclaim policy Delete. CSI driver logs show no delete calls arriving.", tenant_id: 25 },
  { title: "Scheduler scoring latency degrades with 5000 nodes", body: "Scheduling throughput drops from 300 to 40 pods/s as the cluster grows. Profile points at inter-pod affinity scoring.", tenant_id: 12 },
  { title: "DNS resolution fails for headless service after scale to zero and back", body: "After scaling a StatefulSet to 0 and back to 3, SRV records for the headless service stay empty for minutes.", tenant_id: 33 },
  { title: "Volume snapshot restore creates PVC stuck in Pending", body: "Restoring from a VolumeSnapshot, the new PVC never binds. snapshot-controller shows contentready true.", tenant_id: 44 },
  { title: "NodePort service unreachable from other nodes with externalTrafficPolicy Local", body: "Traffic to the NodePort only works on nodes hosting a backend pod. Health check node port reports correctly.", tenant_id: 9 },
  { title: "Taint based eviction ignores tolerationSeconds under API server load", body: "Pods evicted immediately instead of honoring tolerationSeconds=300 when the API server is under heavy load.", tenant_id: 28 },
];

export function postTicket(index) {
  const payload = PAYLOADS[index % PAYLOADS.length];
  const res = http.post(`${API}/tickets`, JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    timeout: "115s", // under gracefulStop so slow requests complete, not censor
  });

  let degraded = null;
  let route = null;
  if (res.status === 200) {
    try {
      const data = res.json();
      degraded = data.degraded;
      route = data.route.route;
    } catch (e) { /* classified below as shape failure */ }
  }

  if (res.status === 200 && degraded === false) {
    llmCompleted.add(1);
    llmLatency.add(res.timings.duration);
  } else if (res.status === 200 && degraded === true) {
    degradedCompleted.add(1);
    degradedLatency.add(res.timings.duration);
  } else {
    httpErrors.add(1);
  }

  const checks = {
    "status 200": (r) => r.status === 200,
    "valid route": () => ["auto_attach", "escalate", "request_info"].includes(route),
  };
  if (__ENV.EXPECT_DEGRADED === "none") {
    checks["arm expects no degraded responses"] = () => degraded === false;
  }
  check(res, checks);
  return res;
}

// Full metric export per arm; TAG names the artifact. dropped_iterations, when
// present, are load-generator saturation (VUs exhausted), not server failures.
export function summaryFor(prefix) {
  return function (data) {
    const tag = __ENV.TAG || "untagged";
    const out = {};
    out[`artifacts/incidents/${prefix}_${tag}_k6.json`] = JSON.stringify(
      {
        tag: tag,
        api: API,
        expect_degraded: __ENV.EXPECT_DEGRADED || "unset",
        git_sha: __ENV.GIT_SHA || "unset",
        box_env: __ENV.BOX_ENV || "unset", // the server-side admission/LLM config this arm ran under
        note_dropped_iterations: "load-generator artifact (VU pool exhausted), not a server failure",
        metrics: data.metrics,
        checks_root_group: data.root_group,
      },
      null,
      1,
    );
    return out;
  };
}
