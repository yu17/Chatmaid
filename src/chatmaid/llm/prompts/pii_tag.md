# pii_tag
List the personal names of people mentioned in this text, exactly as written in the text. Include given names, family names and full names; leave out organizations, places and products. JSON only.

```json
{"type":"object","properties":{"persons":{"type":"array","items":{"type":"string"}}},"required":["persons"],"additionalProperties":false}
```
