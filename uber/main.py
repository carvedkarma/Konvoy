from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Global variable to store the destination
stored_destination = None

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    global stored_destination
    stored_destination = request.form.get('destination')
    print(f"Destination Saved: {stored_destination}")
    return jsonify(status="success")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)