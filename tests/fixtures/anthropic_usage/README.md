# Anthropic Usage & Cost report fixtures

**Status: PROVISIONAL — derived from Anthropic's published endpoint documentation, not
from a saved live response.** Replace these files and re-verify the key maps in
`src/eurysx/imports/anthropic_usage.py` (`REPORT_KEYS`, `COST_KEYS`) as soon as a real
response is available. Reader logic (dedupe, cents→USD, completeness flagging,
isolation) does not depend on these key names; only the maps do.

## Sources

| File | Endpoint | Documented parameters |
| --- | --- | --- |
| `usage-report.json` | `GET /v1/organizations/usage_report/messages` | `bucket_width=1d`, `group_by=model` (plus the documented dimension fields), `limit=31` |
| `cost-report.json` | `GET /v1/organizations/cost_report` | `bucket_width=1d`, `group_by=description`, `limit=31` |

Both endpoints paginate with `has_more`/`next_page`, cap at 31 daily buckets, and report
cost as USD cents in decimal strings. `workspace_id: null` is the documented default
workspace, not missing data.

## Logical field → JSON key

| Logical field | Usage report key | Cost report key |
| --- | --- | --- |
| bucket list | `data[]` | `data[]` |
| bucket start | `time_range.start_date` | `time_range.start_date` |
| bucket end | `time_range.end_date` | `time_range.end_date` |
| group results | `results[]` | `results[]` |
| model | `model` | `description.model` |
| workspace | `workspace_id` | `workspace_id` |
| service tier | `service_tier` | — |
| context window | `context_window` | — |
| inference geo | `inference_geo` | `description.inference_geo` |
| speed | `speed` | — |
| uncached input | `iterations.input_tokens` | — |
| cached input | `iterations.cache_read_input_tokens` | — |
| cache creation | `iterations.cache_creation_input_tokens` | — |
| output | `iterations.output_tokens` | — |
| cost (cents) | — | `cost` |
| cost type | — | `type` |

## Sanitization

All identifiers are synthetic (`ws-1`, no account or API-key ids, no organization id).
No field carries content, prompts, responses, tool data, rate limits, or credits. Token
counts and cent strings were scaled down but keep their documented types: integers for
tokens, decimal strings for cents.
