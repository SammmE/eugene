# Channel Checklist

Use this checklist after creating or modifying a Eugene channel.

## Required Files

- `channels/<name>.py` exists.
- `eugene.toml` includes a `[channels.<name>]` example when the skill added a new channel.
- `src/eugene/config.py` is updated only if the channel truly needs new config fields.

## Channel Contract

- The file defines one `ChannelBase` subclass.
- `name` matches the config key and outbound routing key.
- `on_start`, `on_stop`, `normalize`, and `send` are all implemented.

## Inbound Behavior

- Inbound handlers ignore bot/self messages when the transport supports that distinction.
- `normalize()` returns `Message(...)` from `src/eugene/models.py`.
- `session_id` is stable for the conversation scope Eugene should answer in.
- `attachments` defaults to `[]` when unsupported or absent.
- `metadata` only contains values that downstream code may actually need.
- Inbound handlers publish `message.received` with `message.model_dump(mode="json")`.

## Outbound Behavior

- `send()` is a no-op when the underlying client or session target is unavailable.
- `send()` uses the same `session_id` domain produced by `normalize()`.
- Optional reply/thread metadata is handled consistently if the transport needs it.

## Startup and Shutdown

- Missing credentials cause an early return rather than a broken partial startup.
- Missing Python packages raise a clear `RuntimeError` only when the channel is configured to run.
- `on_stop()` safely handles partially initialized clients.

## Config

- `enabled` behavior matches the rest of Eugene's channels.
- Secret fields can be supplied either in `eugene.toml` or through documented environment fallbacks when appropriate.
- Any added config fields are optional or have sensible defaults so old configs still validate.

## Good Review Questions

- What raw event object does the inbound handler receive?
- What exact id should be used for `session_id`?
- What should Eugene store in `metadata`, if anything?
- Does the transport support attachments, threads, edits, or slash commands that need to be ignored or handled?
