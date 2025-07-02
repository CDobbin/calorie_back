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
import urllib.parse
import logging

load_dotenv()

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    logger.debug("Health check requested")
    return jsonify({'message': 'Calorie Calculator API is running'}), 200

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    # Input validation
    if not email or not password:
        logger.debug("Missing email or password")
        return error_response('Email and password are required', 400)
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
        logger.debug(f"Invalid email format: {email}")
        return error_response('Invalid email format', 400)
    if len(password) < 8 or not any(c.isupper() for c in password) or not any(c.isdigit() for c in password):
        logger.debug("Invalid password format")
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
        logger.debug(f"User registered: {email}")
        return jsonify({'message': 'User registered', 'user_id': user_id}), 201
    except psycopg.errors.UniqueViolation:
        logger.debug(f"Email already registered: {email}")
        return error_response('Email already registered', 400)
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        return error_response(str(e), 500)

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    logger.debug(f"Login attempt for email: {email}")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, password FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if not user or not check_password_hash(user[1], password):
            logger.debug("Invalid credentials")
            return error_response('Invalid credentials', 401)
        access_token = create_access_token(identity=user[0])
        logger.debug(f"Login successful for user_id: {user[0]}")
        return jsonify({'message': 'Login successful', 'access_token': access_token}), 200
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return error_response(str(e), 500)

@app.route('/search_ingredient', methods=['GET'])
@limiter.limit("10 per minute")
@jwt_required()
def search_ingredient():
    query = request.args.get('query', '').strip()
    if not query:
        logger.debug("No query provided")
        return jsonify([]), 200
    if not isinstance(query, str):
        logger.debug(f"Invalid query type: {type(query)}")
        return error_response('Query must be a string', 400)
    if len(query) < 3:
        logger.debug(f"Query too short: {query}")
        return jsonify([]), 200
    if not USDA_API_KEY:
        logger.debug("USDA_API_KEY is not set")
        return error_response('Server configuration error: API key missing', 500)
    encoded_query = urllib.parse.quote(query)
    params = {
        'api_key': USDA_API_KEY,
        'query': encoded_query
    }
    logger.debug(f"Sending USDA API request with query: {query}, encoded: {encoded_query}, params: {params}")
    try:
        response = requests.get(FDC_API_URL, params=params, timeout=5)
        logger.debug(f"USDA API request URL: {response.url}")
        response.raise_for_status()
        data = response.json()
        logger.debug(f"USDA API response: {json.dumps(data, indent=2)}")
        foods = data.get('foods', [])
        return jsonify(foods[:10]), 200
    except requests.exceptions.HTTPError as e:
        logger.debug(f"USDA API HTTP error: {str(e)}, Response: {e.response.text if e.response else 'No response'}")
        return error_response(f'Failed to fetch ingredients: {str(e)}', 500)
    except requests.exceptions.RequestException as e:
        logger.debug(f"USDA API request error: {str(e)}")
        return error_response(f'Failed to fetch ingredients: {str(e)}', 500)

@app.route('/calculate_nutrition', methods=['POST'])
@jwt_required()
def calculate_nutrition():
    try:
        ingredients = request.json.get('ingredients', [])
        if not ingredients:
            logger.debug("No ingredients provided")
            return error_response('No ingredients provided', 400)
        total_nutrients = {key: 0 for key in NUTRIENT_IDS}
        for ingredient in ingredients:
            fdc_id = ingredient.get('fdcId')
            quantity = float(ingredient.get('quantity', 0))
            if not fdc_id or quantity <= 0:
                logger.debug(f"Invalid ingredient data: fdc_id={fdc_id}, quantity={quantity}")
                return error_response('Invalid ingredient data', 400)
            scaling_factor = quantity / 100
            response = requests.get(f"{FDC_FOOD_URL}/{fdc_id}?api_key={USDA_API_KEY}", timeout=5)
            response.raise_for_status()
            food_data = response.json()
            nutrients = {n['nutrient']['id']: n.get('amount', 0) for n in food_data.get('foodNutrients', []) if 'nutrient' in n and 'id' in n['nutrient']}
            for key, nid in NUTRIENT_IDS.items():
                total_nutrients[key] += nutrients.get(nid, 0) * scaling_factor
        logger.debug(f"Calculated nutrients: {total_nutrients}")
        return jsonify(total_nutrients), 200
    except Exception as e:
        logger.error(f"Nutrition calculation error: {str(e)}")
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
        logger.debug(f"Missing required fields: user_id={user_id}, name={name}, ingredients={ingredients}, nutrition={nutrition}")
        return error_response('Missing required fields', 400)
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO recipes (user_id, name, ingredients, nutrition) VALUES (%s, %s, %s, %s)", 
                   (user_id, name, json.dumps(ingredients), json.dumps(nutrition)))
        conn.commit()
        cur.close()
        conn.close()
        logger.debug(f"Recipe saved for user_id: {user_id}, name: {name}")
        return jsonify({'message': 'Recipe saved'}), 201
    except Exception as e:
        logger.error(f"Save recipe error: {str(e)}")
        return error_response(str(e), 500)

@app.route('/get_recipes', methods=['POST'])
@jwt_required()
def get_recipes():
    user_id = get_jwt_identity()
    logger.debug(f"Fetching recipes for user_id: {user_id}")
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
        logger.debug(f"Retrieved {len(recipes)} recipes for user_id: {user_id}")
        return jsonify(recipes), 200
    except Exception as e:
        logger.error(f"Get recipes error: {str(e)}")
        return error_response(str(e), 500)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)