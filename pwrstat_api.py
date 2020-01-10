#!/usr/bin/env python3
"""Get output from pwrstat program and send results to REST or MQTT clients."""
import asyncio
import json
import logging
from subprocess import Popen, PIPE
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt
import voluptuous as vol
from flask import Flask, Response, make_response, jsonify
from ruamel.yaml import YAML as yaml
from ruamel.yaml import YAMLError

APP = Flask(__name__)
YAML = yaml(typ="safe")

VALID_IP_REGEX = (
    r"^(([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])\.)"
    r"{3}([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$"
)


@APP.route("/pwrstat", methods=["GET"])
def pwrstat() -> Response:
    """Responder for get requests."""
    return jsonify(get_status())


@APP.route("/health", methods=["GET"])
def health() -> Response:
    """Responder for get requests."""
    data = {"message": "OK", "code": "SUCCESS"}
    return make_response(jsonify(data), 200)


class PwrstatMqtt:
    """Create MQTT publisher."""

    def __init__(self, *args, **kwargs) -> None:
        """Start MQTT loop."""
        self.mqtt_config: Dict[str, Any] = kwargs["mqtt_config"]
        client_id: str = self.mqtt_config["client_id"]
        self.client = mqtt.Client(
            client_id=client_id,
            clean_session=True,
            userdata=None,
            protocol=mqtt.MQTTv311,
            transport="tcp",
        )

        username = self.mqtt_config.get("username")
        password = self.mqtt_config.get("password")
        if None not in (username, password):
            self.client.username_pw_set(username=username, password=password)

        mqtt_host: str = self.mqtt_config["broker"]
        mqtt_port: int = self.mqtt_config["port"]
        logging.log(level=logging.INFO, msg="Connecting to MQTT broker...")
        self.client.connect(host=mqtt_host, port=mqtt_port)
        self.refresh_interval: int = self.mqtt_config["refresh"]

    async def loop(self) -> None:
        """Loop for MQTT updates."""
        logging.log(level=logging.INFO, msg="Starting MQTT loop...")
        while True:
            await self.publish_update()
            logging.log(level=logging.DEBUG, msg="Publishing message to MQTT broker...")
            await asyncio.sleep(self.refresh_interval)

    async def publish_update(self) -> bool:
        """Update MQTT topic with latest status."""
        topic = self.mqtt_config["topic"]
        qos: int = self.mqtt_config["qos"]
        retain: bool = self.mqtt_config["retained"]
        json_payload = json.dumps(get_status())
        if json_payload is not None and len(json_payload) > 0:
            result = self.client.publish(topic, json_payload, qos=qos, retain=retain)
            return result.is_published()
        return False


class Pwrstat:
    """Get output from pwrstat program and send results to REST or MQTT clients."""

    def __init__(self, *args, **kwargs) -> None:
        """Initilize Pwrstat class."""
        with open("pwrstat.yaml") as file:
            try:
                yaml_config = YAML.load(file)
            except YAMLError as ex:
                logging.log(level=logging.ERROR, msg=ex)

        self.mqtt_config: Optional[Dict[str, Any]] = yaml_config[
            "mqtt"
        ] if "mqtt" in yaml_config else None
        self.rest_config: Optional[Dict[str, Any]] = yaml_config[
            "rest"
        ] if "rest" in yaml_config else None

        mqtt_schema = vol.Schema(
            {
                vol.Required("broker"): str,
                vol.Required("port"): int,
                vol.Required("client_id"): str,
                vol.Required("topic"): str,
                vol.Required("refresh"): int,
                vol.Required("qos"): int,
                vol.Required("retained"): bool,
                vol.Optional("username"): str,
                vol.Optional("password"): str,
            }
        )

        rest_schema = vol.Schema(
            {
                vol.Required("port"): vol.All(int, vol.Range(min=1025, max=65535)),
                vol.Required("bind_address"): vol.All(
                    str, vol.Length(min=7, max=15), vol.Match(VALID_IP_REGEX)
                ),
            }
        )

        if self.mqtt_config is not None:
            logging.log(level=logging.INFO, msg="Initializing MQTT...")
            mqtt_schema(self.mqtt_config)
            pwrstatmqtt = PwrstatMqtt(mqtt_config=self.mqtt_config)
            asyncio.run(pwrstatmqtt.loop())

        if self.rest_config is not None:
            logging.log(level=logging.INFO, msg="Initializing REST...")
            rest_schema(self.rest_config)
            port = self.rest_config["port"]
            host = self.rest_config["bind_address"]
            APP.run(port=port, host=host)


def get_status() -> Dict[str, str]:
    """Return status from pwrstat program."""
    logging.log(level=logging.DEBUG, msg="Getting status from pwrstatd...")
    status: str = Popen(["pwrstat", "-status"], stdout=PIPE).communicate()[0].decode(
        "utf-8"
    )
    status_list: List[List[str]] = []
    for line in status.splitlines():
        line = line.lstrip()
        line = line.replace(". ", ";")
        line = line.replace(".", "")
        lines: List[str] = line.split(";")
        if len(lines) > 1:
            status_list.append(lines)
    return {k[0]: k[1] for k in status_list}


if __name__ == "__main__":
    logging.log(level=logging.INFO, msg="Starting Pwrstat_API...")
    Pwrstat()
