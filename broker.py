"""Fallback pure-Python MQTT broker (amqtt) for machines without Node.js.

Use it for the Python pipeline and Node-RED (TCP 1883). Its WebSocket listener (9001)
accepts browser clients but did not deliver messages to them in our tests (amqtt 0.12.1),
so the browser dashboard needs the Aedes broker in broker_node/ (or Mosquitto)."""
import asyncio
import sys

from amqtt.broker import Broker

import config as C

CONFIG = {
    "listeners": {
        "default": {"type": "tcp", "bind": f"0.0.0.0:{C.MQTT_PORT}"},
        "ws": {"type": "ws", "bind": f"0.0.0.0:{C.MQTT_WS_PORT}"},
    },
    "plugins": {
        "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True},
    },
}


async def main():
    if len(sys.argv) > 1:
        CONFIG["listeners"]["default"]["bind"] = f"0.0.0.0:{sys.argv[1]}"
    broker = Broker(CONFIG)
    await broker.start()
    print(f"MQTT broker running: {CONFIG['listeners']['default']['bind']}",
          flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
