from flask import Flask, render_template, request, redirect, url_for
import plotly.graph_objects as go
from plotly.offline import plot
import os
import requests

app = Flask(__name__)

#http://127.0.0.1:5000

def get_distance_and_duration(origin, destination, mode='driving'):
    api_key = os.getenv('GOOGLE_MAPS_API_KEY')
    if not api_key:
        return {'error': 'Missing GOOGLE_MAPS_API_KEY in environment.'}

    url = 'https://maps.googleapis.com/maps/api/distancematrix/json'
    params = {
        'origins': origin,
        'destinations': destination,
        'mode': mode,
        'key': api_key,
        'units': 'metric'
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return {'error': f'Request to Google Maps API failed: {e}'}

    if data.get('status') != 'OK':
        return {'error': f"Google Maps API status: {data.get('status')}. {data.get('error_message', '')}"}

    row = data['rows'][0]
    element = row['elements'][0]

    if element.get('status') != 'OK':
        return {'error': f"Route status: {element.get('status')}"}

    return {
        'distance_text': element['distance']['text'],
        'distance_meters': element['distance']['value'],
        'duration_text': element['duration']['text'],
        'duration_seconds': element['duration']['value'],
        'origin': origin,
        'destination': destination,
        'mode': mode
    }


def fetch_ec3_epd(product_name):
    api_key = os.getenv('EC3_API_KEY')
    if not api_key:
        return {'error': 'Missing EC3_API_KEY environment variable', 'product': product_name}

    # Example endpoint, replace with actual EC3 API URL if different
    url = 'https://buildingtransparency.org/ec3/api/v1/products'
    params = {'search': product_name}
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {api_key}'}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return {'error': f'EC3 request failed for {product_name}: {e}', 'product': product_name}

    if not data:
        return {'error': f'No result from EC3 for {product_name}', 'product': product_name}

    # Adapt to API structure, use best-effort fields
    product_info = data.get('data') if isinstance(data, dict) else data
    if isinstance(product_info, dict):
        hit = product_info.get('results') or product_info.get('items') or product_info
        if isinstance(hit, list) and len(hit) > 0:
            first = hit[0]
            return {
                'product': product_name,
                'id': first.get('id') or first.get('product_id'),
                'name': first.get('name') or first.get('description') or str(first),
                'raw': first
            }
        return {'error': f'No EC3 matches for {product_name}', 'product': product_name}

    return {'product': product_name, 'data': product_info}


def create_sunburst_figure(result):
    cement = float(result.get('cement_kg', 0) or 0)
    sand = float(result.get('sand_kg', 0) or 0)
    aggregate = float(result.get('aggregate_kg', 0) or 0)
    water = float(result.get('water_l', 0) or 0)
    admixture = float(result.get('admixture_ml', 0) or 0)
    mix_total = float(result.get('mix_total_kg', 0) or 0)

    cement_1 = round(cement * 0.7, 2)
    cement_2 = round(cement - cement_1, 2)
    sand_1 = round(sand * 0.6, 2)
    sand_2 = round(sand - sand_1, 2)
    agg_1 = round(aggregate * 0.55, 2)
    agg_2 = round(aggregate - agg_1, 2)

    labels = [
        'Concrete',
        'Cement', 'Sand', 'Aggregate', 'Water', 'Admixture',
        'Portland Cement', 'Fly Ash',
        'Fine Sand', 'Coarse Sand',
        'Gravel', 'Crushed Stone',
        'Plasticizer'
    ]

    parents = [
        '',
        'Concrete', 'Concrete', 'Concrete', 'Concrete', 'Concrete',
        'Cement', 'Cement',
        'Sand', 'Sand',
        'Aggregate', 'Aggregate',
        'Admixture'
    ]

    values = [
        mix_total,
        cement, sand, aggregate, water, admixture,
        cement_1, cement_2,
        sand_1, sand_2,
        agg_1, agg_2,
        admixture
    ]

    fig = go.Figure(go.Sunburst(
        labels=labels,
        parents=parents,
        values=values,
        branchvalues='total',
        maxdepth=3
    ))
    fig.update_layout(margin=dict(t=40, l=0, r=0, b=0))
    return plot(fig, output_type='div', include_plotlyjs='cdn')


@app.route('/', methods=['GET', 'POST'])
def home():
    result = None
    form_data = {
        'length': '',
        'width': '',
        'height': '',
        'cement': '',
        'sand': '',
        'aggregate': '',
        'water': '',
        'admixture': ''
    }

    origin = ''
    destination = ''
    mode = 'driving'
    distance_result = None
    route_results = []
    ec3_results = []
    percent_by_row = {}
    sunburst_div = None

    if request.method == 'POST':
        action = request.form.get('action', 'concrete')

        if action == 'distance':
            origin = request.form.get('origin', '').strip()
            destination = request.form.get('destination', '').strip()
            mode = request.form.get('mode', 'driving')

            if origin and destination:
                distance_result = get_distance_and_duration(origin, destination, mode)
            else:
                distance_result = {'error': 'Both origin and destination are required.'}

            # preserve calculator inputs if submitted together
            for key in form_data.keys():
                form_data[key] = request.form.get(key, '')

        else:
            for key in form_data.keys():
                form_data[key] = request.form.get(key, '')

            # validate table text fields
            table_missing = False
            for field_prefix in ['scm', 'agg', 'adm']:
                row_count = int(request.form.get(f'{field_prefix}_row_count', '1') or '1')
                for i in range(1, row_count + 1):
                    t = request.form.get(f'{field_prefix}_type_{i}', '').strip()
                    old = request.form.get(f'{field_prefix}_old_{i}', '').strip()
                    new = request.form.get(f'{field_prefix}_new_{i}', '').strip()
                    trans = request.form.get(f'{field_prefix}_transport_{i}', '').strip()
                    amt = request.form.get(f'{field_prefix}_amount_{i}', '').strip()
                    any_field = any([t, old, new, trans, amt])
                    all_fields = all([t, old, new, trans, amt])
                    if any_field and not all_fields:
                        table_missing = True
                        break
                if table_missing:
                    break

            if table_missing:
                result = {'error': 'Please fill in all table fields before calculating.'}
            else:
                ec3_results = []

                # collect amount data for percent calculations
                row_amounts = []
                categories = [
                    ('SCM', 'scm'),
                    ('Aggregate', 'agg'),
                    ('Admixture', 'adm')
                ]
                for label_prefix, field_prefix in categories:
                    row_count = int(request.form.get(f'{field_prefix}_row_count', '1') or '1')
                    for i in range(1, row_count + 1):
                        amount_str = request.form.get(f'{field_prefix}_amount_{i}', '').strip()
                        try:
                            amount_val = float(amount_str) if amount_str else 0.0
                        except ValueError:
                            amount_val = 0.0
                        row_amounts.append((field_prefix, i, amount_val))

                total_amount = sum([amt for _, _, amt in row_amounts])
                percent_by_row = {}
                for field_prefix, i, amt in row_amounts:
                    key = f'{field_prefix}_{i}'
                    percent_by_row[key] = round((amt / total_amount * 100) if total_amount > 0 else 0.0, 2)

                # calculate EC3 metadata for each Type value in all rows
                for label_prefix, field_prefix in categories:
                    row_count = int(request.form.get(f'{field_prefix}_row_count', '1') or '1')
                    for i in range(1, row_count + 1):
                        product_name = request.form.get(f'{field_prefix}_type_{i}', '').strip()
                        if product_name:
                            ec3_data = fetch_ec3_epd(product_name)
                            ec3_results.append({
                                'category': label_prefix,
                                'label': f'{label_prefix}{i}',
                                'product': product_name,
                                'epd': ec3_data,
                                'percent': percent_by_row.get(f'{field_prefix}_{i}', 0.0)
                            })

                # calculate distances for each table row route
                for label_prefix, field_prefix in categories:
                    row_count = int(request.form.get(f'{field_prefix}_row_count', '1') or '1')
                    for i in range(1, row_count + 1):
                        old_addr = request.form.get(f'{field_prefix}_old_{i}', '').strip()
                        new_addr = request.form.get(f'{field_prefix}_new_{i}', '').strip()
                        if old_addr and new_addr:
                            step_distance = get_distance_and_duration(old_addr, new_addr)
                            route_results.append({
                                'category': label_prefix,
                                'label': f'{label_prefix}{i}',
                                'origin': old_addr,
                                'destination': new_addr,
                                'transport': request.form.get(f'{field_prefix}_transport_{i}', '').strip(),
                                'distance': step_distance
                            })

                try:
                    length = float(form_data['length'])
                    width = float(form_data['width'])
                    height = float(form_data['height'])
                    cement = float(form_data['cement'])
                    sand = float(form_data['sand'])
                    aggregate = float(form_data['aggregate'])
                    water = float(form_data['water'])
                    admixture = float(form_data['admixture'])

                    volume = length * width * height
                    mix_total = cement + sand + aggregate
                    water_cement_ratio = water / cement if cement != 0 else 0

                    result = {
                        'volume_m3': round(volume, 4),
                        'cement_kg': round(cement, 2),
                        'sand_kg': round(sand, 2),
                        'aggregate_kg': round(aggregate, 2),
                        'water_l': round(water, 2),
                        'admixture_ml': round(admixture, 2),
                        'mix_total_kg': round(mix_total, 2),
                        'water_cement_ratio': round(water_cement_ratio, 2)
                    }
                except ValueError:
                    result = {'error': 'Please enter numeric values for all concrete fields.'}

            if result and not result.get('error'):
                sunburst_div = create_sunburst_figure(result)

    return render_template('index.html', result=result, form_data=form_data, origin=origin, destination=destination, mode=mode, distance_result=distance_result, route_results=route_results, ec3_results=ec3_results, percent_by_row=percent_by_row, sunburst_div=sunburst_div)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    message = None
    if request.method == 'POST':
        name = request.form.get('name', 'Guest')
        email = request.form.get('email', '')
        message_text = request.form.get('message', '')
        # In a real app you'd save/send the message. Here we just acknowledge.
        message = f'Thanks {name}! We received your message.'
    return render_template('contact.html', message=message)


@app.route('/sunburst_example')
def sunburst_example():
    sample_result = {
        'mix_total_kg': 280,
        'cement_kg': 80,
        'sand_kg': 100,
        'aggregate_kg': 98,
        'admixture_ml': 2,
    }
    sunburst_div = create_sunburst_figure(sample_result)
    return render_template('sunburst_example.html', sunburst_div=sunburst_div)


@app.route('/help')
def help_page():
    return render_template('help.html')


if __name__ == '__main__':
    app.run(debug=True)
