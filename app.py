from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chatbot')
def chatbot():
    return render_template('chatbot.html')

@app.route('/mock-exam')
def mock_exam():
    return render_template('mock_exam.html')

@app.route('/flashcards')
def flashcards():
    return render_template('flashcards.html')

@app.route('/level-test')
def level_test():
    return render_template('level_test.html')

@app.route('/question-bank')
def question_bank():
    return render_template('question_bank.html')

if __name__ == '__main__':
    app.run(debug=True)