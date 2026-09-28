# Initial evaluation: repetitive generation dominates the tail

One original-model sample is still inside the same generation call after more than three hours. It is repeatedly emitting a Django application list. The request is actively producing tokens; it is not a lost RPC.

| Observation | Value |
|---|---:|
| Sample index | 96 |
| Prompt tokens at this call | 10,680 |
| Generated tokens at21:05UTC | 34,532 |
| Maximum new tokens for this call | 120,937 |
| Recent generation rate | About2tokens/second |
| Exact repeated pattern | 61tokens |
| Confirmed consecutive repeats | At least16 |

If the pattern persists until the configured token limit, the remaining generation may take roughly12hours at the observed rate. This single sample prevents the initial evaluation batch from completing.

The user has been asked whether to add a repetition termination rule: at least4,096generated tokens and16consecutive copies of the same long token fragment. A terminated attempt would still be verified against its current patch and would not be resampled for a low score. Total128Kbudget,200turns,batch64,n8 remain unchanged, but this adds a stopping rule beyond the original configuration.

**The rule is not enabled.** The original termination policy continues while that preference is pending.

A read-only audit covered370completed samples and14,566generation calls. Four calls exceeded4Ktokens. None matched the candidate rule, including every successful repair. The current repetitive request does match it. This describes the observed sample paths; it does not establish that the rule is harmless on all future tasks.

Evidence: runtime `results/live-inference-diagnostic.json`, `results/live-repetition-audit.json`, and `results/repetition-rule-audit.json`. The candidate detector is isolated in `code/repetition_guard.py` and is not installed in the inference or training path.
