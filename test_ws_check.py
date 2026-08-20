import asyncio
import json
import websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        for i in range(3):
            msg = await ws.recv()
            data = json.loads(msg)
            print(i + 1, data["agents"][0]["x"], data["agents"][0]["y"], data["map"]["width"], data["map"]["height"])

asyncio.run(main())