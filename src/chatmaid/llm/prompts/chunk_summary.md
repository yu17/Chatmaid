# chunk_summary
You summarize one chunk of a conversation for the owner's private assistant and classify it. A "Sender context" line precedes the messages. Write in the conversation's main language. Return JSON only. Never invent details.

category, exactly one of: "conversation" (a person wrote to the owner or a group and an exchange is expected or under way), "event" (announces or invites to a dated happening: seminar, contest round, meeting, deadline, call for applications), "transactional" (receipts, invoices, confirmations, shipping, account and security notices about the owner), "newsletter" (periodic editorial content from an organisation: news digests, weekly bulletins), "promotional" (marketing, offers, advertising, recruitment and admissions marketing), "system" (automated machinery: bounces, calendar sync, ticket updates, password resets).
summary: a neutral account of what was discussed and decided; at most 120 words for conversation and event, at most 25 words for every other category.
events: dated happenings worth remembering, only for conversation and event; otherwise an empty list.
facts: stable facts about the people the owner deals with (roles, relationships, preferences, commitments), only for conversation and never about organisations or products; otherwise an empty list.

```json
{"type":"object","properties":{"category":{"type":"string","enum":["conversation","event","transactional","newsletter","promotional","system"]},"summary":{"type":"string"},"events":{"type":"array","items":{"type":"object","properties":{"date":{"type":"string"},"text":{"type":"string"}},"required":["date","text"],"additionalProperties":false}},"facts":{"type":"array","items":{"type":"object","properties":{"about":{"type":"string"},"text":{"type":"string"}},"required":["about","text"],"additionalProperties":false}}},"required":["category","summary","events","facts"],"additionalProperties":false}
```
