---
name: eugene-channel-builder
description: >-
  Build or modify Eugene channels that live in `channels/*.py` and subclass
  `ChannelBase`. Use when Claude needs to add a new integration such as Slack,
  SMS, email, or another messaging transport; update message normalization or
  delivery behavior for an existing Eugene channel; wire channel-specific config
  under `[channels.<name>]` in `eugene.toml`; or verify that a channel publishes
  normalized `Message` events and can send responses back through Eugene.
---

# Eugene Channel Builder

Build Eugene channels by following the repo's existing discovery and runtime conventions instead of inventing a new integration shape.

## Workflow

1. Inspect the current contract before editing:
   Read `src/eugene/core.py` for `ChannelBase`, `src/eugene/models.py` for `Message`, `src/eugene/config.py` for `ChannelConfig`, and one or two existing files in `channels/`.
2. Decide whether the task is:
   Create a brand-new channel.
   Modify an existing channel.
   Extend shared config because the channel needs new credentials beyond `enabled`, `token`, `application_id`, or `webhook_secret`.
3. Implement the channel in `channels/<name>.py`.
4. Add or update config examples in `eugene.toml`.
5. Validate behavior:
   Check startup behavior, message normalization, outbound send behavior, and dependency failure paths.

## Eugene Rules

- Put each channel in its own Python file under `channels/`.
- Define exactly one `ChannelBase` subclass per channel file so Eugene's subclass discovery stays predictable.
- Set `name` to the config key and delivery key the rest of Eugene will use.
- Implement all four required methods: `on_start`, `on_stop`, `normalize`, and `send`.
- Publish inbound messages by calling `self.services.event_bus.publish("message.received", {"message": message.model_dump(mode="json")})` after normalization.
- Normalize external SDK payloads into `eugene.models.Message` with:
  `text`: the user-visible text Eugene should reason over.
  `source_channel`: the channel name.
  `session_id`: the stable conversation/thread/channel identifier Eugene should reply to.
  `attachments`: resolved attachments or an empty list.
  `metadata`: only channel-specific extras needed for downstream behavior.
- Treat missing optional dependencies and missing credentials as startup-time concerns:
  Return early when the channel is not configured.
  Raise a clear `RuntimeError` when the channel is configured but its dependency is not installed.
- Keep `send()` safe to call even if the client/session is unavailable.

## Creation Pattern

Start from `assets/channel_template.py` and adapt it to the target transport.

For most channels:

1. In `on_start`, load `self.services.config.channels.get("<name>")` and return early when the channel is disabled or missing required credentials.
2. Initialize the SDK client, register inbound handlers, and have those handlers call `normalize()` plus `event_bus.publish(...)`.
3. In `normalize`, map the raw transport object into `Message`.
4. In `send`, translate Eugene's `response`, `session_id`, and optional `metadata` back into the transport's outbound API.
5. In `on_stop`, shut down the client cleanly and guard against partially initialized state.

## Config Guidance

- If the channel only needs `enabled`, `token`, `application_id`, or `webhook_secret`, reuse `ChannelConfig` as-is and document the expected `eugene.toml` block.
- If it needs extra fields, update `ChannelConfig` in `src/eugene/config.py` with optional fields that will not break existing channels.
- Mirror environment-variable fallback patterns in `load_config()` when secrets should come from `.env`.
- Keep the runtime config key identical to `ChannelBase.name`.

## Editing Existing Channels

- Preserve the channel's `name` unless the task explicitly includes a migration plan.
- Be careful when changing `session_id` semantics because scheduler delivery, response routing, and conversation memory depend on it being stable.
- If you change `metadata`, confirm nothing downstream assumes the old shape.
- Keep message publishing and response delivery consistent with the existing event flow in `ChannelManager` and `CoreService`.

## Verification Checklist

Use `references/channel-checklist.md` as the final pass. At minimum verify:

- Discovery: the file sits in `channels/` and defines a `ChannelBase` subclass.
- Startup: misconfigured channels no-op cleanly; configured channels fail loudly on missing dependencies.
- Inbound flow: a real or mocked inbound event becomes a valid `Message`.
- Outbound flow: `send()` targets the correct session/thread/channel id.
- Config: `eugene.toml` examples and `.env` fallbacks match the code.

## Output Expectations

When implementing channel work with this skill:

- Make the code changes directly instead of only describing them.
- Keep the final explanation brief and include any dependency or secret the user still needs to provide.
- Mention tests or validation that were run, and call out anything you could not verify locally.
