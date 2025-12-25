# app.py
from flask import Flask, render_template, request

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    user_input = request.form.get('user_input')
    # This variable 'user_input' now holds the value from the frontend
    print(f"Backend received: {user_input}")
    return f"Successfully received input: {user_input}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
