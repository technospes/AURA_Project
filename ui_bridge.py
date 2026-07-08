"""
UI BRIDGE — WebSocket server broadcasting Jarvis state, recent commands,
and memory snapshots to the Electron UI.
"""
import asyncio
import json
import logging
import threading

logger = logging.getLogger(__name__)


class UIBridgeServer:
    def __init__(self, host="localhost", port=8765):
        self.host = host
        self.port = port
        self.clients = set()
        self._loop = None
        self.on_command_callback = None # 🟢 NEW: Backend listener
        
        self._last_state = {"type": "state", "value": "idle", "text": ""}
        self._last_commands = {"type": "recent_commands", "value": []}
        self._last_memory = {"type": "memory", "value": {}}

    async def _handler(self, websocket):
        self.clients.add(websocket)
        try:
            await websocket.send(json.dumps(self._last_state))
            await websocket.send(json.dumps(self._last_commands))
            await websocket.send(json.dumps(self._last_memory))
            
            # 🟢 NEW: Listen for clicks from the React UI
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get("type") == "ui_command" and self.on_command_callback:
                        self.on_command_callback(data["command"], data.get("payload"))
                except Exception as e:
                    logger.error(f"[UI Bridge] Message parse error: {e}")
        finally:
            self.clients.discard(websocket)

    async def _run_server(self):
        import websockets
        async with websockets.serve(self._handler, self.host, self.port) as server:
            await server.serve_forever()

    def start(self):
        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_server())

        t = threading.Thread(target=_run, daemon=True, name="ui-bridge")
        t.start()
        logger.info(f"[UI Bridge] WebSocket on ws://{self.host}:{self.port}")

    def _send_all(self, payload: dict):
        if not self._loop:
            return
        message = json.dumps(payload)
        for client in list(self.clients):
            asyncio.run_coroutine_threadsafe(client.send(message), self._loop)

    def broadcast(self, state: str, text: str = ""):
        self._last_state = {"type": "state", "value": state, "text": text}
        self._send_all(self._last_state)

    def broadcast_recent_commands(self, commands: list):
        self._last_commands = {"type": "recent_commands", "value": commands}
        self._send_all(self._last_commands)

    def broadcast_memory(self, memory: dict):
        self._last_memory = {"type": "memory", "value": memory}
        self._send_all(self._last_memory)


ui_bridge = UIBridgeServer()