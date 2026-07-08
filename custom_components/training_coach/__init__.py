"""Minimal Training Coach custom component for Home Assistant.

Services:
 - training_coach.query
   data:
     text: string (required)
     media_player: entity_id (optional)
"""
import logging
import requests
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "training_coach"
SERVICE_QUERY = "query"


def setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config.get(DOMAIN, {}) if config else {}
    agent_url = conf.get("agent_url")
    agent_secret = conf.get("agent_secret")
    configured_tts_entity = conf.get("tts_entity")

    if not agent_url:
        _LOGGER.warning("training_coach: 'agent_url' not configured in configuration.yaml")

    def resolve_tts_entity() -> str | None:
        if configured_tts_entity:
            if hass.states.get(configured_tts_entity):
                return configured_tts_entity
            _LOGGER.warning(
                "training_coach: configured tts_entity '%s' was not found",
                configured_tts_entity,
            )

        tts_entities = sorted(state.entity_id for state in hass.states.async_all("tts"))
        if tts_entities:
            return tts_entities[0]

        return None

    def handle_query(call: ServiceCall) -> None:
        text = call.data.get("text", "").strip()
        media_player = call.data.get("media_player")
        if not text:
            _LOGGER.warning("training_coach.query called with empty text")
            return

        if media_player:
            media_player_state = hass.states.get(media_player)
            if not media_player_state or media_player_state.state in {"unavailable", "unknown"}:
                _LOGGER.warning(
                    "training_coach: media_player '%s' is missing or unavailable. Falling back to persistent_notification.",
                    media_player,
                )
                media_player = None

        headers = {"Content-Type": "application/json"}
        if agent_secret:
            headers["Authorization"] = f"Bearer {agent_secret}"

        reply = None
        try:
            # FastAPI /chat expects `message`; keep `text` for compatibility if backend ignores extras.
            payload = {"message": text, "text": text, "conversation_id": "ha_main"}
            resp = requests.post(agent_url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            try:
                j = resp.json()
                reply = j.get("reply") or j.get("text") or str(j)
            except ValueError:
                reply = resp.text or "Sorry, no reply."
        except Exception:
            _LOGGER.exception("Error contacting agent endpoint")
            reply = "Sorry, I couldn't reach the training coach."

        # If a media_player is provided, send TTS; otherwise create a persistent notification
        if media_player:
            if hass.services.has_service("tts", "speak"):
                tts_entity = resolve_tts_entity()
                if tts_entity:
                    hass.services.call("tts", "speak", {
                        "entity_id": tts_entity,
                        "media_player_entity_id": media_player,
                        "message": reply
                    })
                elif hass.services.has_service("tts", "google_translate_say"):
                    _LOGGER.warning(
                        "training_coach: no TTS entity found for tts.speak. Using legacy tts.google_translate_say."
                    )
                    hass.services.call("tts", "google_translate_say", {
                        "entity_id": media_player,
                        "message": reply
                    })
                else:
                    _LOGGER.warning(
                        "training_coach: tts.speak is available but no TTS entity exists. Falling back to persistent_notification."
                    )
                    hass.services.call("persistent_notification", "create", {
                        "title": "Training Coach reply",
                        "message": reply
                    })
            elif hass.services.has_service("tts", "google_translate_say"):
                hass.services.call("tts", "google_translate_say", {
                    "entity_id": media_player,
                    "message": reply
                })
            else:
                _LOGGER.warning(
                    "training_coach: no compatible TTS service found. Falling back to persistent_notification."
                )
                hass.services.call("persistent_notification", "create", {
                    "title": "Training Coach reply",
                    "message": reply
                })
        else:
            hass.services.call("persistent_notification", "create", {
                "title": "Training Coach reply",
                "message": reply
            })

    hass.services.register(DOMAIN, SERVICE_QUERY, handle_query)
    _LOGGER.info("training_coach service registered (agent_url=%s)", agent_url)
    return True
