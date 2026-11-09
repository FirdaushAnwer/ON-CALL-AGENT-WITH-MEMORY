# Test Suite

This directory contains automated unit and integration tests for the On-Call Memory Agent.

---

## Running Tests

```bash
# Run full test suite with verbose output:
python -m pytest tests/ -v

# Run with short output:
make test
```

---

## Test Modules

### 1. `tests/test_flow.py`
Tests the complete incident triage and feedback lifecycle with mocked LLM and memory:
- **`test_triage_recalls_then_retains`**: Verifies that triage calls `recall()` before LLM completion and `retain()` afterwards with unconfirmed tag.
- **`test_memory_off_skips_recall_and_retain`**: Confirms that when memory is OFF (`?memory=off`), recall and retain are completely bypassed for an honest baseline comparison.
- **`test_feedback_retains_correction_and_failed_fix`**: Verifies that engineer corrections and failed fixes are properly structured and stored under dedicated feedback document IDs.
- **`test_feedback_unknown_incident_404`**: Ensures 404 response when providing feedback on non-existent incident IDs.
- **`test_recall_failure_is_a_warning_not_a_crash`**: Proves that Hindsight timeouts or errors gracefully produce warnings without crashing triage.
- **`test_retain_failure_is_a_warning_not_a_crash`**: Proves that retain failures do not break the response.
- **`test_llm_failure_returns_labelled_safe_default`**: Confirms safe default response when all LLM attempts fail.
- **`test_bad_json_then_fallback_model`**: Tests retry logic on invalid JSON output and failover to `qwen/qwen3-32b`.
- **`test_prompt_contains_memories`**: Asserts that recalled memories are injected directly into the LLM prompt.
- **`test_judge`**: Validates the evaluation matcher against positive and negative ground truth.
- **`test_health_and_samples`**: Asserts `/health` and `/samples` endpoints respond with expected payloads.

### 2. `tests/test_inference_memory.py`
Tests Hindsight Inference decision engine integration:
- **`test_hindsight_inference_retain_and_recall`**: Tests semantic reranking with `hs-decide-1-flash`, document ordering by relevance score, and parameter validation.
- **`test_hindsight_inference_empty_bank`**: Validates cold-start handling on unpopulated banks.

