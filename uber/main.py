from flask import Flask, render_template, request, jsonify
from objects.uberDev import vehicleDetails, appLaunch, driverLocation
import config

app = Flask(__name__)
stop_signal = 0

# Global variables to store the state
stored_destination = None


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/submit', methods=['POST'])
def submit():
    global stored_destination
    stored_destination = request.form.get('destination')
    response = driverLocation(stored_destination)
    print(f"Destination Saved: {stored_destination}")
    return jsonify(status="success")


@app.route('/stop', methods=['POST'])
def stop():

    config.stop_signal = 1
    print(
        f"Stop signal received. Variable 'stop_signal' set to: {stop_signal}")
    return jsonify(status="success", value=stop_signal)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
