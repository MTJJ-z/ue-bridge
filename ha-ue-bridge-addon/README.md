# Home Assistant UE MQTT Bridge Add-on

这个 Add-on 做的事：

- 连接到你的 MQTT Broker
- 订阅指定 topic
- 把 MQTT 消息实时转发给 Unreal Engine 的 WebSocket 客户端
- 可选支持 UE -> MQTT 反向发布

## 目录

- `config.yaml`：Home Assistant Add-on 元数据和配置项
- `Dockerfile`：Add-on 镜像
- `run.sh`：启动脚本
- `requirements.txt`：Python 依赖
- `ue_bridge.py`：Bridge 主程序

## 安装方式

1. 在 Home Assistant 的 add-ons 仓库里加入这个自定义仓库，或者把这整个目录放进你的本地 add-ons 目录。
2. 重启 Home Assistant Supervisor。
3. 安装 `UE MQTT Bridge`。
4. 填写配置项后启动。

## 推荐配置

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_client_id: ha_ue_bridge
mqtt_topics:
  - "ue/#"
ws_host: 0.0.0.0
ws_port: 8765
auth_token: "your-secret-token"
log_level: info
```

## UE 连接地址

```text
ws://HOME_ASSISTANT_IP:8765
```

如果设置了 `auth_token`，UE 连接后第一条消息先发：

```json
{
  "action": "auth",
  "token": "your-secret-token"
}
```

## MQTT -> UE 消息格式

```json
{
  "type": "mqtt_message",
  "topic": "ue/test",
  "payload": {
    "x": 1,
    "y": 2
  },
  "qos": 0,
  "retain": false,
  "timestamp": "2026-04-09T00:00:00+00:00"
}
```

如果 MQTT payload 不是 JSON，会作为字符串转发；如果不是 UTF-8，会转成十六进制字符串。

## UE -> MQTT 消息格式

```json
{
  "action": "publish",
  "topic": "ue/cmd/player1",
  "payload": {
    "jump": true,
    "speed": 320
  },
  "qos": 0,
  "retain": false
}
```

## 心跳

UE 可以发：

```json
{
  "action": "ping"
}
```

服务端会回：

```json
{
  "type": "pong",
  "timestamp": "2026-04-09T00:00:00+00:00"
}
```

## 最小测试

发布一条 MQTT：

```bash
mosquitto_pub -h BROKER_IP -t ue/test -m '{"hello":"world"}'
```

UE 端应收到一条 `mqtt_message`。

## 注意

- `host_network: true` 已启用，方便直接暴露 WebSocket 端口。
- 如果你不想开启 host network，可以改成手动映射端口。
- 生产环境建议设置 `auth_token`，并在路由器/防火墙中限制访问来源。
