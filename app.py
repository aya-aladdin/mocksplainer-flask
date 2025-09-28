import os
import json
import re
import dirtyjson
from datetime import datetime
import urllib.request
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import Markup
import markdown
HACKCLUB_API_URL = "https://ai.hackclub.com/chat/completions"
IGCSE_INFO_TEXT = "The user is studying IGCSE level content in Math, Physics, Biology, and Chemistry. Focus your answers on curriculum topics."

app = Flask(__name__)
app.config['SECRET_KEY'] = 'SNSnT_LoJM8ejQ1GFtSJCdcrQJCg1NInP5Klbp68Rqs'

# Use Vercel Postgres in production, otherwise fall back to a local SQLite database.
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:////tmp/igcse_study.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@app.template_filter('markdown')
def markdown_to_html(text):
    return Markup(markdown.markdown(text))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(250), nullable=False)
    flashcards = db.relationship('Flashcard', backref='owner', lazy=True)

class Folder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(10), nullable=False, default='📁')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('folder.id'), nullable=True)
    
    parent = db.relationship('Folder', remote_side=[id], backref='subfolders')
    flashcards = db.relationship('Flashcard', backref='folder', lazy='dynamic')

class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey('folder.id'), nullable=True)

class FlashcardAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    flashcard_id = db.Column(db.Integer, db.ForeignKey('flashcard.id', ondelete='CASCADE'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    was_correct = db.Column(db.Boolean, nullable=False)

    user = db.relationship('User', backref=db.backref('attempts', lazy='dynamic'))
    flashcard = db.relationship('Flashcard', backref=db.backref('attempts', lazy='dynamic'))

class MockTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(150), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('mock_tests', lazy='dynamic'))
    questions = db.relationship('TestQuestion', backref='test', lazy='dynamic', cascade="all, delete-orphan")

class TestQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey('mock_test.id'), nullable=False)
    question_number = db.Column(db.Integer, nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    marks = db.Column(db.Integer, nullable=False)
    answer_text = db.Column(db.Text, nullable=False)
    model_answer = db.Column(db.Text, nullable=True) # New field for the model answer

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/save_flashcards', methods=['POST'])
@login_required
def save_flashcards():
    try:
        data = request.get_json()
        flashcards_data = data.get('flashcards', [])

        if not flashcards_data:
            return jsonify({'message': 'No flashcards provided to save.'}), 200

        new_flashcards = []
        for fc in flashcards_data:
            new_flashcard = Flashcard(
                user_id=current_user.id,
                topic=fc.get('topic', 'Unassigned'),
                question=fc.get('question'),
                answer=fc.get('answer'),
                folder_id=data.get('folder_id', None)
            )
            new_flashcards.append(new_flashcard)

        db.session.bulk_save_objects(new_flashcards)
        db.session.commit()
        return jsonify({'message': f'{len(new_flashcards)} flashcards saved successfully!'})

    except Exception as e:
        db.session.rollback()
        print(f"Error saving flashcards: {e}")
        return jsonify({'error': 'Failed to save flashcards due to a server error.'}), 500

@app.route('/generate_flashcards_ai', methods=['POST'])
@login_required
def generate_flashcards_ai():
    data = request.json
    text = data.get('text', '').strip()
    topic = data.get('topic', 'Generated').strip()

    if not text:
        return jsonify({'error': 'Please provide a topic or text to generate flashcards from.'}), 400

    try:
        system_prompt = (
            "You are an expert flashcard creation assistant. Based on the user's text, generate 5-7 concise, high-quality flashcards. "
            "Each flashcard must have a 'question' and an 'answer' field. "
            "Respond ONLY with a valid JSON array of objects. Do not include any other text, explanation, or markdown. "
            "Example: [{\"question\": \"What is the powerhouse of the cell?\", \"answer\": \"The mitochondria.\"}]"
        )
        
        api_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ]

        req = urllib.request.Request(
            HACKCLUB_API_URL,
            data=json.dumps({"model": "gpt-4o-mini", "messages": api_messages, "max_tokens": 1000}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )

        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                response_text = response.read().decode('utf-8')
                ai_response_data = json.loads(response_text)
                content = ai_response_data['choices'][0]['message']['content']
                
                json_start = content.find('[')
                json_end = content.rfind(']') + 1
                flashcards_json_str = content[json_start:json_end]
                
                flashcards_data = json.loads(flashcards_json_str)
                return jsonify({'flashcards': flashcards_data})
            else:
                return jsonify({'error': 'Failed to get a response from the AI.'}), 500
    except Exception as e:
        print(f"AI Flashcard Generation Error: {e}")
        return jsonify({'error': 'An error occurred while generating flashcards.'}), 500

@app.route('/create_folder', methods=['POST'])
@login_required
def create_folder():
    data = request.json
    name = data.get('name', '').strip()
    parent_id = data.get('parent_id')

    if not name:
        return jsonify({'error': 'Folder name cannot be empty.'}), 400

    try:
        new_folder = Folder(name=name, user_id=current_user.id, parent_id=parent_id)
        db.session.add(new_folder)
        db.session.commit()
        return jsonify({'id': new_folder.id, 'name': new_folder.name, 'parent_id': new_folder.parent_id})
    except Exception as e:
        db.session.rollback()
        print(f"Folder Creation Error: {e}")
        return jsonify({'error': 'Failed to create folder.'}), 500

@app.route('/generate_test_ai', methods=['POST'])
@login_required
def generate_test_ai():
    data = request.json
    exam_board = data.get('exam_board', 'IGCSE').strip()
    subject = data.get('subject', 'General').strip()
    topic = data.get('topic', '').strip()
    num_questions = data.get('num_questions', 10)
    total_marks = data.get('total_marks', 25)

    if not topic:
        return jsonify({'error': 'Please provide a topic for the test.'}), 400

    try:
        system_prompt = r"""
            You are an expert IGCSE exam paper creator. Your task is to generate a mock test based on user specifications.
            - Generate questions appropriate for the specified curriculum level.
            - Each question must have a 'question_number', 'question_text', 'marks', a 'model_answer', and an 'answer_text' (the mark scheme).
            - The 'question_text', 'model_answer', and 'answer_text' fields MUST all be formatted using Markdown.
            - The 'answer_text' (mark scheme) MUST follow IGCSE conventions:
                - Use a bulleted list for marking points.
                - Indicate the mark for each point in square brackets, e.g., `[1]`.
                - Underline or bold key terms required for the mark.
                - Use "OR" for alternative correct answers.
                - Example mark scheme point: "- **Movement** of particles from high to low concentration [1]"
            - The sum of marks should be close to the requested total.
            - Respond ONLY with a single valid JSON object inside a ```json ... ``` markdown block.
            - Do not include any reasoning, conversational text, or <think> tags in your response.
            - The JSON object must have a single root key called "questions", which contains an array of question objects.
        """
        
        user_prompt = (
            f"Generate a mock test with the following specifications:\n"
            f"- Exam Board: {exam_board}\n"
            f"- Subject: {subject}\n"
            f"- Topic: {topic}\n"
            f"- Number of Questions: {num_questions}\n"
            f"- Approximate Total Marks: {total_marks} (don't think, no: ```json``` tags)"
        )

        api_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        req = urllib.request.Request(
            HACKCLUB_API_URL,
            data=json.dumps({"model": "gpt-4o-mini", "messages": api_messages, "max_tokens": 2000}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )

        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                response_text = response.read().decode('utf-8')
                ai_response_data = json.loads(response_text)
                content = ai_response_data['choices'][0]['message']['content']

                try:
                    # Pre-process to remove any <think>...</think> blocks
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)

                    # 1. Extract the JSON part of the string to remove any leading/trailing text from the AI.
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if not json_match:
                        raise ValueError(f"No JSON object found in AI response. Content: {content}")
                    
                    json_string = json_match.group(0)
                    
                    # 2. Use dirtyjson to parse the extracted, potentially malformed JSON.
                    test_data = dirtyjson.loads(json_string)
                except Exception as e:
                    print(f"AI Test Generation Error: Failed to parse AI response. Error: {e}")
                    raise ValueError(f"Could not parse the AI's response. Raw content: {content}")

                questions_data = test_data.get('questions', [])

                if not questions_data:
                    raise ValueError("AI returned a valid response but with no questions in it.")

                new_test = MockTest(user_id=current_user.id, topic=topic)
                db.session.add(new_test)
                db.session.flush()

                for q_data in questions_data:
                    new_question = TestQuestion(
                        test_id=new_test.id,
                        question_number=q_data.get('question_number'),
                        question_text=q_data.get('question_text'),
                        marks=q_data.get('marks'),
                        answer_text=q_data.get('answer_text'),
                        model_answer=q_data.get('model_answer')
                    )
                    db.session.add(new_question)
                
                db.session.commit()
                return jsonify({'message': 'Test generated successfully!', 'test_id': new_test.id})
    except Exception as e:
        db.session.rollback()
        print(f"AI Test Generation Error: {e}")
        return jsonify({'error': f'An error occurred while generating the test. The AI may have returned an invalid format. Details: {str(e)}'}), 500

@app.route('/get_learn_session_flashcards', methods=['POST'])
@login_required
def get_learn_session_flashcards():
    data = request.json
    folder_ids = data.get('folder_ids', [])
    
    all_flashcards = []
    
    def fetch_flashcards_recursive(folder_id):
        folder = Folder.query.get(folder_id)
        if not folder or folder.user_id != current_user.id:
            return

        for fc in folder.flashcards:
            all_flashcards.append({
                'id': fc.id, 'question': fc.question, 'answer': fc.answer, 'topic': fc.topic
            })
        
        for subfolder in folder.subfolders:
            fetch_flashcards_recursive(subfolder.id)

    for f_id in folder_ids:
        fetch_flashcards_recursive(f_id)

    unique_flashcards = list({fc['id']: fc for fc in all_flashcards}.values())
    
    return jsonify({'flashcards': unique_flashcards})

@app.route('/record_learn_attempt', methods=['POST'])
@login_required
def record_learn_attempt():
    data = request.json
    flashcard_id = data.get('flashcard_id')
    was_correct = data.get('was_correct')

    if flashcard_id is None or was_correct is None:
        return jsonify({'error': 'Missing data.'}), 400

    attempt = FlashcardAttempt(user_id=current_user.id, flashcard_id=flashcard_id, was_correct=was_correct)
    db.session.add(attempt)
    db.session.commit()
    return jsonify({'message': 'Attempt recorded.'})

@login_required
@app.route('/update_item', methods=['POST'])
def update_item():
    data = request.json
    item_id = data.get('item_id')
    item_type = data.get('item_type')
    new_name = data.get('name')
    new_icon = data.get('icon')

    try:
        if item_type == 'folder':
            item = Folder.query.get(item_id)
            if item and item.user_id == current_user.id:
                if new_name: item.name = new_name
                if new_icon: item.icon = new_icon
        elif item_type == 'flashcard':
            item = Flashcard.query.get(item_id)
            if item and item.owner.id == current_user.id:
                if new_name: item.question = new_name
        
        db.session.commit()
        return jsonify({'message': 'Item updated successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to update item.'}), 500

@app.route('/delete_item', methods=['POST'])
@login_required
def delete_item():
    data = request.json
    item_id = data.get('item_id')
    item_type = data.get('item_type')

    try:
        if item_type == 'flashcard':
            item = Flashcard.query.get(item_id)
            if item and item.user_id == current_user.id:
                db.session.delete(item)
        elif item_type == 'folder':
            item = Folder.query.get(item_id)
            if item and item.user_id == current_user.id:
                if item.subfolders or item.flashcards.count() > 0:
                    return jsonify({'error': 'Folder is not empty.'}), 400
                db.session.delete(item)
        db.session.commit()
        return jsonify({'message': 'Item deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete item.'}), 500

@app.route('/delete_items_bulk', methods=['POST'])
@login_required
def delete_items_bulk():
    data = request.json
    items_to_delete = data.get('items', [])

    if not items_to_delete: 
        return jsonify({'message': 'No items selected for deletion.'}), 200

    try:
        for item_data in items_to_delete:
            item_id = item_data.get('id')
            item_type = item_data.get('type')

            if item_type == 'flashcard':
                item = Flashcard.query.get(item_id)
                if item and item.user_id == current_user.id:
                    db.session.delete(item)
            elif item_type == 'folder':
                item = Folder.query.get(item_id)
                if item and item.user_id == current_user.id:
                    if item.subfolders or item.flashcards.count() > 0:
                        db.session.rollback()
                        return jsonify({'error': f'Cannot delete non-empty folder: "{item.name}".'}), 400
                    db.session.delete(item)
        
        db.session.commit()
        return jsonify({'message': 'Selected items deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        print(f"Bulk Delete Error: {e}")
        return jsonify({'error': 'An error occurred during bulk deletion.'}), 500

@app.route('/delete_test/<int:test_id>', methods=['DELETE'])
@login_required
def delete_test(test_id):
    test = MockTest.query.get_or_404(test_id)
    if test.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        db.session.delete(test)
        db.session.commit()
        return jsonify({'message': 'Test deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to delete test.'}), 500

@app.route('/move_items_bulk', methods=['POST'])
@login_required
def move_items_bulk():
    data = request.json
    items_to_move = data.get('items', [])
    target_folder_id = data.get('target_folder_id')

    try:
        for item_data in items_to_move:
            item_id = item_data.get('id')
            item_type = item_data.get('type')
            move_item_logic(item_id, item_type, target_folder_id, current_user.id)
        db.session.commit()
        return jsonify({'message': 'Items moved successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to move items.'}), 500

@app.route('/move_item', methods=['POST'])
@login_required
def move_item():
    data = request.json
    item_id = data.get('item_id')
    item_type = data.get('item_type')
    target_folder_id = data.get('target_folder_id') 

    try:
        move_item_logic(item_id, item_type, target_folder_id, current_user.id)
        db.session.commit()
        return jsonify({'message': 'Item moved successfully.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to move item.'}), 500

def move_item_logic(item_id, item_type, target_folder_id, user_id):
    if item_type == 'flashcard':
        item = Flashcard.query.get(item_id)
        if item and item.user_id == user_id:
            item.folder_id = target_folder_id
    elif item_type == 'folder':
        item = Folder.query.get(item_id)
        if item and item.user_id == user_id:
            item.parent_id = target_folder_id

@app.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.json
    messages = data.get("messages", [])

    if not messages:
        return jsonify({"reply": "⚠️ No message provided."})

    try:
        system_content = (
            "You are a helpful and strict AI tutor. You are speaking directly to the user. "
            "Your responses must be concise, straightforward, and in Markdown format. "
            "Provide the final answer directly. DO NOT show your reasoning process, thoughts, or self-correction. Never refer to the user in the third person (e.g., 'the user is asking')."
        )

        # Modify the last user message to include the instruction
        modified_messages = messages.copy()
        if modified_messages:
            modified_messages[-1]['content'] += " (don't think)"

        api_messages = [{"role": "system", "content": system_content}] + modified_messages
        
        req = urllib.request.Request(
            HACKCLUB_API_URL,
            data=json.dumps({
                "model": "gpt-4o-mini",
                "messages": api_messages,
                "max_tokens": 800
            }).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                response_data = json.loads(response.read().decode('utf-8'))
                content = response_data['choices'][0]['message']['content']

                reply = content.strip()
                return jsonify({"reply": reply})
            else:
                error_body = response.read().decode('utf-8', errors='ignore')
                print(f"Hack Club API Error Status {response.status}: {error_body}")
                return jsonify({"reply": "⚠️ Error connecting to Hack Club AI API."})

    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}, {e.read().decode('utf-8', errors='ignore')}")
        return jsonify({"reply": "⚠️ Error connecting to Hack Club AI API due to an HTTP error."})
    except Exception as e:
        print("General Error:", e)
        return jsonify({"reply": "⚠️ An unexpected error occurred while processing the request."})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('profile'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user, remember=True)
            return redirect(url_for('profile'))
        flash('Invalid username or password', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('profile'))
    if request.method == 'POST':
        try:
            hashed_password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
            new_user = User(username=request.form['username'], email=request.form['email'], password=hashed_password)
            db.session.add(new_user)
            db.session.commit()
            flash('Account created successfully! You are now logged in.', 'success')
            login_user(new_user)
            return redirect(url_for('profile'))
        except Exception as e:
            db.session.rollback()
            flash('Registration failed. Username or Email might already be taken.', 'error')
            print(f"Registration Error: {e}")
            return redirect(url_for('register'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('profile'))
    return render_template('index.html')

@app.route('/profile')
@login_required
def profile():
    total_flashcards = Flashcard.query.filter_by(user_id=current_user.id).count()
    
    attempts = db.session.query(FlashcardAttempt).join(Flashcard).filter(Flashcard.user_id == current_user.id).all()
    total_questions_answered = len(attempts)

    user_flashcards = Flashcard.query.filter_by(user_id=current_user.id).options(db.joinedload(Flashcard.folder)).all()
    topics_dict = {}
    for fc in user_flashcards:
        topic = fc.topic
        if topic == 'Manual':
            if fc.folder:
                topic = fc.folder.name
            else:
                topic = 'General'
        topics_dict[topic] = topics_dict.get(topic, 0) + 1

    performance_data = {}
    for attempt in attempts:
        topic = attempt.flashcard.topic
        if topic not in performance_data:
            performance_data[topic] = {'correct': 0, 'total': 0}
        performance_data[topic]['total'] += 1
        if attempt.was_correct:
            performance_data[topic]['correct'] += 1
    
    topic_performance = []
    for topic, data in performance_data.items():
        accuracy = (data['correct'] / data['total']) * 100 if data['total'] > 0 else 0
        topic_performance.append({'topic': topic, 'accuracy': round(accuracy)})
    topic_performance.sort(key=lambda x: x['accuracy'], reverse=True)

    recently_studied_attempts = db.session.query(FlashcardAttempt).join(Flashcard).filter(
        Flashcard.user_id == current_user.id,
        Flashcard.folder_id.isnot(None)
    ).order_by(FlashcardAttempt.timestamp.desc()).limit(20).all()
    
    recent_folders = []
    seen_folder_ids = set()
    for attempt in recently_studied_attempts:
        if attempt.flashcard.folder and attempt.flashcard.folder.id not in seen_folder_ids:
            recent_folders.append(attempt.flashcard.folder)
            seen_folder_ids.add(attempt.flashcard.folder.id)

    return render_template('profile.html', total_flashcards=total_flashcards, topic_performance=topic_performance, recent_folders=recent_folders[:5], topics_data=topics_dict, total_questions_answered=total_questions_answered)

@app.route('/tests')
@login_required
def tests():
    page = request.args.get('page', 1, type=int)
    tests_pagination = MockTest.query.filter_by(user_id=current_user.id).order_by(MockTest.timestamp.desc()).paginate(page=page, per_page=10, error_out=False)
    return render_template('tests.html', tests_pagination=tests_pagination)

@app.route('/tests/<int:test_id>')
@login_required
def take_test(test_id):
    test = MockTest.query.get_or_404(test_id)
    if test.user_id != current_user.id:
        return "Unauthorized", 403
    return render_template('take_test.html', test=test)

@app.route('/chatbot')
@login_required
def chatbot():
    return render_template('chatbot.html')

@app.route('/flashcards')
@login_required
def flashcards():
    top_level_folders = Folder.query.filter_by(user_id=current_user.id, parent_id=None).all()
    top_level_flashcards = Flashcard.query.filter_by(user_id=current_user.id, folder_id=None).all()

    def build_folder_tree(folder):
        return {
            'id': folder.id,
            'name': folder.name,
            'icon': folder.icon,
            'subfolders': [build_folder_tree(sub) for sub in folder.subfolders],
            'flashcards': [{'id': fc.id, 'question': fc.question, 'answer': fc.answer, 'topic': fc.topic} for fc in folder.flashcards]
        }

    folder_structure = [build_folder_tree(f) for f in top_level_folders]
    root_flashcards_data = [{'id': fc.id, 'question': fc.question, 'answer': fc.answer, 'topic': fc.topic} for fc in top_level_flashcards]

    
    return render_template('flashcards.html', folder_structure=folder_structure, root_flashcards=root_flashcards_data)


if __name__ == '__main__':
    app.run(debug=True, port=5001)
