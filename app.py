from flask import Flask, request, jsonify
import requests
from flask_cors import CORS
import os
import psycopg2
import json
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
CORS(app)

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
    return psycopg2.connect(DATABASE_URL, sslmode='require')

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    password = generate_password_hash(data.get('password'))
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (email, password) VALUES (%s, %s) RETURNING id", (email, password))
        user_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'message': 'User registered', 'user_id': user_id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

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
            return jsonify({'error': 'Invalid credentials'}), 401
        return jsonify({'message': 'Login successful', 'user_id': user[0]}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/search_ingredient', methods=['GET'])
def search_ingredient():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify([]), 200
    params = {'api_key': USDA_API_KEY, 'query': query, 'pageSize': 15, 'dataType': ['Foundation', 'SR Legacy', 'Branded']}
    try:
        response = requests.get(FDC_API_URL, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        foods = data.get('foods', [])
        return jsonify(foods[:10]), 200
    except Exception as e:
        return jsonify({'error': 'Failed to fetch ingredients'}), 500

@app.route('/calculate_nutrition', methods=['POST'])
def calculate_nutrition():
    ingredients = request.json.get('ingredients', [])
    if not ingredients:
        return jsonify({'error': 'No ingredients provided'}), 400
    total_nutrients = {key: 0 for key in NUTRIENT_IDS}
    for ingredient in ingredients:
        fdc_id = ingredient.get('fdcId')
        quantity = float(ingredient.get('quantity', 0))
        scaling_factor = quantity / 100 if quantity > 0 else 0
        try:
            response = requests.get(f"{FDC_FOOD_URL}/{fdc_id}?api_key={USDA_API_KEY}")
            response.raise_for_status()
            food_data = response.json()
            nutrients = {n['nutrient']['id']: n.get('amount', 0) for n in food_data.get('foodNutrients', []) if 'nutrient' in n and 'id' in n['nutrient']}
            for key, nid in NUTRIENT_IDS.items():
                total_nutrients[key] += nutrients.get(nid, 0) * scaling_factor
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify(total_nutrients)

@app.route('/save_recipe', methods=['POST'])
def save_recipe():
    data = request.json
    user_id = data.get('user_id')
    name = data.get('name')
    ingredients = data.get('ingredients')
    nutrition = data.get('nutrition')
    if not user_id:
        return jsonify({'error': 'Missing user_id'}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO recipes (user_id, name, ingredients, nutrition) VALUES (%s, %s, %s, %s)", (user_id, name, json.dumps(ingredients), json.dumps(nutrition)))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'message': 'Recipe saved'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get_recipes', methods=['POST'])
def get_recipes():
    data = request.json
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'error': 'Missing user_id'}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, name, ingredients, nutrition, created_at FROM recipes WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        recipes = [
            {'id': r[0], 'name': r[1], 'ingredients': r[2], 'nutrition': r[3], 'created_at': r[4].isoformat()}
            for r in rows
        ]
        return jsonify(recipes)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)