from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Variable to store ride data
latest_ride_request = {}

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    global latest_ride_request
    pickup = request.form.get('pickup')
    destination = request.form.get('destination')
    
    # Save to variable
    latest_ride_request = {
        'pickup': pickup,
        'destination': destination
    }
    
    print(f"New Ride Request Saved: {latest_ride_request}")
    return jsonify(status="success", message="Request saved")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)