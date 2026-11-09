# Synthetic Incident Dataset & Persistent Storage

This directory contains the synthetic incident dataset and persistent memory state used by the On-Call Memory Agent.

---

## 1. `data/incidents.json`

A collection of 40 realistic operational incidents across 8 recurring failure patterns (5 variations each).

### Failure Patterns & Team-Specific Root Causes:
1. **`db_pool_exhausted`**:
   - *Symptom*: HikariPool connection timeout (`total=50, active=50, waiting=...`), pgbouncer connection limit.
   - *True Root Cause*: Nightly invoice-reconciliation cron (`recon-batch`) runs without a statement timeout, holding connections open past 02:00 UTC.
   - *Fix That Worked*: Terminate sessions with `pg_terminate_backend` and set `statement_timeout=60s` on the database role.
   - *Failed Fix*: Increasing connection pool max size from 50 to 100 (exhausted Postgres max_connections).
2. **`redis_out_of_memory`**:
   - *Symptom*: `redis.exceptions.ResponseError: OOM command not allowed when used memory > 'maxmemory'`.
   - *True Root Cause*: Auth service session keys written without a TTL after deploy omitted `EXPIRE`.
   - *Fix That Worked*: Backfill 24h TTL on all `session:*` keys and restore `EXPIRE` in auth service.
   - *Failed Fix*: Running `FLUSHALL` (logged all active users out, cache refilled without TTLs within 20m).
3. **`tls_cert_expired`**:
   - *Symptom*: `x509: certificate has expired or is not yet valid`, SSL alert 46 TLS handshake failure.
   - *True Root Cause*: Internal mTLS cert issued by legacy cert-manager issuer excluded from auto-renewal.
   - *Fix That Worked*: Reissue cert from `corp-ca-v2` issuer and restart envoy sidecars on mesh.
   - *Failed Fix*: Restarting pods (reloaded the same expired cert from the mounted secret).
4. **`feature_flag_5xx_spike`**:
   - *Symptom*: 5xx rate spike, 503 errors in region `eu-west` after config sync.
   - *True Root Cause*: `new-checkout-flow` flag enabled for 100% of traffic before `tax-service v3` deployed in `eu-west`.
   - *Fix That Worked*: Toggle `new-checkout-flow` flag to 0% and deploy `tax-service v3` before re-enabling.
   - *Failed Fix*: Rolling back checkout container image (the flag, not the image, caused the failure).
5. **`kafka_disk_full`**:
   - *Symptom*: `KafkaStorageException: No space left on device` on broker, ISR shrink for partitions.
   - *True Root Cause*: Log retention for `clickstream-raw` topic raised to 30 days by experiment and never reverted.
   - *Fix That Worked*: Lower topic retention to 48 hours and run log cleaner on broker.
   - *Failed Fix*: Restarting the broker (crashed on startup during index recovery).
6. **`dns_resolution_timeouts`**:
   - *Symptom*: `getaddrinfo EAI_AGAIN`, DNS lookup timeouts on internal cluster domains.
   - *True Root Cause*: CoreDNS pods overloaded after kube-dns autoscaler deployment config was removed.
   - *Fix That Worked*: Restore CoreDNS autoscaler deployment and scale replicas to 10.
   - *Failed Fix*: Flushing client DNS cache on worker nodes.
7. **`oomkilled_memory_leak`**:
   - *Symptom*: Pod restarts with exit code 137 (`OOMKilled`), container memory at cgroup limit.
   - *True Root Cause*: Unbounded in-memory bitmap cache in image-resizer service holding onto decompressed buffers.
   - *Fix That Worked*: Enable bounded LRU eviction on image-resizer cache and cap cache size at 512MB.
   - *Failed Fix*: Bumping container memory limits from 2Gi to 4Gi (delayed OOMKill but still crashed).
8. **`third_party_rate_limit`**:
   - *Symptom*: HTTP 429 Too Many Requests from external SendGrid / Stripe endpoints.
   - *True Root Cause*: Email notification queue worker concurrency scaled to 50 without rate limiter.
   - *Fix That Worked*: Add token bucket rate limiter to notification workers capped at 20 req/s.
   - *Failed Fix*: Retrying failed requests immediately (triggered IP block from provider).

---

## 2. `data/hindsight_inference_store.json`

Persistent memory bank storage when using the Hindsight Inference API (`hs-decide-1-flash`). Stores retained documents with unique document IDs, tags, contexts, and content across server restarts.

