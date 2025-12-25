from flask import Flask, render_template, request, jsonify
from objects.uberDev import vehicleDetails, appLaunch, driverLocation
import config

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    config.stored_destination = request.form.get('destination')
    # Start driverLocation, it will now check config.stop_signal
    response = driverLocation(config.stored_destination)
    print(f"Destination Saved: {config.stored_destination}")
    return jsonify(status="success")

@app.route('/stop', methods=['POST'])
def stop():
    config.stop_signal = 1
    print(f"Stop signal received. Variable 'stop_signal' set to: {config.stop_signal}")
    return jsonify(status="success", value=config.stop_signal)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
