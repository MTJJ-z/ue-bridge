#!/usr/bin/with-contenv bashio
set -e

export MQTT_HOST="$(bashio::config 'mqtt_host')"
export MQTT_PORT="$(bashio::config 'mqtt_port')"
export MQTT_USERNAME="$(bashio::config 'mqtt_username')"
export MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
export MQTT_CLIENT_ID="$(bashio::config 'mqtt_client_id')"
export MQTT_TOPICS_JSON="$(bashio::config 'mqtt_topics')"
export WS_HOST="$(bashio::config 'ws_host')"
export WS_PORT="$(bashio::config 'ws_port')"
export AUTH_TOKEN="$(bashio::config 'auth_token')"
export LOG_LEVEL="$(bashio::config 'log_level')"

bashio::log.info "Starting UE MQTT Bridge"
python3 /app/ue_bridge.py
