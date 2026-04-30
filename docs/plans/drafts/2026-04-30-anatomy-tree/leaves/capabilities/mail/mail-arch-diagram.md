# Mail Architecture — Living Diagram

> Auto-generated from source by `mail-arch-diagram.py`.
> Re-run: `python3 mail-arch-diagram.py src/ > mail-arch-diagram.md`

## Diagram

```mermaid
flowchart TD
    subgraph capability["Capability Layer<br/><i>EmailManager — cc/__init__.py</i>"]
        direction TB
        capability_EmailManager["<b>EmailManager</b><br/><code>start_scheduler</code> :245<br/><code>stop_scheduler</code> :257<br/><code>_load_email</code> :276<br/><code>_list_emails</code> :306<br/><code>_email_summary</code> :333<br/><code>_inject_identity</code> :369<br/><code>handle</code> :391<br/><code>_handle_schedule</code> :430"]
        capability__coerce_address_list["<code>_coerce_address_list</code> :41<br/><i>Normalize an address arg into a clean list[str].</i>"]
        capability__preview["<code>_preview</code> :69"]
        capability__email_time["<code>_email_time</code> :76<br/><i>Extract the best timestamp from an email dict for filtering.</i>"]
        capability_get_description["<code>get_description</code> :82"]
        capability_get_schema["<code>get_schema</code> :86"]
        capability_setup["<code>setup</code> :1294<br/><i>Set up email capability — filesystem-based mailbox.</i>"]
    
    subgraph intrinsic["Intrinsic Layer<br/><i>intrinsics/mail.py</i>"]
        direction TB
        intrinsic_get_description["<code>get_description</code> :22"]
        intrinsic_mode_field["<code>mode_field</code> :27<br/><i>Schema field for the address-mode parameter.</i>"]
        intrinsic_get_schema["<code>get_schema</code> :42"]
        intrinsic_handle["<code>handle</code> :95<br/><i>Handle mail tool — dispatch to action handler.</i>"]
        intrinsic__mailbox_dir["<code>_mailbox_dir</code> :114<br/><i>Return the mailbox root directory.</i>"]
        intrinsic__inbox_dir["<code>_inbox_dir</code> :119<br/><i>Return the inbox directory.</i>"]
        intrinsic__load_message["<code>_load_message</code> :124<br/><i>Load a single message by ID, or None if not found.</i>"]
        intrinsic__list_inbox["<code>_list_inbox</code> :135<br/><i>List all inbox messages, sorted newest first (by received...</i>"]
        intrinsic__read_ids_path["<code>_read_ids_path</code> :157<br/><i>Path to the read.json tracking file.</i>"]
        intrinsic__read_ids["<code>_read_ids</code> :162<br/><i>Load set of read message IDs from read.json.</i>"]
        intrinsic__save_read_ids["<code>_save_read_ids</code> :174<br/><i>Atomically write read IDs to read.json.</i>"]
        intrinsic__mark_read["<code>_mark_read</code> :184<br/><i>Mark a message as read.</i>"]
        intrinsic__summary_to_list["<code>_summary_to_list</code> :191<br/><i>Best-effort coercion of to/cc for display.</i>"]
        intrinsic__message_summary["<code>_message_summary</code> :206<br/><i>Build a summary dict for check output.</i>"]
        intrinsic__is_self_send["<code>_is_self_send</code> :233<br/><i>Check if the address matches this agent (by directory nam...</i>"]
        intrinsic__persist_to_inbox["<code>_persist_to_inbox</code> :248<br/><i>Persist a message directly to mailbox/inbox/{uuid}/messag...</i>"]
        intrinsic__outbox_dir["<code>_outbox_dir</code> :267<br/><i>Return the outbox directory.</i>"]
        intrinsic__sent_dir["<code>_sent_dir</code> :272<br/><i>Return the sent directory.</i>"]
        intrinsic__persist_to_outbox["<code>_persist_to_outbox</code> :277<br/><i>Write a message to outbox/{uuid}/message.json. Returns th...</i>"]
        intrinsic__move_to_sent["<code>_move_to_sent</code> :295<br/><i>Move outbox/{uuid}/ → sent/{uuid}/, enriching with sent_a...</i>"]
        intrinsic__mailman["<code>_mailman</code> :317<br/><i>Daemon thread — one per message. Waits, dispatches, archi...</i>"]
        intrinsic__send["<code>_send</code> :378<br/><i>Send a message — validate, write to outbox, spawn mailman.</i>"]
        intrinsic__check["<code>_check</code> :428<br/><i>List inbox summaries with unread flags.</i>"]
        intrinsic__read["<code>_read</code> :448<br/><i>Load full message(s) by ID, mark as read.</i>"]
        intrinsic__search["<code>_search</code> :471<br/><i>Regex search across from/subject/message fields.</i>"]
        intrinsic__delete["<code>_delete</code> :498<br/><i>Remove message(s) from disk and clean read tracking.</i>"]
    
    subgraph transport["Transport Layer<br/><i>services/mail.py</i>"]
        direction TB
        transport_MailService["<b>MailService</b><br/><code>send</code> :37<br/><code>listen</code> :61<br/><code>stop</code> :70<br/><code>address</code> :76"]
        transport_FilesystemMailService["<b>FilesystemMailService</b><br/><code>address</code> :123<br/><code>send</code> :131<br/><code>listen</code> :215<br/><code>stop</code> :369"]
    
    subgraph handshake["Handshake<br/><i>handshake.py</i>"]
        direction TB
        handshake_resolve_address["<code>resolve_address</code> :15<br/><i>Resolve an agent address to an absolute Path.</i>"]
        handshake_is_agent["<code>is_agent</code> :27<br/><i>Check if an agent exists at *path* (has .agent.json).</i>"]
        handshake_is_human["<code>is_human</code> :32<br/><i>Check if the agent at *path* is a human (admin key explic...</i>"]
        handshake_is_alive["<code>is_alive</code> :41<br/><i>Check if the agent at *path* has a fresh heartbeat.</i>"]
        handshake_manifest["<code>manifest</code> :60<br/><i>Read and return .agent.json contents.</i>"]
    

    %% === SEND PATH ===
    capability__send --> capability__persist_to_outbox
    capability__persist_to_outbox --> intrinsic__mailman
    intrinsic__mailman --> intrinsic__is_self_send
    intrinsic__is_self_send --> intrinsic__persist_to_inbox
    intrinsic__persist_to_inbox --> intrinsic__move_to_sent
    intrinsic__move_to_sent --> transport_send
    transport_send --> handshake_resolve_address
    handshake_resolve_address --> handshake_is_agent
    handshake_is_agent --> handshake_is_alive

    %% === DATA STORES ===
    inbox[("📬 inbox/{uuid}/msg.json")]
    outbox[("📦 outbox/{uuid}/msg.json")]
    sent[("📤 sent/{uuid}/msg.json")]
    schedules[("📅 schedules/{id}/schedule.json")]
    readjson[("👁 read.json")]
    contacts[("📇 contacts.json")]
    capability_EmailManager --> outbox
    outbox --> intrinsic__mailman
    transport_send --> inbox
    intrinsic__mailman --> sent
    intrinsic__mailman --> inbox
    capability_EmailManager --> sent
    capability_EmailManager --> schedules
    capability_EmailManager --> contacts
    intrinsic__mark_read --> readjson

    %% === STYLING ===
    classDef capability fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
    classDef intrinsic fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    classDef transport fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    classDef data fill:#fce4ec,stroke:#c62828,stroke-width:2px,stroke-dasharray:5
    classDef handshake fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
```

## Stats

| Layer | File | Classes | Functions/Methods |
|-------|------|---------|-------------------|
| Capability Layer | `lingtai/core/email/__init__.py` | 1 | 43 |
| Intrinsic Layer | `lingtai_kernel/intrinsics/mail.py` | 0 | 26 |
| Transport Layer | `lingtai_kernel/services/mail.py` | 2 | 10 |
| Handshake | `lingtai_kernel/handshake.py` | 0 | 5 |

**Total:** 84 functions/methods across 4 source files.

## Source

| File | Lines |
|------|-------|
| `lingtai/core/email/__init__.py` | 1-1292 |
| `lingtai_kernel/intrinsics/mail.py` | 1-? |
| `lingtai_kernel/services/mail.py` | 1-374 |
| `lingtai_kernel/handshake.py` | 1-? |

