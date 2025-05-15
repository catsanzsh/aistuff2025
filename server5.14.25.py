import asyncio
import json
import random
import websockets
import uuid # For unique NPC IDs (currently unused but kept for potential future use)

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
        },
        "blocked_tiles": [[5,5], [5,6], [6,5]] # Example of impassable tiles
    },
    "karanlık_orman": {
        "dimensions": [30, 20],
        "npcs": {
            "npc_goblin_1": {"id": "npc_goblin_1", "name": "Goblin Gözcüsü", "pos": [5, 5], "dialog": "Grrr... You no pass!"},
            "npc_goblin_2": {"id": "npc_goblin_2", "name": "Goblin Akıncısı", "pos": [15, 12], "dialog": "Shiny things? Give to me!"}
        },
        "portals": {
             (0, 7): {"target_map": "başlangıç_alanı", "target_pos": [23, 7]}
        },
        "blocked_tiles": []
    }
}

# --- Server State ---
CONNECTED_PLAYERS = {} # {websocket: {"player_id": "nickname", "pos": [x,y], "current_map": "map_id"}}

# --- Helper Functions ---
def get_npcs_for_map(map_id):
    """Returns a list of NPC data for a given map, formatted for client."""
    map_data = GAME_MAPS.get(map_id)
    if not map_data or not isinstance(map_data, dict): # Ensure map_data is a dictionary
        print(f"[ERROR] Map data for '{map_id}' is missing or invalid in get_npcs_for_map.")
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
    map_config = GAME_MAPS.get(current_map_id)
    if not map_config or not isinstance(map_config, dict):
        print(f"[ERROR] Cannot broadcast world update: Map '{current_map_id}' not found or invalid.")
        return

    players_on_map = [p for p in CONNECTED_PLAYERS.values() if p["current_map"] == current_map_id]
    npcs_on_map = get_npcs_for_map(current_map_id) # Already handles map existence
    map_dimensions = map_config.get("dimensions", [10,10])
    # Include blocked tiles in the world update if client needs to know
    # blocked_tiles_on_map = map_config.get("blocked_tiles", [])


    for ws, player_data in list(CONNECTED_PLAYERS.items()): # Use list for safe iteration if a player disconnects during broadcast
        if player_data["current_map"] == current_map_id:
            other_players_list = []
            for other_p_data in players_on_map:
                if other_p_data["player_id"] != player_data["player_id"]:
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
                # "blocked_tiles": blocked_tiles_on_map # Optional: if client handles this
            }
            try:
                await ws.send(json.dumps(update_message))
            except websockets.exceptions.ConnectionClosed:
                # Handled by the main client_handler's finally block
                print(f"[INFO] Attempted to send to a closed connection for player {player_data.get('player_id', 'Unknown')} during world update.")
                pass # The connection will be cleaned up by client_handler
            except Exception as e:
                print(f"[ERROR] Failed to send world update to {player_data.get('player_id', 'Unknown')}: {e}")


async def broadcast_chat_message(sender_id, message_text, map_id):
    """Broadcasts a chat message to all players on the same map."""
    if not GAME_MAPS.get(map_id): # Ensure map exists
        print(f"[WARN] Attempted to broadcast chat to non-existent map: {map_id}")
        return

    chat_message = {
        "type": "chat_message",
        "sender": sender_id,
        "message": message_text
    }
    for ws, player_data in list(CONNECTED_PLAYERS.items()): # Iterate over a copy
        if player_data["current_map"] == map_id:
            try:
                await ws.send(json.dumps(chat_message))
            except websockets.exceptions.ConnectionClosed:
                pass # Handled by client_handler
            except Exception as e:
                print(f"[ERROR] Failed to send chat message to {player_data.get('player_id', 'Unknown')}: {e}")


async def send_private_chat_message(ws_receiver, sender_name, message_text):
    """Sends a private chat message (e.g., from an NPC) to a specific player."""
    chat_message = {
        "type": "chat_message",
        "sender": sender_name,
        "message": message_text
    }
    try:
        if ws_receiver and ws_receiver.open:
            await ws_receiver.send(json.dumps(chat_message))
    except websockets.exceptions.ConnectionClosed:
        print(f"[INFO] Attempted to send private message to a closed connection.")
    except Exception as e:
        print(f"[ERROR] Failed to send private chat message: {e}")


# --- Action Handlers ---
async def handle_client_hello(ws, data):
    nickname = data.get('nickname', 'AnonPlayer' + str(random.randint(1000,9999)))
    client_version = data.get('client_version', 'unknown')
    print(f"[INFO] Client Hello from {nickname} (Version: {client_version})")

    # Check if nickname is already in use
    for p_data in CONNECTED_PLAYERS.values():
        if p_data["player_id"] == nickname:
            nickname += str(random.randint(10,99)) # Simple collision resolution
            break
            
    initial_map_id = "başlangıç_alanı"
    initial_map_config = GAME_MAPS.get(initial_map_id)

    if not initial_map_config or not isinstance(initial_map_config.get("dimensions"), list) or len(initial_map_config["dimensions"]) != 2:
        print(f"[ERROR] Initial map '{initial_map_id}' configuration is invalid or missing dimensions. Client cannot join.")
        await ws.close(code=1011, reason="Server error: Initial map config invalid.")
        return

    map_dims = initial_map_config["dimensions"]
    # Ensure spawn is not on the very edge, and map is large enough.
    # Spawn at least 1 tile away from edge if map is 3x3 or larger.
    # If map is smaller, spawn at (0,0) or center.
    if map_dims[0] >= 3 and map_dims[1] >= 3:
        initial_pos = [random.randint(1, map_dims[0] - 2), 
                       random.randint(1, map_dims[1] - 2)]
    else: # For very small maps, spawn in the middle or at (0,0)
        initial_pos = [map_dims[0] // 2, map_dims[1] // 2]
    
    # Ensure initial position is not blocked (if blocked_tiles are implemented for movement)
    # For now, this is a simple spawn. A more complex system would check for valid spawn points.

    CONNECTED_PLAYERS[ws] = {
        "player_id": nickname,
        "pos": initial_pos,
        "current_map": initial_map_id
    }

    ack_message = {
        "type": "connection_ack",
        "your_id": nickname,
        "message": f"Welcome to the server, {nickname}!"
    }
    await ws.send(json.dumps(ack_message))
    await broadcast_world_update(initial_map_id)
    await broadcast_chat_message("SERVER", f"{nickname} has joined the {initial_map_id}.", initial_map_id)


async def handle_move(ws, data):
    player_data = CONNECTED_PLAYERS.get(ws)
    if not player_data:
        return

    direction = data.get('direction')
    current_map_id = player_data["current_map"]
    map_config = GAME_MAPS.get(current_map_id)

    if not map_config or not isinstance(map_config.get("dimensions"), list) or len(map_config["dimensions"]) != 2:
        print(f"[ERROR] Player {player_data['player_id']} on invalid map '{current_map_id}'. Cannot move.")
        return

    map_width, map_height = map_config["dimensions"]
    blocked_tiles = map_config.get("blocked_tiles", [])
    old_pos = list(player_data["pos"]) # Store old position for portal check
    new_pos_candidate = list(player_data["pos"]) # Candidate for new position

    if direction == 'up':
        new_pos_candidate[1] -= 1
    elif direction == 'down':
        new_pos_candidate[1] += 1
    elif direction == 'left':
        new_pos_candidate[0] -= 1
    elif direction == 'right':
        new_pos_candidate[0] += 1
    
    # Boundary checks
    new_pos_candidate[0] = max(0, min(new_pos_candidate[0], map_width - 1))
    new_pos_candidate[1] = max(0, min(new_pos_candidate[1], map_height - 1))

    # Check for blocked tiles (simple implementation)
    if new_pos_candidate in blocked_tiles:
        await send_private_chat_message(ws, "SYSTEM", "You can't move there.")
        # Player doesn't move, but we still send a world update to confirm their current position
        # or simply do nothing if client doesn't expect update on failed move.
        # For simplicity, we'll just not update position and rely on next world_update.
        # No actual position change, so no broadcast needed unless to correct client prediction.
        # To ensure client is synced, we could send a specific update for this player.
        # However, the client sends move and server updates state; client should reflect server state.
        return # Movement blocked

    player_data["pos"] = new_pos_candidate # Update position
    
    # Check for portals using the NEW position
    portals_on_map = map_config.get("portals", {})
    if tuple(player_data["pos"]) in portals_on_map:
        portal_info = portals_on_map[tuple(player_data["pos"])]
        target_map_id = portal_info["target_map"]
        requested_target_pos = portal_info["target_pos"]
        
        target_map_config = GAME_MAPS.get(target_map_id)
        if not target_map_config or not isinstance(target_map_config.get("dimensions"), list):
            print(f"[ERROR] Portal to invalid map '{target_map_id}' from '{current_map_id}'. Player {player_data['player_id']} remains.")
            await send_private_chat_message(ws, "SYSTEM", "This portal seems to lead nowhere safe...")
            player_data["pos"] = old_pos # Revert to position before portal attempt if portal is bad
            await broadcast_world_update(current_map_id) # Update with reverted position
            return

        target_map_dims = target_map_config["dimensions"]
        # Validate and clamp target portal position
        final_target_pos_x = max(0, min(requested_target_pos[0], target_map_dims[0] - 1))
        final_target_pos_y = max(0, min(requested_target_pos[1], target_map_dims[1] - 1))
        
        # Check if portal target position is blocked on the new map
        target_map_blocked_tiles = target_map_config.get("blocked_tiles", [])
        if [final_target_pos_x, final_target_pos_y] in target_map_blocked_tiles:
            print(f"[WARN] Player {player_data['player_id']} tried to portal into a blocked tile on {target_map_id}. Finding alternative.")
            # Simple fallback: try to place near portal or at a default spawn for that map.
            # For now, let's put them at map center if entry is blocked.
            final_target_pos_x = target_map_dims[0] // 2
            final_target_pos_y = target_map_dims[1] // 2
            await send_private_chat_message(ws, "SYSTEM", "The portal exit was obstructed, you appear nearby.")


        old_map_id_for_broadcast = player_data["current_map"] # Store before changing
        player_data["current_map"] = target_map_id
        player_data["pos"] = [final_target_pos_x, final_target_pos_y]
        
        # Announce departure on old map and arrival on new map
        await broadcast_chat_message("SERVER", f"{player_data['player_id']} has left for another area.", old_map_id_for_broadcast)
        await broadcast_world_update(old_map_id_for_broadcast) # Update old map (player disappeared)
        
        await broadcast_chat_message("SERVER", f"{player_data['player_id']} has arrived.", player_data['current_map'])
        await broadcast_world_update(player_data["current_map"]) # Update new map (player appeared)
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
    map_config = GAME_MAPS.get(current_map_id)

    if not map_config or not isinstance(map_config.get("npcs"), dict):
        await send_private_chat_message(ws, "SYSTEM", "This area feels strangely empty of anyone to talk to.")
        return
        
    map_npcs = map_config["npcs"]
    interacted = False
    for npc_id, npc_details in map_npcs.items():
        npc_pos = npc_details.get("pos")
        if not isinstance(npc_pos, list) or len(npc_pos) != 2: continue # Skip invalid NPC pos

        # Check if player is adjacent (Manhattan distance <= 1) or on the same tile
        if abs(player_pos[0] - npc_pos[0]) <= 1 and abs(player_pos[1] - npc_pos[1]) <= 1:
            dialog = npc_details.get("dialog", "This NPC has nothing to say.")
            await send_private_chat_message(ws, npc_details.get("name", "NPC"), dialog)
            interacted = True
            break 
    
    if not interacted:
        await send_private_chat_message(ws, "SYSTEM", "There is no one nearby to interact with.")


# --- Main WebSocket Handler ---
async def client_handler(websocket, path):
    """Handles a single client connection."""
    client_id_for_log = websocket.remote_address 
    print(f"[INFO] New connection from {client_id_for_log}")

    try:
        async for message_json in websocket:
            try:
                message_data = json.loads(message_json)
                action = message_data.get('action')

                if action == 'client_hello':
                    await handle_client_hello(websocket, message_data)
                elif websocket not in CONNECTED_PLAYERS:
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
                    break 
                else:
                    print(f"[WARN] Unknown action '{action}' from {CONNECTED_PLAYERS.get(websocket, {}).get('player_id', client_id_for_log)}")

            except json.JSONDecodeError:
                print(f"[ERROR] Could not decode JSON from {client_id_for_log}: {message_json}")
            except Exception as e:
                player_id_for_error = CONNECTED_PLAYERS.get(websocket, {}).get('player_id', str(client_id_for_log))
                print(f"[ERROR] Error processing message for {player_id_for_error}: {e} (Message: {message_json})")
                # Optionally send an error message back to the client
                try:
                    await websocket.send(json.dumps({"type": "error", "message": "An internal server error occurred processing your request."}))
                except: pass


    except websockets.exceptions.ConnectionClosedError as e:
        print(f"[INFO] Connection from {client_id_for_log} closed unexpectedly: Code {e.code}, Reason: {e.reason}")
    except websockets.exceptions.ConnectionClosedOK:
        print(f"[INFO] Connection from {client_id_for_log} closed normally.")
    except Exception as e: # Catch any other exceptions during the handler's lifetime
        print(f"[ERROR] Unhandled exception in client_handler for {client_id_for_log}: {e}")
    finally:
        player_leaving_data = CONNECTED_PLAYERS.pop(websocket, None)
        if player_leaving_data:
            player_id = player_leaving_data["player_id"]
            map_id = player_leaving_data["current_map"]
            print(f"[INFO] Player {player_id} disconnected from {map_id}.")
            # Ensure map_id is valid before broadcasting
            if GAME_MAPS.get(map_id):
                await broadcast_chat_message("SERVER", f"{player_id} has left the server.", map_id)
                await broadcast_world_update(map_id)
            else:
                print(f"[WARN] Player {player_id} was on an invalid map '{map_id}' upon disconnect. No final broadcast for that map.")
        else:
            print(f"[INFO] Unidentified connection {client_id_for_log} closed and removed.")


async def main():
    """Starts the WebSocket server."""
    print(f"[INFO] Starting WebSocket server on ws://{SERVER_IP}:{SERVER_PORT}")
    # Set a higher connection limit if needed, default is 100
    async with websockets.serve(client_handler, SERVER_IP, SERVER_PORT, ping_interval=20, ping_timeout=20):
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] Server shutting down gracefully...")
    except Exception as e:
        print(f"[FATAL] Server failed to start or crashed: {e}")

