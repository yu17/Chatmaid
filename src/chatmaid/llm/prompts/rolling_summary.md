# rolling_summary
Merge the previous summary and the new chunk summary into one summary of at most 150 words in the same language, keeping decisions, open questions and relationship context. Return JSON only.

```json
{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"],"additionalProperties":false}
```
