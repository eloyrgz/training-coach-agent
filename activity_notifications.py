import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


logger = logging.getLogger(__name__)


def build_rpe_keyboard(activity_id: str) -> InlineKeyboardMarkup:
    rows = []
    for start in (1, 6):
        rows.append([
            InlineKeyboardButton(
                str(rpe),
                callback_data=f"rpe:{activity_id}:{rpe}",
            )
            for rpe in range(start, start + 5)
        ])
    if any(
        len(button.callback_data.encode("utf-8")) > 64
        for row in rows
        for button in row
    ):
        raise ValueError("Activity ID is too long for Telegram callback data.")
    return InlineKeyboardMarkup(rows)


def build_activity_keyboard(activity_id: str) -> InlineKeyboardMarkup:
    keyboard = [list(row) for row in build_rpe_keyboard(activity_id).inline_keyboard]
    keyboard.extend(build_note_keyboard(activity_id).inline_keyboard)
    return InlineKeyboardMarkup(keyboard)


def build_note_keyboard(activity_id: str) -> InlineKeyboardMarkup:
    note_callback = f"note:{activity_id}"
    if len(note_callback.encode("utf-8")) > 64:
        raise ValueError("Activity ID is too long for Telegram callback data.")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Añadir nota", callback_data=note_callback)]
    ])


def format_activity_summary(activity: dict) -> str:
    name = activity.get("activity_name") or "Entrenamiento"
    activity_type = activity.get("activity_type") or "Actividad"
    activity_date = activity.get("activity_date") or ""
    lines = [f"{name}", f"{activity_type} · {activity_date[:16].replace('T', ' ')}"]

    distance = activity.get("distance_meters") or 0
    if distance > 0:
        lines.append(f"Distancia: {distance / 1000:.2f} km")

    duration = activity.get("moving_time_seconds") or 0
    if duration > 0:
        hours, remainder = divmod(int(duration), 3600)
        minutes = remainder // 60
        duration_text = f"{hours} h {minutes} min" if hours else f"{minutes} min"
        lines.append(f"Duración: {duration_text}")

    elevation = activity.get("elevation_gain_meters") or 0
    if elevation > 0:
        lines.append(f"Desnivel: {elevation:.0f} m")

    load = activity.get("icu_load")
    if load is not None:
        lines.append(f"Carga: {load}")

    return "\n".join(lines)


class ActivityNotificationWorker:
    def __init__(self, memory, bot, chat_id: int, sync_missing_activity=None):
        self.memory = memory
        self.bot = bot
        self.chat_id = chat_id
        self.sync_missing_activity = sync_missing_activity

    async def discover_recent_activities(self) -> None:
        try:
            if self.sync_missing_activity:
                await asyncio.to_thread(self.sync_missing_activity)
            activity_ids = await asyncio.to_thread(
                self.memory.get_activities_without_events, 2
            )
            for activity_id in activity_ids:
                await asyncio.to_thread(self.memory.enqueue_activity_event, activity_id)
        except Exception:
            logger.exception("Could not discover recent activities for notification")

    async def process_one(self) -> bool:
        event = await asyncio.to_thread(self.memory.claim_next_activity_event)
        if not event:
            return False

        activity_id = event["activity_id"]
        try:
            activity = await asyncio.to_thread(self.memory.get_activity_by_id, activity_id)
            if not activity and self.sync_missing_activity:
                await asyncio.to_thread(self.sync_missing_activity)
                activity = await asyncio.to_thread(self.memory.get_activity_by_id, activity_id)
            if not activity:
                raise RuntimeError(f"Activity {activity_id} is not available in training_metrics")

            if (activity.get("moving_time_seconds") or 0) < 300:
                if not await asyncio.to_thread(
                    self.memory.complete_activity_event, activity_id, "done"
                ):
                    raise RuntimeError("Could not mark short activity as complete")
                return True

            summary = format_activity_summary(activity)
            message = await self.bot.send_message(
                chat_id=self.chat_id,
                text=summary,
                reply_markup=build_activity_keyboard(activity_id),
            )
            delivered = await asyncio.to_thread(
                self.memory.complete_activity_event,
                activity_id,
                "awaiting_feedback",
                summary,
                self.chat_id,
                message.message_id,
            )
            if not delivered:
                raise RuntimeError("Telegram sent the message but delivery state was not persisted")
            return True
        except Exception as error:
            attempts = event.get("attempts", 1)
            delay_seconds = min(60 * (2 ** max(0, attempts - 1)), 3600)
            await asyncio.to_thread(
                self.memory.retry_activity_event,
                activity_id,
                str(error),
                delay_seconds,
            )
            logger.exception("Could not deliver activity notification %s", activity_id)
            return True

    async def run(
        self,
        stop_event: asyncio.Event,
        poll_seconds: float = 10,
        sync_interval_seconds: float = 900,
    ) -> None:
        loop = asyncio.get_running_loop()
        last_sync = 0.0
        while not stop_event.is_set():
            if loop.time() - last_sync >= sync_interval_seconds:
                await self.discover_recent_activities()
                last_sync = loop.time()
            await self.process_one()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass