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

    if not agent_url:
        _LOGGER.warning("training_coach: 'agent_url' not configured in configuration.yaml")

    def handle_query(call: ServiceCall) -> None:
        text = call.data.get("text", "").strip()
        media_player = call.data.get("media_player")
        if not text:
            _LOGGER.warning("training_coach.query called with empty text")
            return

        headers = {"Content-Type": "application/json"}
        if agent_secret:
            headers["Authorization"] = f"Bearer {agent_secret}"

        reply = None
        try:
            resp = requests.post(agent_url, json={"text": text}, headers=headers, timeout=15)
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
            hass.services.call("tts", "google_translate_say", {
                "entity_id": media_player,
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
