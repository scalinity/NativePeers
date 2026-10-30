# Antigravity stream fixtures

`documented-stream.jsonl` is copied from the Antigravity CLI's headless documentation (`agy -p … --output-format
stream-json`), captured in the runtime-approvals archive for issue 4. The lines are the documentation's own examples,
not a recording of an owned worker: no recorded worker stream exists yet. They fix the documented shapes (`init`,
`step_update` with `agent_response`, `tool` and `checkpoint` steps, `result`) that the normalizer maps.
