# Interactive

Grace's text conversation engine. Interactive assembles the system prompt and
recent history, calls an OpenAI-compatible chat endpoint, streams response
events, and persists completed exchanges through the supplied Memory API.

## Component boundary

- **Shell** owns process lifecycle, system configuration, and runtime wiring.
- **Telepathy** owns the user-facing console and input/output transport.
- **Interactive** owns text-turn processing and chat-model requests.
- **Memory** owns conversation and memory storage APIs.

The package is independent of ROS. Shell's ROS adapter supplies configuration,
Memory, a logger, and an event publisher. Interactive does not own database
schemas, user-interface transports, model hosting, tool execution, Canvas, or
image generation.

## Current turn behavior

`ConversationEngine.answer(text, identifier)`:

1. Handles the existing local time/date responses.
2. Builds model context from the supplied system prompt, local time, recent
   conversation history, and the current user message.
3. Streams `start`, `delta`, `done`, or `error` events to the supplied callback.
4. Persists a completed user/assistant exchange through the supplied Memory
   object and reports provider token/latency metrics when available.

The event shape remains compatible with Shell's current Telepathy WebUI stream.
The Memory object is expected to provide `recent()` and `add_exchange()`.

## Install

For the AGi local workspace layout, Shell installs this package from the
neighboring checkout using `pip install -r requirements.txt`:

```sh
cd AIVA
git clone https://github.com/AGi-robot/interactive.git interactive
.venv/bin/python -m pip install -r requirements.txt
```

## License and origin

The Python distribution is `agi-interactive`, imported as `agi_interactive`.

This repository is AGPL-3.0-only. The conversation-turn implementation was
extracted and adapted from the text-chat path in
`AGi-robot/shell`'s `aiva_chat` ROS adapter. Canvas and Imaging code remain
outside this package.

## Optional expressive lighting

Install the matching `Project-AGi/toolbox` lighting client in Grace's environment, run the matching Console OpenRGB API, and set `AGI_LIGHTING_CONSOLE_URL=http://127.0.0.1:8853` in the process hosting Interactive. Hosts can instead pass `lighting_url` to `ConversationEngine`; an empty string disables it.

Chat requests then offer `set_lighting`, `lighting_status`, and `stop_lighting` with automatic tool choice. The model chooses freely whether to compose an effect; no mood, holiday or activity rules are injected. Tool arguments are assembled from the response stream and executed only after a complete tool-call finish. Up to three tool rounds precede the final streamed reply; errors become tool results. Existing memory keeps the user-visible exchange, not raw tool messages. Token metrics describe the final request; latency covers the full turn.

See [Console's lighting contract](https://github.com/Project-AGi/console/blob/dev/LIGHTING.md) for colours, transitions, brightness, rhythms and stop/expiry behavior. This does not introduce idle triggers or a scheduler. Use a model endpoint that supports OpenAI-compatible tool calls.

The lighting tools also include `lighting_catalog` and `save_lighting_preset`. Grace can expand the runtime Markdown catalogue with named colours and complete effects (including programmable brightness rhythms and Morse messages) without changing code. Saving is separate from starting an effect. Files and limits are described in Console's lighting guide.
