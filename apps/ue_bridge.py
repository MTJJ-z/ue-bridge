import json
import asyncio
import threading
from datetime import datetime, timezone

import appdaemon.plugins.mqtt.mqttapi as mqtt
from websockets.sync.server import serve


class UEBridge(mqtt.Mqtt):
    def initialize(self):
        self.ws_host = self.args.get("ws_host", "0.0.0.0")
        self.ws_port = int(self.args.get("ws_port", 8765))
        self.mqtt_topics = self.args.get("mqtt_topics", ["ue/#"])
        self.publish_raw = bool(self.args.get("publish_raw", True))

        self.clients = set()
        self.clients_lock = threading.Lock()

        # 启动 websocket server
        self.server_thread = threading.Thread(
            target=self._run_ws_server,
            daemon=True
        )
        self.server_thread.start()

        # 订阅 MQTT topics
        for topic in self.mqtt_topics:
            self.mqtt_subscribe(topic)
            self.log(f"Subscribed MQTT topic: {topic}")

        # 监听 AppDaemon 中的 MQTT_MESSAGE 事件
        self.listen_event(self._on_mqtt_event, "MQTT_MESSAGE", namespace="mqtt")

        self.log(
            f"UE Bridge started. WS on ws://{self.ws_host}:{self.ws_port}, "
            f"topics={self.mqtt_topics}"
        )

    def terminate(self):
        self.log("UE Bridge terminated")

    def _run_ws_server(self):
        def handler(websocket):
            client_id = f"{websocket.remote_address}"
            with self.clients_lock:
                self.clients.add(websocket)

            self.log(f"UE client connected: {client_id}")

            # 可选：连接成功推送欢迎消息
            welcome = {
                "type": "bridge_status",
                "status": "connected",
                "server_time": datetime.now(timezone.utc).isoformat()
            }
            try:
                websocket.send(json.dumps(welcome, ensure_ascii=False))

                for message in websocket:
                    self._on_ws_message(message, websocket)

            except Exception as e:
                self.log(f"WS client error {client_id}: {e}", level="WARNING")
            finally:
                with self.clients_lock:
                    if websocket in self.clients:
                        self.clients.remove(websocket)
                self.log(f"UE client disconnected: {client_id}")

        with serve(handler, self.ws_host, self.ws_port):
            self.log(f"WebSocket server listening on {self.ws_host}:{self.ws_port}")
            threading.Event().wait()

    def _on_ws_message(self, message, websocket):
        """
        支持 UE -> MQTT 的反向通道
        UE 发：
        {
          "action": "publish",
          "topic": "ue/cmd/light",
          "payload": {"on": true},
          "qos": 0,
          "retain": false
        }
        """
        try:
            data = json.loads(message)
        except Exception:
            self.log("Received non-JSON WS message, ignored", level="WARNING")
            return

        action = data.get("action")
        if action == "publish":
            topic = data.get("topic")
            payload = data.get("payload")
            qos = int(data.get("qos", 0))
            retain = bool(data.get("retain", False))

            if not topic:
                return

            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False)

            self.mqtt_publish(topic, payload=payload, qos=qos, retain=retain)
            self.log(f"WS -> MQTT: {topic} {payload}")

    def _on_mqtt_event(self, event_name, data, kwargs):
        """
        AppDaemon MQTT_MESSAGE 常见数据里会带 topic/payload 等信息
        """
        topic = data.get("topic")
        payload = data.get("payload")
        qos = data.get("qos")
        retain = data.get("retain")
        wildcard = data.get("wildcard")

        envelope = {
            "type": "mqtt_message",
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain,
            "wildcard": wildcard,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        msg = json.dumps(envelope, ensure_ascii=False)
        dead = []

        with self.clients_lock:
            for client in list(self.clients):
                try:
                    client.send(msg)
                except Exception:
                    dead.append(client)

            for client in dead:
                self.clients.discard(client)

        self.log(f"MQTT -> WS: {topic} {payload}")