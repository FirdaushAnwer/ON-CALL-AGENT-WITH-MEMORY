"""Generate data/incidents.json: 8 recurring incident patterns x 5 variations = 40 incidents.

Each pattern has a TEAM-SPECIFIC root cause that cannot be guessed from the alert text alone
(e.g. "the nightly recon cron holds connections"), so a stateless LLM gives a generic answer
while an agent with memory can recall what actually happened here before.

Supports both:
1. Fast offline generation (deterministic, seeded, no API key needed):
       python scripts/generate_incidents.py
2. LLM-assisted generation via Groq API (when GROQ_API_KEY is available):
       python scripts/generate_incidents.py --use-llm
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OUT = Path(__file__).resolve().parent.parent / "data" / "incidents.json"

PATTERNS = [
    {
        "pattern": "db_pool_exhausted",
        "services": ["billing-api", "invoice-service", "ledger-api", "orders-api", "reports-api"],
        "alerts": [
            "{ts} ERROR [{svc}] HikariPool-1 - Connection is not available, request timed out after 30000ms (total=50, active=50, idle=0, waiting={n})",
            "{ts} WARN  [{svc}] pgbouncer: no more connections allowed (max_client_conn={mx}); p99 latency {n}00ms on {host}",
        ],
        "root_cause": "The nightly invoice-reconciliation cron (recon-batch) runs without a statement timeout and holds database connections open past 02:00 UTC.",
        "fix": "Terminate the recon-batch sessions with pg_terminate_backend and set statement_timeout=60s on the recon-batch database role.",
        "failed": "Increasing the connection pool max size from 50 to 100 (Postgres then hit max_connections and everything got slower).",
    },
    {
        "pattern": "redis_out_of_memory",
        "services": ["auth-service", "session-gateway", "login-web", "sso-proxy", "profile-api"],
        "alerts": [
            "{ts} ERROR [{svc}] redis.exceptions.ResponseError: OOM command not allowed when used memory > 'maxmemory' (used_memory_human:{n}.9G) host={host}",
            "{ts} CRIT  [{svc}] Redis evicted_keys=0 used_memory_peak={n}.9G maxmemory=8G; SET session:* rejected on {host}",
        ],
        "root_cause": "Session keys written by auth-service have no TTL since the v2.14 deploy removed the EXPIRE call.",
        "fix": "Backfill a 24h TTL on all session:* keys and redeploy auth-service with the EXPIRE call restored.",
        "failed": "Running FLUSHALL on the cache (it logged every user out and the cache refilled with TTL-less keys within 20 minutes).",
    },
    {
        "pattern": "tls_cert_expired",
        "services": ["payments-gateway", "card-vault", "settlement-svc", "refunds-api", "fraud-check"],
        "alerts": [
            "{ts} ERROR [{svc}] x509: certificate has expired or is not yet valid: current time {ts} is after {ts} (peer {host})",
            "{ts} ERROR [{svc}] upstream connect error: TLS handshake failure, certificate verify failed, SSL alert 46 from {host}",
        ],
        "root_cause": "The internal mTLS certificate for the payments mesh was issued by the legacy cert-manager issuer, which was excluded from auto-renewal.",
        "fix": "Reissue the certificate from the corp-ca-v2 issuer and restart the envoy sidecars on the payments mesh.",
        "failed": "Restarting the payments pods (they reloaded the same expired certificate from the mounted secret).",
    },
    {
        "pattern": "feature_flag_5xx_spike",
        "services": ["checkout-service", "cart-api", "pricing-service", "storefront-bff", "promo-engine"],
        "alerts": [
            "{ts} ALERT [{svc}] 5xx rate {n}% (threshold 2%) region=eu-west; upstream connect error or disconnect/reset before headers, reset reason: connection failure",
            "{ts} ERROR [{svc}] HTTP 503 spike: {n}% of requests failing in eu-west after config sync at {ts}; trace id {host}",
        ],
        "root_cause": "The new-checkout-flow feature flag was enabled for 100% of traffic while its dependency tax-service v3 was not yet deployed in eu-west.",
        "fix": "Set the new-checkout-flow flag to 0% and deploy tax-service v3 to eu-west before re-enabling it.",
        "failed": "Rolling back the checkout-service container image (the flag, not the image, caused the errors, so nothing changed).",
    },
    {
        "pattern": "kafka_disk_full",
        "services": ["event-ingest", "clickstream-writer", "audit-log-sink", "metrics-pipeline", "notifications-bus"],
        "alerts": [
            "{ts} ERROR [{svc}] kafka.common.KafkaStorageException: No space left on device (broker {host}, /var/lib/kafka/data at 100%)",
            "{ts} ALERT [{svc}] Disk usage {n}9% on {host}; ISR shrink for 14 partitions, producers failing with NotEnoughReplicas",
        ],
        "root_cause": "Log retention for the clickstream-raw topic was raised to 30 days by an analytics experiment and never reverted.",
        "fix": "Revert retention.ms on clickstream-raw to 3 days and let the broker delete the old log segments.",
        "failed": "Expanding the EBS volume (the disk filled up again within 6 hours because retention was still 30 days).",
    },
    {
        "pattern": "dns_resolution_timeouts",
        "services": ["orders-api", "inventory-svc", "shipping-api", "catalog-service", "search-api"],
        "alerts": [
            "{ts} ERROR [{svc}] dial tcp: lookup orders-db.internal on 10.0.0.2:53: read udp i/o timeout (pod {host})",
            "{ts} WARN  [{svc}] getaddrinfo EAI_AGAIN inventory.internal after {n}000ms; intermittent, {n}% of requests affected on {host}",
        ],
        "root_cause": "CoreDNS pods are CPU-throttled because the dns-autoscaler maximum replica count is capped at 2 on this node pool.",
        "fix": "Raise the CoreDNS replicas to 6, lift the CPU limit, and update the dns-autoscaler maximum so it can scale out.",
        "failed": "Restarting the CoreDNS pods (relief lasted about 10 minutes before the throttling returned).",
    },
    {
        "pattern": "oomkilled_memory_leak",
        "services": ["image-resizer", "thumbnail-worker", "media-api", "upload-service", "avatar-service"],
        "alerts": [
            "{ts} ALERT [{svc}] Pod {host} restarted {n} times in 1h. Last State: Terminated, Reason: OOMKilled, Exit Code: 137",
            "{ts} ERROR [{svc}] container memory working set {n}00Mi / limit 1Gi, kernel: Memory cgroup out of memory: Killed process on {host}",
        ],
        "root_cause": "The image-resizer service caches decoded bitmaps in an unbounded in-process map, introduced in pull request 4412.",
        "fix": "Roll back to v1.9.3 and cap the bitmap cache with an LRU of 200 entries before redeploying.",
        "failed": "Raising the container memory limit from 1Gi to 2Gi (the service leaked up to 2Gi within 3 hours).",
    },
    {
        "pattern": "third_party_rate_limit",
        "services": ["notification-worker", "email-dispatcher", "alerts-mailer", "receipt-sender", "digest-job"],
        "alerts": [
            "{ts} ERROR [{svc}] HTTP 429 Too Many Requests from api.sendgrid.com (x-ratelimit-remaining: 0, retry queue depth {n}000) host={host}",
            "{ts} WARN  [{svc}] SendGrid rate limit exceeded; {n}00 sends/min, queue growing, worker {host} retrying",
        ],
        "root_cause": "A retry storm: notification-worker retries failed sends with no backoff after a template-rendering bug.",
        "fix": "Fix the template-rendering bug, enable exponential backoff with jitter in notification-worker, and purge the retry queue.",
        "failed": "Upgrading the SendGrid plan for a higher rate limit (the retry storm consumed the new quota within an hour).",
    },
]


def generate_deterministic() -> list[dict]:
    rng = random.Random(42)
    incidents: list[dict] = []
    for p in PATTERNS:
        for k in range(5):
            svc = p["services"][k % len(p["services"])]
            host = f"{svc.split('-')[0]}-{rng.choice(['prod', 'prd'])}-{rng.randint(1, 9)}{rng.choice('abcd')}.internal"
            ts = f"2026-{rng.randint(5, 10):02d}-{rng.randint(1, 28):02d}T{rng.choice(['02', '03', '09', '14', '21'])}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"
            template = p["alerts"][k % len(p["alerts"])]
            n = rng.randint(9, 15) if "{n}.9G" in template else rng.randint(12, 47)
            alert = template.format(ts=ts, svc=svc, host=host, n=n, mx=rng.choice([200, 300, 500]))
            incidents.append(
                {
                    "id": f"{p['pattern']}-{k + 1}",
                    "pattern": p["pattern"],
                    "timestamp": ts,
                    "service": svc,
                    "severity": rng.choice(["SEV2", "SEV2", "SEV3"]),
                    "raw_alert": alert,
                    "true_root_cause": p["root_cause"],
                    "fix_that_worked": p["fix"],
                    "failed_fix": p["failed"],
                }
            )
    return incidents


def generate_with_llm() -> list[dict]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY is not set. Falling back to deterministic generation.")
        return generate_deterministic()

    import httpx

    print("Generating incidents with Groq LLM...")
    incidents = []
    rng = random.Random(42)
    for p in PATTERNS:
        prompt = (
            f"Generate 5 realistic variation log lines / alert messages for the recurring incident pattern '{p['pattern']}'.\n"
            f"Services: {', '.join(p['services'])}\n"
            f"Confirmed root cause: {p['root_cause']}\n"
            f"Fix that worked: {p['fix']}\n"
            f"Fix that failed: {p['failed']}\n\n"
            "Return JSON only: an array of 5 objects, each with fields:\n"
            '  "service": str, "severity": "SEV2"|"SEV3", "raw_alert": str (realistic multi-part log line or alert)'
        )
        try:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": os.getenv("MODEL_PRIMARY", "openai/gpt-oss-120b"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4,
                    "response_format": {"type": "json_object"},
                },
                timeout=45.0,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            items = data.get("items") or data.get("variations") or (data if isinstance(data, list) else list(data.values())[0])
            for k, item in enumerate(items[:5]):
                ts = f"2026-{rng.randint(5, 10):02d}-{rng.randint(1, 28):02d}T{rng.choice(['02', '03', '09', '14', '21'])}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"
                incidents.append({
                    "id": f"{p['pattern']}-{k + 1}",
                    "pattern": p["pattern"],
                    "timestamp": ts,
                    "service": item.get("service", p["services"][k % len(p["services"])]),
                    "severity": item.get("severity", "SEV2"),
                    "raw_alert": item.get("raw_alert", f"Alert for {p['pattern']}"),
                    "true_root_cause": p["root_cause"],
                    "fix_that_worked": p["fix"],
                    "failed_fix": p["failed"],
                })
        except Exception as e:
            print(f"LLM generation failed for {p['pattern']} ({e}), using fallback variations.")
            return generate_deterministic()

    return incidents if len(incidents) == 40 else generate_deterministic()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic incidents dataset")
    parser.add_argument("--use-llm", action="store_true", help="Use Groq LLM to generate alert variations")
    args = parser.parse_args()

    incidents = generate_with_llm() if args.use_llm else generate_deterministic()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(incidents, indent=2) + "\n")
    print(f"Wrote {len(incidents)} incidents across {len(PATTERNS)} patterns to {OUT}")


if __name__ == "__main__":
    main()
