from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import RPi.GPIO as GPIO
import time
import logging
import random
import json
import serial

# Initialize Flask app and SocketIO for real-time communication
app = Flask(__name__)
socketio = SocketIO(app)

# Configure logging for debugging purposes
logging.basicConfig(level=logging.DEBUG)

# GPIO setup for the distance sensor
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
TRIG = 17  # GPIO pin for the TRIG of the distance sensor
ECHO = 27  # GPIO pin for the ECHO of the distance sensor
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

# Path to the JSON file for player data
DATA_FILE = 'player_data.json'

# Function to get player distance from the Arduino (assuming serial communication)
def get_player_distance():
    try:
        line = ser.readline().decode('utf-8').rstrip()
        logging.debug(f"Raw data from Arduino: {line}")
        if "Distance" in line:
            distance = float(line.split(": ")[1].split(" cm")[0])
            return distance
        else:
            logging.error("Unexpected data format from Arduino")
            return float('inf')
    except Exception as e:
        logging.error(f"Error reading from serial: {e}")
        return float('inf')

# Function to load player data from the JSON file
def load_data():
    try:
        with open(DATA_FILE, 'r') as file:
            data = json.load(file)
            if 'players' not in data:
                raise ValueError("Player data missing 'players' key.")
            for player in data['players']:
                if 'username' not in player:
                    raise ValueError("Player data missing 'username' key.")
            return data['players']
    except (FileNotFoundError, ValueError) as e:
        logging.error(f"Error loading data: {e}")
        return []

# Function to save player data to the JSON file
def save_data(players):
    with open(DATA_FILE, 'w') as file:
        json.dump({'players': players}, file, indent=4)

# Load player data at startup
players = load_data()
high_scores = []

# Function to measure the distance using the ultrasonic sensor
def distance():
    try:
        GPIO.output(TRIG, True)
        time.sleep(0.00001)
        GPIO.output(TRIG, False)
        start_time = time.time()
        stop_time = time.time()
        while GPIO.input(ECHO) == 0:
            start_time = time.time()
        while GPIO.input(ECHO) == 1:
            stop_time = time.time()
        elapsed_time = stop_time - start_time
        dist = (elapsed_time * 34300) / 2
        logging.debug(f"Measured Distance: {dist} cm")
        return dist
    except Exception as e:
        logging.error(f"Error measuring distance: {e}")
        return float('inf')

# Route to render the main page
@app.route('/')
def index():
    return render_template('index.html')

# Route to handle high scores (both GET and POST methods)
@app.route('/highscores', methods=['GET', 'POST'])
def highscores():
    global high_scores
    if request.method == 'POST':
        data = request.json
        high_scores.append(data)
        high_scores = sorted(high_scores, key=lambda x: x['score'], reverse=True)[:10]
        return jsonify({'status': 'success'})
    else:
        return jsonify(high_scores)

# Event handler for client connection
@socketio.on('connect')
def handle_connect():
    logging.info('Client connected')
    emit('updateLeaderboard', high_scores)

# Event handler for client disconnection
@socketio.on('disconnect')
def handle_disconnect():
    logging.info('Client disconnected')

# Event handler to get the player's currency
@socketio.on('getCurrency')
def handle_get_currency(data):
    global players
    username = data['username']
    player = next((p for p in players if p['username'] == username), None)
    if player:
        emit('currencyUpdate', {'username': username, 'currency': player['currency']})
    else:
        emit('currencyUpdate', {'username': username, 'currency': 0})

# Event handler to start the game
@socketio.on('startGame')
def handle_start_game(data):
    global players
    username = data['username']
    bet = data.get('bet', 0)
    player = next((p for p in players if p['username'] == username), None)
    
    if not player:
        player = {
            'username': username,
            'score': 0,
            'currency': 100,  # Starting currency
            'mode': 'competitive',
            'game_started': True,
            'game_over': False,
            'bet': bet,
            'target_score': random.randint(5, 10)
        }
        players.append(player)
    else:
        player['score'] = 0
        player['game_started'] = True
        player['game_over'] = False
        player['bet'] = bet
        player['target_score'] = random.randint(5, 10)

    logging.info(f'Game started for {username} with bet: {bet}')
    save_data(players)  # Save updates to file

    emit('startGameResponse', {'targetScore': player['target_score']})

# Event handler to end the game
@socketio.on('endGame')
def handle_end_game(data):
    global players, high_scores
    username = data['username']
    player = next((p for p in players if p['username'] == username), None)
    if player and player['game_started']:
        player['game_started'] = False
        player['game_over'] = True
        if player['score'] >= player['target_score']:
            player['currency'] += player['bet']  # Win bet amount
        else:
            player['currency'] -= player['bet']  # Lose bet amount
        high_scores.append({'username': username, 'score': player['score']})
        high_scores.sort(key=lambda x: x['score'], reverse=True)
        high_scores = high_scores[:5]  # Keep only top 5 scores
        socketio.emit('updateLeaderboard', high_scores)
        logging.info(f'Game ended for {username}')
        save_data(players)  # Save updates to file

# Function to update the score in real-time
def update_score():
    global players
    detection_window = 0.5  # Time window to consider for detection
    last_detection_time = time.time() - detection_window

    while True:
        for player in players:
            if player['game_started'] and not player['game_over']:
                hoop_distance = distance()  # Get hoop distance using the ultrasonic sensor
                current_time = time.time()

                # Check if the detected distance is within the threshold
                if hoop_distance < 30:  # Adjust threshold as necessary
                    # Check for debounce window
                    if current_time - last_detection_time >= detection_window:
                        player['score'] += 1
                        player['currency'] += 0.5  # Increase currency by 0.5
                        logging.info(f"Score and currency updated for {player['username']}: {player['score']} points, {player['currency']} currency")
                        socketio.emit('scoreUpdate', {'username': player['username'], 'score': player['score'], 'currency': player.get('currency', 0)})
                        last_detection_time = current_time
                        # Provide a brief pause to avoid multiple detections
                        time.sleep(0.5)
                else:
                    time.sleep(0.1)  # Check more frequently

        time.sleep(0.1)  # Reduce sleep time for the outer loop as well

# Main function to run the Flask app and start the score updating thread
if __name__ == '__main__':
    from threading import Thread
    thread = Thread(target=update_score)
    thread.daemon = True
    thread.start()
    socketio.run(app, host='0.0.0.0', port=5000)
