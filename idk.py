import asyncio
import json
import random
import websockets
import uuid # For unique NPC IDs

SERVER_IP = 'localhost' # Or '0.0.0.0' to listen on all available IPs
SERVER_PORT = 8766

# --- Game World Configuration ---
GAME_MAPS = {
    "başlangıç_alanı": {
        "dimensions": [25, 15], # width, height
        "npcs": {
            "npc_wise_man": {"id": "npc_wise_man", "name": "Bilge Adam", "pos": [3, 3], "dialog": "Greetings, traveler. The path ahead is fraught with peril and opportunity."},
            "npc_merchant": {"id": "npc_merchant", "name": "Tüccar", "pos": [20, 10], "dialog": "Looking for wares? I have the finest goods... for a price."}
        },
        "portals": { # Example: pos: [x,y], target_map: "map_id", target_pos: [x,y]
            (24, 7): {"target_map": "karanlık_orman", "target_pos": [1, 7]}
        }
    },
    "karanlık_orman": {
        "dimensions": [30, 20],
        "npcs": {
            "npc_goblin_1": {"id": "npc_goblin_1", "name": "Goblin Gözcüsü", "pos": [5, 5], "dialog": "Grrr... You no pass!"},
            "npc_goblin_2": {"id": "npc_goblin_2", "name": "Goblin Akıncısı", "pos": [15, 12], "dialog": "Shiny things? Give to me!"}
        },
        "portals": {
             (0, 7): {"target_map": "başlangıç_alanı", "target_pos": [23, 7]}
        }
    }
}

# --- Server State ---
# Using nickname as player_id as per client's initial design
# If unique IDs are strictly needed beyond nickname, client_hello could assign one
# For now, client_id will be the websocket connection object itself for internal tracking,
# and player_id for communication will be the nickname.
CONNECTED_PLAYERS = {} # {websocket: {"player_id": "nickname", "pos": [x,y], "current_map": "map_id"}}

# --- Helper Functions ---
def get_npcs_for_map(map_id):
    """Returns a list of NPC data for a given map, formatted for client."""
    map_data = GAME_MAPS.get(map_id)
    if not map_data:
        return []
    
    npc_list = []
    for npc_id, npc_details in map_data.get("npcs", {}).items():
        npc_list.append({
            "id": npc_id,
            "name": npc_details.get("name", "NPC"),
            "pos": npc_details.get("pos", [0,0])
        })
    return npc_list

async def broadcast_world_update(current_map_id):
    """Sends world updates to all players on the specified map."""
    players_on_map = [p for p in CONNECTED_PLAYERS.values() if p["current_map"] == current_map_id]
    npcs_on_map = get_npcs_for_map(current_map_id)
    map_dimensions = GAME_MAPS.get(current_map_id, {}).get("dimensions", [10,10])

    for ws, player_data in CONNECTED_PLAYERS.items():
        if player_data["current_map"] == current_map_id:
            # Prepare list of other players for this specific player
            other_players_list = []
            for other_p_data in players_on_map:
                if other_p_data["player_id"] != player_data["player_id"]: # Don't send self as other
                    other_players_list.append({
                        "id": other_p_data["player_id"],
                        "pos": other_p_data["pos"]
                    })
            
            update_message = {
                "type": "world_update",
                "my_pos": player_data["pos"],
                "map_id": current_map_id,
                "map_dimensions": map_dimensions,
                "other_players": other_players_list,
                "npcs": npcs_on_map
            }
            try:
                await ws.send(json.dumps(update_message))
            except websockets.exceptions.ConnectionClosed:
                # Will be handled by the main loop's finally block
                pass


async def broadcast_chat_message(sender_id, message_text, map_id):
    """Broadcasts a chat message to all players on the same map."""
    chat_message = {
        "type": "chat_message",
        "sender": sender_id,
        "message": message_text
    }
    for ws, player_data in CONNECTED_PLAYERS.items():
        if player_data["current_map"] == map_id:
            try:
                await ws.send(json.dumps(chat_message))
            except websockets.exceptions.ConnectionClosed:
                pass

async def send_private_chat_message(ws_receiver, sender_name, message_text):
    """Sends a private chat message (e.g., from an NPC) to a specific player."""
    chat_message = {
        "type": "chat_message",
        "sender": sender_name,
        "message": message_text
    }
    try:
        await ws_receiver.send(json.dumps(chat_message))
    except websockets.exceptions.ConnectionClosed:
        pass


# --- Action Handlers ---
async def handle_client_hello(ws, data):
    nickname = data.get('nickname', 'AnonPlayer' + str(random.randint(1000,9999)))
    client_version = data.get('client_version', 'unknown')
    print(f"[INFO] Client Hello from {nickname} (Version: {client_version})")

    # Check if nickname is already in use
    for p_data in CONNECTED_PLAYERS.values():
        if p_data["player_id"] == nickname:
            # Simple handling: append random numbers if nickname exists
            # A more robust system would ask client to pick another or assign unique ID
            nickname += str(random.randint(10,99))
            break
            
    initial_map = "başlangıç_alanı"
    initial_pos = [random.randint(1, GAME_MAPS[initial_map]["dimensions"][0]-2), 
                   random.randint(1, GAME_MAPS[initial_map]["dimensions"][1]-2)]

    CONNECTED_PLAYERS[ws] = {
        "player_id": nickname,
        "pos": initial_pos,
        "current_map": initial_map
    }

    # Acknowledge connection
    ack_message = {
        "type": "connection_ack",
        "your_id": nickname, # Client expects its nickname back as 'your_id'
        "message": f"Welcome to the server, {nickname}!"
    }
    await ws.send(json.dumps(ack_message))
    await broadcast_world_update(initial_map)
    await broadcast_chat_message("SERVER", f"{nickname} has joined the {initial_map}.", initial_map)


async def handle_move(ws, data):
    player_data = CONNECTED_PLAYERS.get(ws)
    if not player_data:
        return

    direction = data.get('direction')
    current_map_id = player_data["current_map"]
    map_config = GAME_MAPS.get(current_map_id)
    if not map_config:
        return

    map_width, map_height = map_config["dimensions"]
    new_pos = list(player_data["pos"]) # Make a mutable copy

    if direction == 'up':
        new_pos[1] -= 1
    elif direction == 'down':
        new_pos[1] += 1
    elif direction == 'left':
        new_pos[0] -= 1
    elif direction == 'right':
        new_pos[0] += 1
    
    # Boundary checks
    new_pos[0] = max(0, min(new_pos[0], map_width - 1))
    new_pos[1] = max(0, min(new_pos[1], map_height - 1))

    player_data["pos"] = new_pos
    
    # Check for portals
    portals_on_map = map_config.get("portals", {})
    if tuple(new_pos) in portals_on_map:
        portal_info = portals_on_map[tuple(new_pos)]
        old_map_id = player_data["current_map"]
        player_data["current_map"] = portal_info["target_map"]
        player_data["pos"] = list(portal_info["target_pos"]) # Ensure it's a list
        
        await broadcast_chat_message("SERVER", f"{player_data['player_id']} has moved to {player_data['current_map']}.", player_data['current_map'])
        # Send update for the old map (player disappeared)
        await broadcast_world_update(old_map_id)
        # Send update for the new map (player appeared)
        await broadcast_world_update(player_data["current_map"])
        await send_private_chat_message(ws, "SYSTEM", f"You have entered {player_data['current_map']}.")

    else:
        # If no portal, just broadcast update for the current map
        await broadcast_world_update(current_map_id)


async def handle_chat(ws, data):
    player_data = CONNECTED_PLAYERS.get(ws)
    if not player_data:
        return
    message_text = data.get('message', '')
    if message_text: # Don't send empty messages
        print(f"[CHAT] {player_data['player_id']} on {player_data['current_map']}: {message_text}")
        await broadcast_chat_message(player_data['player_id'], message_text, player_data['current_map'])

async def handle_interact_nearby(ws, data):
    player_data = CONNECTED_PLAYERS.get(ws)
    if not player_data:
        return

    player_pos = player_data["pos"]
    current_map_id = player_data["current_map"]
    map_npcs = GAME_MAPS.get(current_map_id, {}).get("npcs", {})
    
    interacted = False
    for npc_id, npc_details in map_npcs.items():
        npc_pos = npc_details["pos"]
        # Check if player is adjacent (Manhattan distance <= 1) or on the same tile
        if abs(player_pos[0] - npc_pos[0]) <= 1 and abs(player_pos[1] - npc_pos[1]) <= 1:
            dialog = npc_details.get("dialog", "This NPC has nothing to say.")
            await send_private_chat_message(ws, npc_details["name"], dialog)
            interacted = True
            break # Interact with one NPC at a time
    
    if not interacted:
        await send_private_chat_message(ws, "SYSTEM", "There is no one nearby to interact with.")


# --- Main WebSocket Handler ---
async def client_handler(websocket, path):
    """Handles a single client connection."""
    client_id_for_log = websocket.remote_address # For logging
    print(f"[INFO] New connection from {client_id_for_log}")

    try:
        # Keep connection alive and process messages
        async for message_json in websocket:
            try:
                message_data = json.loads(message_json)
                action = message_data.get('action')

                if action == 'client_hello':
                    await handle_client_hello(websocket, message_data)
                elif websocket not in CONNECTED_PLAYERS:
                    # If not yet greeted, ignore other actions
                    print(f"[WARN] Received action '{action}' from uninitialized client {client_id_for_log}. Ignoring.")
                    continue 
                elif action == 'move':
                    await handle_move(websocket, message_data)
                elif action == 'chat':
                    await handle_chat(websocket, message_data)
                elif action == 'interact_nearby':
                    await handle_interact_nearby(websocket, message_data)
                elif action == 'client_disconnecting':
                    print(f"[INFO] Client {CONNECTED_PLAYERS.get(websocket, {}).get('player_id', client_id_for_log)} sent disconnect message.")
                    # No specific action needed here as the 'finally' block will handle cleanup
                    break # Exit the loop, leading to 'finally'
                else:
                    print(f"[WARN] Unknown action '{action}' from {CONNECTED_PLAYERS.get(websocket, {}).get('player_id', client_id_for_log)}")

            except json.JSONDecodeError:
                print(f"[ERROR] Could not decode JSON from {client_id_for_log}: {message_json}")
            except Exception as e:
                player_id_for_error = CONNECTED_PLAYERS.get(websocket, {}).get('player_id', str(client_id_for_log))
                print(f"[ERROR] Error processing message for {player_id_for_error}: {e}")
                # Optionally send an error message back to the client
                # await websocket.send(json.dumps({"type": "error", "message": "An internal server error occurred."}))


    except websockets.exceptions.ConnectionClosedError as e:
        print(f"[INFO] Connection from {client_id_for_log} closed unexpectedly: Code {e.code}, Reason: {e.reason}")
    except websockets.exceptions.ConnectionClosedOK:
        print(f"[INFO] Connection from {client_id_for_log} closed normally.")
    finally:
        # Cleanup when client disconnects
        player_leaving_data = CONNECTED_PLAYERS.pop(websocket, None)
        if player_leaving_data:
            player_id = player_leaving_data["player_id"]
            map_id = player_leaving_data["current_map"]
            print(f"[INFO] Player {player_id} disconnected from {map_id}.")
            await broadcast_chat_message("SERVER", f"{player_id} has left the server.", map_id)
            # Update world for players on the map the leaver was on
            await broadcast_world_update(map_id)
        else:
            print(f"[INFO] Unidentified connection {client_id_for_log} closed.")


async def main():
    """Starts the WebSocket server."""
    print(f"[INFO] Starting WebSocket server on ws://{SERVER_IP}:{SERVER_PORT}")
    async with websockets.serve(client_handler, SERVER_IP, SERVER_PORT, ping_interval=20, ping_timeout=20):
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[INFO] Server shutting down...")

