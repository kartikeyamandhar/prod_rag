// Shared payloads and request helper for all k6 scenarios.
import http from "k6/http";
import { check } from "k6";

export const API = __ENV.API_URL || "http://127.0.0.1:8080";

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
  });
  check(res, {
    "status 200": (r) => r.status === 200,
    "has route": (r) => {
      try { return ["auto_attach", "escalate", "request_info"].includes(r.json().route.route); }
      catch (e) { return false; }
    },
  });
  return res;
}
