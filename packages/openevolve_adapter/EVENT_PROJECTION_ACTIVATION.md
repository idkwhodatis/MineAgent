# Controller projection integration checklist (disabled draft)

New modules: `event_projection.py` and backend `openevolve_event_projection.py`.
They parse/project only sampling intents and terminal applications. Other event
kinds, including resume, fail closed. There is no listener, dispatcher, registration,
object writer or success ACK. Parsed DTOs and evidence hashes are not authority.

## Owner integration interfaces

1. Authenticate the accepted Unix socket with existing SO_PEERCRED/membership and
   ticket verification. Keep the actual AuthenticatedPeer, never reconstruct one
   from payload UUIDs. For each message, inside the same locked neutral transaction,
   call the draft inspector, which repeats `require_running_peer` using the exact
   epoch admission's **persisted launch logical operation ID**. Bind full epoch
   scope/spec/resolved profile/image/bootstrap through `_locked_epoch`.
2. Supply the closed host-configured process profile, not a request parameter.
   Controller events additionally require the authenticated peer identity to equal
   the committed leader identity. This works only for a direct-Python controller
   leader. A deadline wrapper leader would fail closed and require a separately
   committed controller PID/start identity; descendant membership alone is never
   controller authority. Worker events need separate exact intent/ticket binding.
3. Resolve selected native parent ID to the exact epoch candidate/source tuple.
   Obtain the already-charged immutable `engine_mutation` reservation (one slot)
   from host allocation, never trust a reservation supplied by the engine. Resolve
   seed objective ACK or mutation worker outcome from exact epoch/iteration rows.
4. Publish `EvidenceProjection.content` through the existing bounded research
   object reservation/write/finalization lifecycle with `object_storage` budget.
   The current object-kind registry must be extended revision-specifically for
   `search_context` and `search_population` and their closed codecs; do not widen
   revision015 archives. Identity/hash checks are repeated when forming commands.
5. In one current fenced transaction invoke `register_sampling_intent` or
   `acknowledge_terminal_application` with finalized evidence references and
   host-resolved identities. No network I/O spans SQL locks. Reauthenticate when
   entering any later transaction after object I/O. Require exact immutable replay
   semantics if publication/commit outcomes are uncertain; no replacement intent.
6. Persist an event-envelope receipt mapping exact admission/epoch/event SHA to
   the immutable resulting domain receipt. It must be scoped, append-only and
   included in backup/restore closure. No generic JSON dispatch escape. Only after
   commit emit the existing event-result envelope; seed/mutation ACK contents must
   match runtime expectations exactly. Never acknowledge a `flush()`.

Current production neutral identity/process registry intentionally rejects OE;
these modules do not change that. Required acceptance remains authenticated socket
+ PostgreSQL transaction/object budget tests, stale-fence/replay/crash/backup
closure and actual hosted engine loop under the fixed process profile. Pure tests
with patched peer APIs are wiring tests, not authentication acceptance.
