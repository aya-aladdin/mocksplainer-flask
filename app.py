from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
import requests
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'  # Change this!
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'


db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic = db.Column(db.String(100), nullable=False)
    difficulty = db.Column(db.String(50), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(200), nullable=False)
    option_b = db.Column(db.String(200), nullable=False)
    option_c = db.Column(db.String(200), nullable=False)
    option_d = db.Column(db.String(200), nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)

class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chatbot')
def chatbot():
    return render_template('chatbot.html')

@app.route('/mock-exam', methods=['GET', 'POST'])
@login_required
def mock_exam():
    topics = [topic[0] for topic in Question.query.with_entities(Question.topic).distinct().all()]
    questions = None

    if request.method == 'POST':
        topic = request.form.get('topic')
        num_questions = int(request.form.get('num_questions'))

        questions = Question.query.filter_by(topic=topic).limit(num_questions).all()

    return render_template('mock_exam.html', topics=topics, questions=questions)

@app.route('/submit_exam', methods=['POST'])
@login_required
def submit_exam():
    score = 0
    form_data = request.form
    question_ids = [key.split('_')[1] for key in form_data.keys()]

    for q_id in question_ids:
        question = Question.query.get(q_id)
        if question and form_data.get(f'question_{q_id}') == question.correct_answer:
            score += 1

    flash(f'You scored {score} out of {len(question_ids)}!')
    return redirect(url_for('mock_exam'))

@app.route('/generate_flashcards', methods=['POST'])
@login_required
def generate_flashcards():
    data = request.get_json()
    messages = data.get('messages')

    # Dummy implementation: create one flashcard from the last user message
    if messages:
        last_user_message = None
        for msg in reversed(messages):
            if msg['sender'] == 'user':
                last_user_message = msg['message']
                break
        
        if last_user_message:
            # A more sophisticated implementation would use an LLM to extract key info
            flashcard = Flashcard(
                user_id=current_user.id,
                topic="Chatbot Session",
                question=last_user_message,
                answer="This is a dummy answer."
            )
            db.session.add(flashcard)
            db.session.commit()
            return jsonify({'message': 'Flashcards generated successfully!'})

    return jsonify({'error': 'Failed to generate flashcards'}), 400

@app.route('/flashcards')
@login_required
def flashcards():
    flashcards = Flashcard.query.filter_by(user_id=current_user.id).all()
    return render_template('flashcards.html', flashcards=flashcards)

@app.route('/level-test', methods=['GET', 'POST'])
@login_required
def level_test():
    if request.method == 'POST':
        score = 0
        form_data = request.form
        question_ids = [key.split('_')[1] for key in form_data.keys()]

        for q_id in question_ids:
            question = Question.query.get(q_id)
            if question and form_data.get(f'question_{q_id}') == question.correct_answer:
                score += 1
        
        total_questions = len(question_ids)
        percentage_score = (score / total_questions) * 100

        recommendation = ""
        if percentage_score < 50:
            recommendation = "You should focus on the basics. Try the easy questions in the question bank."
        elif 50 <= percentage_score < 80:
            recommendation = "You have a good understanding. Try the medium questions in the question bank."
        else:
            recommendation = "You have a strong understanding. Try the hard questions in the question bank."

        flash(f'You scored {score} out of {total_questions}!')
        flash(recommendation)
        return redirect(url_for('level_test'))

    questions = Question.query.order_by(db.func.random()).limit(5).all()
    return render_template('level_test.html', questions=questions)

@app.route('/question-bank', methods=['GET', 'POST'])
@login_required
def question_bank():
    topics = [topic[0] for topic in Question.query.with_entities(Question.topic).distinct().all()]
    questions = None

    if request.method == 'POST':
        topic = request.form.get('topic')
        difficulty = request.form.get('difficulty')

        query = Question.query
        if topic:
            query = query.filter_by(topic=topic)
        if difficulty:
            query = query.filter_by(difficulty=difficulty)
        
        questions = query.all()
    else:
        questions = Question.query.all()

    return render_template('question_bank.html', topics=topics, questions=questions)

@app.route('/add-question', methods=['GET', 'POST'])
@login_required
def add_question():
    if request.method == 'POST':
        topic = request.form.get('topic')
        difficulty = request.form.get('difficulty')
        question_text = request.form.get('question_text')
        option_a = request.form.get('option_a')
        option_b = request.form.get('option_b')
        option_c = request.form.get('option_c')
        option_d = request.form.get('option_d')
        correct_answer = request.form.get('correct_answer')

        new_question = Question(
            topic=topic,
            difficulty=difficulty,
            question_text=question_text,
            option_a=option_a,
            option_b=option_b,
            option_c=option_c,
            option_d=option_d,
            correct_answer=correct_answer
        )

        db.session.add(new_question)
        db.session.commit()
        flash('Question added successfully!')
        return redirect(url_for('question_bank'))

    return render_template('add_question.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('profile'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('profile'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('profile'))
    if request.method == 'POST':
        hashed_password = generate_password_hash(request.form['password'])
        new_user = User(username=request.form['username'], email=request.form['email'], password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        flash('Account created successfully!')
        login_user(new_user)  # Log in the user after registration
        return redirect(url_for('profile'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    subjects = [topic[0] for topic in Question.query.with_entities(Question.topic).distinct().all()]
    return render_template('profile.html', username=current_user.username, subjects=subjects)

@app.route('/api/chatbot', methods=['POST'])
@login_required
def api_chatbot():
    data = request.get_json()
    message = data.get('message')

    if not message:
        return jsonify({'error': 'No message provided'}), 400

    try:
        response = requests.post(
            'https://api.hackclub.com/v1/chats/completions',
            headers={
                'Authorization': 'Bearer YOUR_HACK_CLUB_API_KEY',  # Replace with your actual API key
                'Content-Type': 'application/json'
            },
            json={
                'model': 'gpt-3.5-turbo',
                'messages': [
                    {'role': 'system', 'content': 'You are a helpful IGCSE tutor.'},
                    {'role': 'user', 'content': message}
                ]
            }
        )
        response.raise_for_status()  # Raise an exception for bad status codes
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': str(e)}), 500

@app.route('/add_questions')
@login_required
def add_questions():
    questions = [
        Question(topic='Algebra', difficulty='Easy', question_text='Solve for x: 2x + 5 = 15', option_a='5', option_b='10', option_c='2.5', option_d='7.5', correct_answer='a'),
        Question(topic='Algebra', difficulty='Medium', question_text='Factorise: x^2 - 9', option_a='(x-3)(x+3)', option_b='(x-9)(x+1)', option_c='(x-3)(x-3)', option_d='(x+3)(x+3)', correct_answer='a'),
        Question(topic='Geometry', difficulty='Easy', question_text='What is the sum of angles in a triangle?', option_a='180 degrees', option_b='360 degrees', option_c='90 degrees', option_d='270 degrees', correct_answer='a')
    ]
    db.session.bulk_save_objects(questions)
    db.session.commit()
    return 'Questions added!'

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)