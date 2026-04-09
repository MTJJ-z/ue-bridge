import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import orjson
import paho.mqtt.client as mqtt
from websockets.server import WebSocketServerProtocol, serve


def json_dumps(data: Any) -> str:
    return orjson.dumps(data).decode("utf-8")


def parse_topics(raw: str) -> List[str]:
    raw = (raw or "").strip()
    if not raw:
        return ["ue/#"]
    try:
        data = orjson.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        pass
    return ["ue/#"]


@dataclass
class Settings:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    mqtt_topics: List[str]
    ws_host: str
    ws_port: int
    auth_token: str
    log_level: str


class UEMQTTBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.clients: Set[WebSocketServerProtocol] = set()
        self.clients_lock = asyncio.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stop_event = asyncio.Event()
        self.mqtt_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.settings.mqtt_client_id,
            clean_session=True,
        )
        if self.settings.mqtt_username:
            self.mqtt_client.username_pw_set(
                self.settings.mqtt_username,
                self.settings.mqtt_password or None,
            )

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.install_signal_handlers()
        self.connect_mqtt()

        async with serve(
            self.ws_handler,
            self.settings.ws_host,
            self.settings.ws_port,
            ping_interval=20,
            ping_timeout=20,
            max_size=2 * 1024 * 1024,
        ):
            logging.info(
                "WebSocket server listening on ws://%s:%s",
                self.settings.ws_host,
                self.settings.ws_port,
            )
            await self.stop_event.wait()

        logging.info("Shutting down bridge")
        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        except Exception:
            logging.exception("Failed to stop MQTT client cleanly")

    def install_signal_handlers(self) -> None:
        assert self.loop is not None
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self.loop.add_signal_handler(sig, self.stop_event.set)
            except NotImplementedError:
                pass

    def connect_mqtt(self) -> None:
        logging.info(
            "Connecting to MQTT broker %s:%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.mqtt_client.connect_async(
            self.settings.mqtt_host,
            self.settings.mqtt_port,
            keepalive=60,
        )
        self.mqtt_client.loop_start()

    async def ws_handler(self, websocket: WebSocketServerProtocol) -> None:
        client = self.describe_client(websocket)
        try:
            authed = await self.authenticate_client(websocket)
            if not authed:
                return

            async with self.clients_lock:
                self.clients.add(websocket)

            logging.info("UE client connected: %s", client)
            await websocket.send(json_dumps({
                "type": "bridge_status",
                "status": "connected",
                "server_time": datetime.now(timezone.utc).isoformat(),
                "subscribed_topics": self.settings.mqtt_topics,
            }))

            async for message in websocket:
                await self.handle_ws_message(websocket, message)

        except Exception:
            logging.exception("WebSocket error for client %s", client)
        finally:
            async with self.clients_lock:
                self.clients.discard(websocket)
            logging.info("UE client disconnected: %s", client)

    async def authenticate_client(self, websocket: WebSocketServerProtocol) -> bool:
        if not self.settings.auth_token:
            return True

        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
        except asyncio.TimeoutError:
            await websocket.send(json_dumps({
                "type": "auth_result",
                "ok": False,
                "reason": "timeout",
            }))
            await websocket.close(code=4001, reason="auth timeout")
            return False

        try:
            data = orjson.loads(raw)
        except Exception:
            await websocket.send(json_dumps({
                "type": "auth_result",
                "ok": False,
                "reason": "invalid_json",
            }))
            await websocket.close(code=4002, reason="invalid auth payload")
            return False

        if data.get("action") != "auth" or data.get("token") != self.settings.auth_token:
            await websocket.send(json_dumps({
                "type": "auth_result",
                "ok": False,
                "reason": "invalid_token",
            }))
            await websocket.close(code=4003, reason="invalid token")
            return False

        await websocket.send(json_dumps({
            "type": "auth_result",
            "ok": True,
        }))
        return True

    async def handle_ws_message(self, websocket: WebSocketServerProtocol, message: Any) -> None:
        try:
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            data = orjson.loads(message)
        except Exception:
            await websocket.send(json_dumps({
                "type": "error",
                "error": "invalid_json",
            }))
            return

        action = data.get("action")
        if action == "publish":
            topic = str(data.get("topic", "")).strip()
            if not topic:
                await websocket.send(json_dumps({
                    "type": "error",
                    "error": "missing_topic",
                }))
                return

            payload = data.get("payload")
            qos = int(data.get("qos", 0))
            retain = bool(data.get("retain", False))

            if isinstance(payload, (dict, list)):
                mqtt_payload = json_dumps(payload)
            elif payload is None:
                mqtt_payload = ""
            else:
                mqtt_payload = str(payload)

            info = self.mqtt_client.publish(topic, mqtt_payload, qos=qos, retain=retain)
            await websocket.send(json_dumps({
                "type": "publish_result",
                "ok": info.rc == mqtt.MQTT_ERR_SUCCESS,
                "topic": topic,
                "mid": info.mid,
            }))
            return

        if action == "ping":
            await websocket.send(json_dumps({
                "type": "pong",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
            return

        await websocket.send(json_dumps({
            "type": "error",
            "error": "unsupported_action",
        }))

    def on_mqtt_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], reason_code: Any, properties: Any) -> None:
        if getattr(reason_code, "value", reason_code) != 0:
            logging.error("MQTT connect failed: %s", reason_code)
            return

        logging.info("Connected to MQTT broker")
        for topic in self.settings.mqtt_topics:
            result, mid = client.subscribe(topic)
            if result == mqtt.MQTT_ERR_SUCCESS:
                logging.info("Subscribed MQTT topic: %s", topic)
            else:
                logging.error("Failed to subscribe topic %s, rc=%s", topic, result)

    def on_mqtt_disconnect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        logging.warning("MQTT disconnected: %s", reason_code)

    def on_mqtt_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        payload_text: Any
        raw = bytes(msg.payload)
        try:
            payload_text = raw.decode("utf-8")
            try:
                payload_value = orjson.loads(payload_text)
            except Exception:
                payload_value = payload_text
        except Exception:
            payload_value = raw.hex()

        envelope = {
            "type": "mqtt_message",
            "topic": msg.topic,
            "payload": payload_value,
            "qos": msg.qos,
            "retain": bool(msg.retain),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if self.loop is None:
            logging.warning("Event loop not ready; dropping MQTT message")
            return

        asyncio.run_coroutine_threadsafe(self.broadcast(envelope), self.loop)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        encoded = json_dumps(payload)
        async with self.clients_lock:
            clients = list(self.clients)

        if not clients:
            return

        dead: List[WebSocketServerProtocol] = []
        for client in clients:
            try:
                await client.send(encoded)
            except Exception:
                dead.append(client)

        if dead:
            async with self.clients_lock:
                for client in dead:
                    self.clients.discard(client)

    @staticmethod
    def describe_client(websocket: WebSocketServerProtocol) -> str:
        if websocket.remote_address:
            return f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        return "unknown"


def load_settings() -> Settings:
    return Settings(
        mqtt_host=os.getenv("MQTT_HOST", "core-mosquitto"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        mqtt_username=os.getenv("MQTT_USERNAME", ""),
        mqtt_password=os.getenv("MQTT_PASSWORD", ""),
        mqtt_client_id=os.getenv("MQTT_CLIENT_ID", "ha_ue_bridge"),
        mqtt_topics=parse_topics(os.getenv("MQTT_TOPICS_JSON", '["ue/#"]')),
        ws_host=os.getenv("WS_HOST", "0.0.0.0"),
        ws_port=int(os.getenv("WS_PORT", "8765")),
        auth_token=os.getenv("AUTH_TOKEN", ""),
        log_level=os.getenv("LOG_LEVEL", "info").upper(),
    )


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    bridge = UEMQTTBridge(settings)
    await bridge.start()


if __name__ == "__main__":
    asyncio.run(main())
