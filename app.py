from flask import Flask, request, jsonify
import requests
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["https://cdobbin.github.io", "http://localhost:8000"]}})

USDA_API_KEY = os.getenv("USDA_API_KEY", "s5eJOQy3E9zitYnZsYQBShtSbfdOfNFPdu9kVnn0")
FDC_API_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FDC_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food"

NUTRIENT_IDS = {
    'calories': 1008,
    'protein': 1003,
    'fat': 1004,
    'carbohydrates': 1005,
    'fiber': 1079
}

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
        print(f"API request failed: {str(e)}")
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
            nutrients = {
                n['nutrient']['id']: n.get('amount', 0)
                for n in food_data.get('foodNutrients', [])
                if 'nutrient' in n and 'id' in n['nutrient']
            }
            for key, nid in NUTRIENT_IDS.items():
                total_nutrients[key] += nutrients.get(nid, 0) * scaling_factor
        except Exception as e:
            print(f"Error fetching data for fdcId {fdc_id}: {e}")
            return jsonify({'error': str(e)}), 500

    return jsonify(total_nutrients)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)