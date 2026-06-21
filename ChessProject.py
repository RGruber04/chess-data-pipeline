import os
import functions_framework
import requests
import re
import time
from google import genai
from google.cloud import firestore
from discord_webhook import DiscordWebhook


API_KEY = os.environ.get("gemini_API_KEY")


users = [
    {
        'username': 'Rgruber08', 
        'webhook': os.environ.get('Rgruber08_Webhook')
    },
    {
        'username': 'SnowyOasis',
        'webhook':os.environ.get('SnowyOasis_Webhook')
    }
]

def get_latest_chess_game(username: str) -> dict | None :
    '''
    Uses chess.com's public read only API to fetch latest game played from their monthly games database by username.
    '''
    chess_archive_url = f"https://api.chess.com/pub/player/{username}/games/archives"
    headers = {'User-Agent': 'ChessAnalysisBot/1.0 (Contact: ryangruber13@gmail.com)'}
    try:
        response = requests.get(chess_archive_url, headers=headers)
        response.raise_for_status()
        latest_month = response.json().get('archives')[-1]
        games_response = requests.get(latest_month, headers=headers)
        games_response.raise_for_status()
        latest_game = games_response.json().get('games')[-1]
        return {
            'url': latest_game.get('url'),
            'pgn': latest_game.get('pgn'),
            'uuid': latest_game.get('uuid')
        }
    except Exception as e:
        print(f"Error fetching games for {username}: {e}")
        return None

def get_agentic_feedback(pgn_data: str, username: str) -> str:
    '''
    Sends PGN data to gemini 3 flash with system instructions.
    Cleans the clock tags and white space out of the PGN data.
    Defines each players color so gemini knows which player to analyze
    '''
    client = genai.Client(api_key=API_KEY)
    
   
    sys_instructions = (
        "You are a blunt, tough love style expert human chess coach. You focus on the vibe of the game "
        "and logical human mistakes rather than computer engine evaluations that are too hard for people to see. "
        f"You are coaching the player with the username {username}. "
        "WHEN EXAMINING WRONG MOVES SUGGEST BETTER ONES THAT ARE EASIER TO SEE AND DESCRIBE WHAT THE GOAL IS. "
        "Keep in mind the user's rating and their goal of 1300 elo. "
        "Write a detailed coaching review of approximately 400 to 500 words. Do not be brief; I want to understand the 'why' behind my mistakes."
        "Use a 'Step-by-Step' analysis style. For the opening, middlegame, and endgame, provide at least 3 sentences of feedback for each phase AND SAY THE USERS BIGGEST WEAKNESS IN THEIR GAME AND HOW TO FIX IT."
    )

    find_white_player = re.search(r'\[White "(.*?)"\]', pgn_data)
    find_black_player = re.search(r'\[Black "(.*?)"\]', pgn_data)

    white_player = find_white_player.group(1) if find_white_player else "re error finding player"
    black_player = find_black_player.group(1) if find_black_player else "re error finding player"

    moves_only = pgn_data.split('\n\n')[-1]
    clean_moves = re.sub(r'\{.*?\}', '', moves_only)
    clean_moves = ' '.join(clean_moves.split())

    context_header = f"Player White: {white_player}, Player Black: {black_player}."
    full_prompt = f"{context_header}\n\nAnalyze this game for {username}: {clean_moves}"

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        config={'system_instruction': sys_instructions,
        'max_output_tokens': 2100,
        'temperature': 0.7},
        contents=f"Analyze this game for me: {full_prompt}"
    )
    return response.text

def send_to_discord(report_text: str, webhook_url: str, username: str) -> bool: 
    '''
    Sends results of gemini analysis to A discord server using a webhook
    Breaks output into chunks to get around discords 2000 character limit
    Spaces out the sending of chunks so that no ouput gets dropped
    '''
    chunk_size = 1800 
    if len(report_text) <= chunk_size:
        webhook = DiscordWebhook(url=webhook_url, content=report_text)
        webhook.execute()
    else:
        chunks = [report_text[i:i + chunk_size] for i in range(0, len(report_text), chunk_size)]
        for index, chunk in enumerate(chunks):
            part_label = f"**{username} Analysis (Part {index+1}/{len(chunks)})**\n"
            webhook = DiscordWebhook(url=webhook_url, content=part_label + chunk)
            webhook.execute()
            time.sleep(2) 
    return True

    
def check_and_store_game_firestore(game_uuid: str) -> bool:
    '''
    Unique ID of each game is stored here so that the same game isn't analyzed twice.
    '''
    db = firestore.Client()
    game_ref = db.collection("processed_games").document(game_uuid)
    doc = game_ref.get()
    if doc.exists:
        return True
    game_ref.set({
        "timestamp": firestore.SERVER_TIMESTAMP,
        "status": "analyzed"
    })
    return False

def main():
    '''
    Executes the game retrieval, analysis and output for all listed users.
    '''
    results = []
    for user in users:
        current_user = user['username']
        current_webhook = user['webhook']

        game_data = get_latest_chess_game(current_user)
        if not game_data:
            results.append(f"No games for {current_user}")
            continue

        is_old_game = check_and_store_game_firestore(game_data['uuid'])
        if is_old_game:
            results.append(f"Skipping {current_user}, already analyzed.")
            continue

        coach_feedback = get_agentic_feedback(game_data['pgn'], current_user)
        send_to_discord(coach_feedback, current_webhook, current_user)
        results.append(f"Analyzed game for {current_user}.")
    
    return " | ".join(results)

@functions_framework.http
def chess_agent_entry_point(request):
    '''
    HTTP trigger for cloud run function.
    Accepts the incoming request from cloud run/scheduler and executes everything
    '''
    try:
        status = main()
        return status, 200
    except Exception as e:
        print(f"Cloud Error: {e}")
        return f"Error: {e}", 500