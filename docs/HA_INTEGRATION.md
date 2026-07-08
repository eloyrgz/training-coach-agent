# Home Assistant integration for training-coach-agent

This directory contains a minimal custom component to call the training coach agent from Home Assistant.

Installation
1. Copy the `custom_components/training_coach` directory into your Home Assistant configuration folder under `config/custom_components/`.
2. Add the configuration to your `configuration.yaml` as shown below.
3. Restart Home Assistant.

Example configuration.yaml

```yaml
training_coach:
  agent_url: "https://your-agent.example.com/assistant"  # required: your agent HTTP endpoint
  agent_secret: "LONG_RANDOM_SECRET"                     # optional: Bearer token used by integration
  tts_entity: "tts.google_en_com"                        # optional: preferred TTS entity for tts.speak

input_text:
  coach_query:
    name: Coach Query
    initial: ""
    max: 512
```

Example automation (automations.yaml)

```yaml
- id: training_coach_on_input_change
  alias: Training Coach - run on coach_query change
  trigger:
    platform: state
    entity_id: input_text.coach_query
  condition:
    - condition: template
      value_template: "{{ trigger.to_state.state | length > 0 }}"
  action:
    - service: training_coach.query
      data:
        text: "{{ states('input_text.coach_query') }}"
        media_player: media_player.living_room_speaker  # change to your speaker entity
    - service: input_text.set_value
      data:
        entity_id: input_text.coach_query
        value: ""   # clear after processing (optional)
```

Notes
- The component expects your agent to accept POST { "text": "..." } and return JSON { "reply": "..." } or plain text.
- For voice playback, the integration prefers modern `tts.speak`. If `tts_entity` is omitted, it auto-selects the first available `tts.*` entity.
- If the selected `media_player` is missing or unavailable, the integration skips TTS and posts a persistent notification instead.
- Use HTTPS for the agent_url and verify the `agent_secret` Bearer token on the agent side.
- If Home Assistant and your agent run in the same Python environment, you can modify the component to import the agent directly instead of calling over HTTP.
