from flask import Flask, render_template, request, jsonify
from objects.uberDev import vehicleDetails, appLaunch, driverLocation
import config

app = Flask(__name__)
stop_signal = 0

# Global variables to store the state
stored_destination = None


@app.route('/')
def root():
    return render_template('home.html')


@app.route('/change-location')
def home():
    return render_template('index.html')


@app.route('/fetch-ride')
def fetch_ride():
    ride_data = appLaunch()[0]
    print(ride_data)
    if ride_data == 1:
        return render_template('ride_details.html', ride_data=ride_data)
    else:
        return render_template('ride_details.html', ride_data=None)


@app.route('/submit', methods=['POST'])
def submit():

    config.stored_destination = request.form.get('destination')

    response = driverLocation(config.stored_destination)
    print(f"Destination Saved: {config.stored_destination}")
    return jsonify(status="success")


@app.route('/stop', methods=['POST'])
def stop():

    config.stop_signal = 1
    print(
        f"Stop signal received. Variable 'stop_signal' set to: {config.stop_signal}"
    )
    return jsonify(status="success", value=config.stop_signal)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
