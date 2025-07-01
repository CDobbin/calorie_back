from flask import Flask, request, jsonify
import requests
from flask_cors import CORS
import os
import sqlite3
from sqlite3 import Error

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["https://cdobbin.github.io", "http://localhost:8000"]}})  # Allow GitHub Pages and local frontend

# USDA API key from environment variable
USDA_API_KEY = os.getenv("USDA_API_KEY", "YOUR_USDA_API_KEY")

# USDA FoodData Central API endpoint
FDC_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FDC_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food"

# SQLite database setup
DATABASE = "nutrition_cache.db"

def init_db():
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS food_nutrients (
                fdc_id TEXT PRIMARY KEY,
                description TEXT,
                calories REAL,
                protein REAL,
                fat REAL,
                carbohydrates REAL,
                fiber REAL
            )
        ''')
        conn.commit()
        conn.close()
    except Error as e:
        print(f"Error initializing database: {e}")

# Initialize database on startup
init_db()

def get_cached_nutrients(fdc_id):
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM food_nutrients WHERE fdc_id = ?', (fdc_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'calories': row[2],
                'protein': row[3],
                'fat': row[4],
                'carbohydrates': row[5],
                'fiber': row[6]
            }
        return None
    except Error as e:
        print(f"Error accessing cache: {e}")
        return None

def cache_nutrients(fdc_id, description, nutrients):
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO food_nutrients (fdc_id, description, calories, protein, fat, carbohydrates, fiber)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            fdc_id,
            description,
            nutrients.get('Energy', {}).get('value', 0),
            nutrients.get('Protein', {}).get('value', 0),
            nutrients.get('Total lipid (fat)', {}).get('value', 0),
            nutrients.get('Carbohydrate, by difference', {}).get('value', 0),
            nutrients.get('Fiber, total dietary', {}).get('value', 0)
        ))
        conn.commit()
        conn.close()
    except Error as e:
        print(f"Error caching nutrients: {e}")

@app.route('/search_ingredient', methods=['GET'])
def search_ingredient():
    query = request.args.get('query')
    if not query:
        return jsonify({'error': 'No query provided'}), 400
    
    params = {
        'api_key': USDA_API_KEY,
        'query': query,
        'pageSize': 10,
        'dataType': ['Foundation', 'SR Legacy']
    }
    
    try:
        response = requests.get(FDC_API_URL, params=params)
        response.raise_for_status()
        data = response.json()
        return jsonify(data.get('foods', []))
    except requests.RequestException as e:
        return jsonify({'error': str(e)}), 500

@app.route('/calculate_nutrition', methods=['POST'])
def calculate_nutrition():
    ingredients = request.json.get('ingredients', [])
    if not ingredients:
        return jsonify({'error': 'No ingredients provided'}), 400
    
    total_nutrients = {
        'calories': 0,
        'protein': 0,
        'fat': 0,
        'carbohydrates': 0,
        'fiber': 0
    }
    
    for ingredient in ingredients:
        fdc_id = ingredient.get('fdcId')
        quantity = float(ingredient.get('quantity', 0))  # Quantity in grams
        
        # Check cache first
        cached = get_cached_nutrients(fdc_id)
        if cached:
            scaling_factor = quantity / 100  # USDA data is per 100g
            total_nutrients['calories'] += cached['calories'] * scaling_factor
            total_nutrients['protein'] += cached['protein'] * scaling_factor
            total_nutrients['fat'] += cached['fat'] * scaling_factor
            total_nutrients['carbohydrates'] += cached['carbohydrates'] * scaling_factor
            total_nutrients['fiber'] += cached['fiber'] * scaling_factor
            continue
        
        # Fetch from USDA API if not cached
        try:
            response = requests.get(f"{FDC_FOOD_URL}/{fdc_id}?api_key={USDA_API_KEY}")
            response.raise_for_status()
            food_data = response.json()
            
            nutrients = {n['nutrientName']: n for n in food_data.get('foodNutrients', [])}
            
            # Cache the data
            cache_nutrients(fdc_id, food_data.get('description', 'Unknown'), nutrients)
            
            # Calculate nutrients
            scaling_factor = quantity / 100  # USDA data is per 100g
            total_nutrients['calories'] += nutrients.get('Energy', {}).get('value', 0) * scaling_factor
            total_nutrients['protein'] += nutrients.get('Protein', {}).get('value', 0) * scaling_factor
            total_nutrients['fat'] += nutrients.get('Total lipid (fat)', {}).get('value', 0) * scaling_factor
            total_nutrients['carbohydrates'] += nutrients.get('Carbohydrate, by difference', {}).get('value', 0) * scaling_factor
            total_nutrients['fiber'] += nutrients.get('Fiber, total dietary', {}).get('value', 0) * scaling_factor
            
        except requests.RequestException as e:
            return jsonify({'error': f"Error fetching data for fdcId {fdc_id}: {str(e)}"}), 500
    
    return jsonify(total_nutrients)

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)