from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import os
import psycopg
import json
from werkzeug.security import generate_password_hash, check_password_hash
import re
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# JWT Configuration
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY')
jwt = JWTManager(app)

# Rate Limiter Configuration
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

USDA_API_KEY = os.getenv("USDA_API_KEY")
FDC_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FDC_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food"
DATABASE_URL = os.getenv("DATABASE_URL")

NUTRIENT_IDS = {
    'calories': 1008,
    'protein': 1003,
    'fat': 1004,
    'carbohydrates': 1005,
    'fiber': 1079
}

def get_db():
    return psycopg.connect(DATABASE_URL, sslmode='require')

def error_response(message, status_code):
    return jsonify({'error': message, 'status': status_code}), status_code

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({'message': 'Calorie Calculator API is running'}), 200

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    # Input validation
    if not email or not password:
        return error_response('Email and password are required', 400)
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
        return error_response('Invalid email format', 400)
    if len(password) < 8 or not any(c.isupper() for c in password) or not any(c.isdigit() for c in password):
        return error_response('Password must be 8+ characters with uppercase and number', 400)
    
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (email, password) VALUES (%s, %s) RETURNING id", 
                   (email, generate_password_hash(password)))
        user_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'message': 'User registered', 'user_id': user_id}), 201
    except psycopg.errors.UniqueViolation:
        return error_response('Email already registered', 400)
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, password FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if not user or not check_password_hash(user[1], password):
            return error_response('Invalid credentials', 401)
        access_token = create_access_token(identity=user[0])
        return jsonify({'message': 'Login successful', 'access_token': access_token}), 200
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/search_ingredient', methods=['GET'])
@limiter.limit("10 per minute")
@jwt_required()
def search_ingredient():
    query = request.args.get('query', '').strip()
    if not query:
        print("No query provided")
        return jsonify([]), 200
    if not isinstance(query, str):
        print(f"Invalid query type: {type(query)}")
        return error_response('Query must be a string', 400)
    params = {
        'api_key': USDA_API_KEY,
        'query': query,
        'pageSize': 15,
        'dataType': 'Foundation,SR Legacy,Branded'
    }
    print(f"Sending USDA API request with query: {query}, params: {params}")
    try:
        response = requests.get(FDC_API_URL, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        print(f"USDA API response: {json.dumps(data, indent=2)}")
        foods = data.get('foods', [])
        return jsonify(foods[:10]), 200
    except requests.exceptions.HTTPError as e:
        print(f"USDA API HTTP error: {str(e)}")
        return error_response(f'Failed to fetch ingredients: {str(e)}', 500)
    except requests.exceptions.RequestException as e:
        print(f"USDA API request error: {str(e)}")
        return error_response(f'Failed to fetch ingredients: {str(e)}', 500)

@app.route('/calculate_nutrition', methods=['POST'])
@jwt_required()
def calculate_nutrition():
    try:
        ingredients = request.json.get('ingredients', [])
        if not ingredients:
            return error_response('No ingredients provided', 400)
        total_nutrients = {key: 0 for key in NUTRIENT_IDS}
        for ingredient in ingredients:
            fdc_id = ingredient.get('fdcId')
            quantity = float(ingredient.get('quantity', 0))
            if not fdc_id or quantity <= 0:
                return error_response('Invalid ingredient data', 400)
            scaling_factor = quantity / 100
            response = requests.get(f"{FDC_FOOD_URL}/{fdc_id}?api_key={USDA_API_KEY}", timeout=5)
            response.raise_for_status()
            food_data = response.json()
            nutrients = {n['nutrient']['id']: n.get('amount', 0) for n in food_data.get('foodNutrients', []) if 'nutrient' in n and 'id' in n['nutrient']}
            for key, nid in NUTRIENT_IDS.items():
                total_nutrients[key] += nutrients.get(nid, 0) * scaling_factor
        return jsonify(total_nutrients), 200
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/save_recipe', methods=['POST'])
@jwt_required()
def save_recipe():
    data = request.json
    user_id = get_jwt_identity()
    name = data.get('name')
    ingredients = data.get('ingredients')
    nutrition = data.get('nutrition')
    if not all([user_id, name, ingredients, nutrition]):
        return error_response('Missing required fields', 400)
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO recipes (user_id, name, ingredients, nutrition) VALUES (%s, %s, %s, %s)", 
                   (user_id, name, json.dumps(ingredients), json.dumps(nutrition)))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'message': 'Recipe saved'}), 201
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/get_recipes', methods=['POST'])
@jwt_required()
def get_recipes():
    user_id = get_jwt_identity()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name, ingredients, nutrition, created_at FROM recipes WHERE user_id = %s ORDER BY created_at DESC", 
                   (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        recipes = [
            {'id': r[0], 'name': r[1], 'ingredients': json.loads(r[2]), 'nutrition': json.loads(r[3]), 
             'created_at': r[4].isoformat()}
            for r in rows
        ]
        return jsonify(recipes), 200
    except Exception as e:
        return error_response(str(e), 500)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)