# app.py
from flask import Flask, render_template, request

app = Flask(__name__)

# This global variable will store the last input received from the frontend
saved_variable = None

@app.route('/')
def home():
    return render_template('index.html', current_value=saved_variable)

@app.route('/submit', methods=['POST'])
def submit():
    global saved_variable
    saved_variable = request.form.get('user_input')
    # The value is now saved in 'saved_variable'
    print(f"Variable updated to: {saved_variable}")
    return render_template('index.html', current_value=saved_variable)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
