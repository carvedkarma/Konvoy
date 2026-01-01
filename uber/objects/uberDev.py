import requests
import time
import json
import math

from source.cred import loc_headers, fare_cookies, fare_headers, fare_query, flight_cookies, flight_headers
import config

with_ride = 0


def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in kilometers using Haversine formula"""
    R = 6371  # Earth's radius in km

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = math.sin(
        delta_lat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(
            delta_lon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return round(R * c, 1)


def locationTracker(addrs):
    params = {
        'format': 'json',
        'q': addrs,
    }

    response = requests.get('https://nominatim.openstreetmap.org/search',
                            params=params,
                            headers=loc_headers)

    return [response.json()[0]['lat'], response.json()[0]['lon']]


def refreshToken(cookies, headers, refresh_token):
    json_data = {
        'request': {
            'scope': [],
            'grantType': 'REFRESH_TOKEN',
            'clientID': 'zozycDbnl17oSjKXdw_x_QuNvq5wfRHq',
            'refreshToken': refresh_token,
        },
    }

    response = requests.post(
        'https://cn-geo1.uber.com/rt/identity/oauth2/token',
        cookies=cookies,
        headers=headers,
        json=json_data)

    return response.json()['accessToken']


def vehicleDetails(cookies, headers, refresh_token):
    params = {'includeInaccessible': 'false'}

    headers = dict(headers)
    headers['authorization'] = 'Bearer ' + refreshToken(
        cookies, headers, refresh_token)

    response = requests.get('https://cn-geo1.uber.com/rt/drivers/v2/vehicles',
                            params=params,
                            cookies=cookies,
                            headers=headers)

    return response.json()['vehicles']


def appLaunch(cookies, headers, refresh_token):
    global with_ride

    json_data = {
        'launchParams': {},
    }

    headers = dict(headers)
    headers['authorization'] = 'Bearer ' + refreshToken(
        cookies, headers, refresh_token)

    try:
        # dont delete this
        # response = requests.post(
        #     'https://cn-geo1.uber.com/rt/drivers/app-launch',
        #     cookies=cookies,
        #     headers=headers,
        #     json=json_data)
        response = requests.get('https://pastebin.com/raw/SYMDNfFL')
        data = response.json()
    except Exception as e:
        print(f"Error fetching app launch data: {e}")
        return [0, None]

    task_scopes = data.get('driverTasks', {}).get('taskScopes', [])
    if len(task_scopes) == 0:
        print("No Ride Found")
        return [0, data]

    try:
        print("Ride Found")
        ride_type = task_scopes[0]['completionTask']['coalescedDataUnion'][
            'pickupCoalescedTaskData']['product']['name']
        job_id = task_scopes[0]['nonBlockingTasks'][0]['driverTaskDataUnion'][
            'singleTaskData']['taskSourceKeyOption']['taskSourceKey'][
                'taskSourceUuid']
        first_name = task_scopes[0]['completionTask']['taskDataMap'][job_id][
            'pickupTaskData']['entity']['firstName']
        last_name = task_scopes[0]['completionTask']['taskDataMap'][job_id][
            'pickupTaskData']['entity']['lastName']
        rating = task_scopes[0]['completionTask']['taskDataMap'][job_id][
            'pickupTaskData']['entity']['rating']

        pickup_address = "Address unavailable"
        drop_off_address = "Destination unavailable"
        trip_distance = None
        pickup_coords = None
        dropoff_coords = None
        trip_status = "Unknown"

        try:
            trip_status = task_scopes[0]['completionTask'][
                'coalescedDataUnion']['pickupCoalescedTaskData']['info'][
                    'status']
        except (KeyError, IndexError):
            pass

        try:
            all_location_tasks = []
            for scope in task_scopes:
                for task in scope.get('nonBlockingTasks', []):
                    task_data = task.get('driverTaskDataUnion',
                                         {}).get('singleTaskData',
                                                 {}).get('taskDataUnion', {})
                    if 'locationTaskData' in task_data:
                        all_location_tasks.append(
                            task_data['locationTaskData'])

            if len(all_location_tasks) >= 1:
                loc = all_location_tasks[0]
                title = loc.get('title', '')
                subtitle = loc.get('subtitle', '')
                if title:
                    pickup_address = f"{title}, {subtitle}".strip(', ')
                pickup_coords = (loc.get('latitude'), loc.get('longitude'))

            if len(all_location_tasks) >= 2:
                loc = all_location_tasks[1]
                title = loc.get('title', '')
                subtitle = loc.get('subtitle', '')
                if title:
                    drop_off_address = f"{title}, {subtitle}".strip(', ')
                dropoff_coords = (loc.get('latitude'), loc.get('longitude'))

            json_data = {
                'operationName': 'Products',
                'variables': {
                    'includeRecommended':
                    False,
                    'destinations': [
                        {
                            'latitude': dropoff_coords[0],
                            'longitude': dropoff_coords[1],
                        },
                    ],
                    'payment': {
                        'paymentProfileUUID':
                        '33ec509c-9a7f-57d4-ad1f-3124df7586c8',
                        'uberCashToggleOn': True,
                    },
                    'paymentProfileUUID':
                    '33ec509c-9a7f-57d4-ad1f-3124df7586c8',
                    'pickup': {
                        'latitude': pickup_coords[0],
                        'longitude': pickup_coords[1],
                    },
                },
                'query': fare_query,
            }

            response = requests.post('https://m.uber.com/go/graphql',
                                     cookies=fare_cookies,
                                     headers=fare_headers,
                                     json=json_data)
            if pickup_coords and dropoff_coords and all(pickup_coords) and all(
                    dropoff_coords):
                trip_distance = calculate_distance(pickup_coords[0],
                                                   pickup_coords[1],
                                                   dropoff_coords[0],
                                                   dropoff_coords[1])
        except (KeyError, IndexError):
            pass

        fare_price = None
        eta_minutes = None
        fare_distance = None
        ride_type_image = None

        ride_type_images = {
            'uberx':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberX_v1.png',
            'uberxl':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberXL_v1.png',
            'uber comfort':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberComfort_v1.png',
            'uber black':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/Black_v1.png',
            'uber green':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/Green_v1.png',
            'uber pet':
            'https://d1a3f4spazzrp4.cloudfront.net/car-types/haloProductImages/v1.1/UberX_v1.png',
        }

        ride_type_lower = ride_type.lower()
        for key, img_url in ride_type_images.items():
            if key in ride_type_lower or ride_type_lower in key:
                ride_type_image = img_url
                break
        if not ride_type_image:
            ride_type_image = ride_type_images.get('uberx')

        try:
            if response and response.status_code == 200:
                products_data = response.json()
                tiers = products_data.get('data', {}).get('products',
                                                          {}).get('tiers', [])

                for tier in tiers:
                    for product in tier.get('products', []):
                        product_name = product.get('displayName', '').lower()
                        if ride_type.lower(
                        ) in product_name or product_name in ride_type.lower():
                            fares_list = product.get('fares', [])
                            if fares_list:
                                pre_adj = fares_list[0].get(
                                    'preAdjustmentValue')
                                raw_fare = pre_adj if pre_adj else fares_list[
                                    0].get('fare')
                                if raw_fare:
                                    fare_num = float(''.join(
                                        c for c in raw_fare
                                        if c.isdigit() or c == '.'))
                                    fare_after_cut = fare_num * 0.73
                                    currency = ''.join(
                                        c for c in raw_fare
                                        if not c.isdigit() and c != '.')
                                    fare_price = f"~{currency}{fare_after_cut:.2f}"
                                meta_str = fares_list[0].get('meta', '{}')
                                try:
                                    meta_data = json.loads(meta_str)
                                    unmod_dist = meta_data.get(
                                        'upfrontFare',
                                        {}).get('unmodifiedDistance')
                                    if unmod_dist:
                                        trip_distance = round(
                                            unmod_dist / 1000, 1)
                                except:
                                    pass
                            estimated_seconds = product.get(
                                'estimatedTripTime')
                            eta_short = product.get('etaStringShort', '')
                            eta_short_secs = 0
                            if eta_short:
                                try:
                                    eta_short_secs = int(''.join(
                                        filter(str.isdigit, eta_short))) * 60
                                except:
                                    eta_short_secs = 0
                            if estimated_seconds:
                                trip_time_seconds = estimated_seconds - eta_short_secs
                                eta_minutes = round(trip_time_seconds / 60)
                            if product.get('productImageUrl'):
                                ride_type_image = product.get(
                                    'productImageUrl')
                            break
                    if fare_price:
                        break

                if not fare_price and tiers:
                    first_product = tiers[0].get(
                        'products',
                        [{}])[0] if tiers[0].get('products') else {}
                    fares_list = first_product.get('fares', [])
                    if fares_list:
                        pre_adj = fares_list[0].get('preAdjustmentValue')
                        raw_fare = pre_adj if pre_adj else fares_list[0].get(
                            'fare')
                        if raw_fare:
                            fare_num = float(''.join(
                                c for c in raw_fare
                                if c.isdigit() or c == '.'))
                            fare_after_cut = fare_num * 0.73
                            currency = ''.join(c for c in raw_fare
                                               if not c.isdigit() and c != '.')
                            fare_price = f"~{currency}{fare_after_cut:.2f}"
                        meta_str = fares_list[0].get('meta', '{}')
                        try:
                            meta_data = json.loads(meta_str)
                            unmod_dist = meta_data.get(
                                'upfrontFare', {}).get('unmodifiedDistance')
                            if unmod_dist:
                                trip_distance = round(unmod_dist / 1000, 1)
                        except:
                            pass
                    estimated_seconds = first_product.get('estimatedTripTime')
                    eta_short = first_product.get('etaStringShort', '')
                    eta_short_secs = 0
                    if eta_short:
                        try:
                            eta_short_secs = int(''.join(
                                filter(str.isdigit, eta_short))) * 60
                        except:
                            eta_short_secs = 0
                    if estimated_seconds:
                        trip_time_seconds = estimated_seconds - eta_short_secs
                        eta_minutes = round(trip_time_seconds / 60)
                    if first_product.get('productImageUrl'):
                        ride_type_image = first_product.get('productImageUrl')
        except Exception as e:
            print(f"Error parsing product pricing: {e}")

        full_name = f"{first_name} {last_name}".strip()
        with_ride = 1

        return {
            'ride_type': ride_type,
            'full_name': full_name,
            'rating': rating,
            'pickup_address': pickup_address,
            'drop_off_address': drop_off_address,
            'trip_distance': trip_distance,
            'trip_status': trip_status,
            'pickup_coords': pickup_coords,
            'dropoff_coords': dropoff_coords,
            'fare_price': fare_price,
            'eta_minutes': eta_minutes,
            'fare_distance': fare_distance,
            'ride_type_image': ride_type_image
        }
    except (KeyError, IndexError) as e:
        print(f"Error parsing ride data: {e}")
        return [0, data]


def driverLocation(address, cookies, headers, refresh_token):
    print(f'Location Moved to: {address}')
    driverTasks = appLaunch(cookies, headers, refresh_token)[1]
    lat, long = locationTracker(address)
    time_stamp = int(driverTasks['driverTasks']['meta']['lastModifiedTimeMs'])

    headers = dict(headers)

    try:
        while True:
            if config.stop_signal == 1:
                print("Stop signal detected. Breaking driverLocation loop.")
                config.stop_signal = 0
                break

            if with_ride == 1:
                print("Ride in progress. Breaking driverLocation loop.")
                break
            json_data = {
                'data': {
                    'positions': [
                        {
                            'positionNavigationData': {
                                'location': {
                                    'allTimestamps': [
                                        {
                                            'ts': time_stamp,
                                        },
                                    ],
                                    'latitude': float(lat),
                                    'speed': -1,
                                    'course': -1,
                                    'horizontalAccuracy': 3.6507954947581602,
                                    'provider': 'ios_core',
                                    'verticalAccuracy': 30,
                                    'altitude': 30.969567390469884,
                                    'bestTimestamp': {
                                        'ts': time_stamp,
                                    },
                                    'longitude': float(long),
                                },
                            },
                        },
                    ],
                },
            }
            headers['authorization'] = 'Bearer ' + refreshToken(
                cookies, headers, refresh_token)
            response = requests.post(
                'https://cn-geo1.uber.com/rt/locations/v1/upload-driver-device-locations',
                cookies=cookies,
                headers=headers,
                json=json_data,
            )
            time_stamp += 4000
            print(response.json())

            time.sleep(2)
    except:
        print("Location Issue!!!")
    return


def updateLocationOnce(lat, lng, cookies, headers, refresh_token):
    """Update driver location once with given coordinates"""
    headers = dict(headers)
    time_stamp = int(time.time() * 1000)

    headers['authorization'] = 'Bearer ' + refreshToken(
        cookies, headers, refresh_token)

    json_data = {
        'data': {
            'positions': [
                {
                    'positionNavigationData': {
                        'location': {
                            'allTimestamps': [{
                                'ts': time_stamp
                            }],
                            'latitude': float(lat),
                            'speed': -1,
                            'course': -1,
                            'horizontalAccuracy': 3.6507954947581602,
                            'provider': 'ios_core',
                            'verticalAccuracy': 30,
                            'altitude': 30.969567390469884,
                            'bestTimestamp': {
                                'ts': time_stamp
                            },
                            'longitude': float(lng),
                        },
                    },
                },
            ],
        },
    }

    response = requests.post(
        'https://cn-geo1.uber.com/rt/locations/v1/upload-driver-device-locations',
        cookies=cookies,
        headers=headers,
        json=json_data,
    )
    return response.json()


def driverInfo(cookies, headers, refresh_token):
    headers = dict(headers)
    headers['authorization'] = 'Bearer ' + refreshToken(
        cookies, headers, refresh_token)

    params = {
        'localeCode': 'en',
    }

    json_data = {}

    response = requests.post('https://account.uber.com/api/getUserInfo',
                             params=params,
                             cookies=cookies,
                             headers=headers,
                             json=json_data)
    name = response.json(
    )['data']['userInfo']['name']['firstname'] + ' ' + response.json(
    )['data']['userInfo']['name']['lastname']
    photo = response.json()['data']['userInfo']['photo']['photoURL']

    return [name, photo]


def flightArrivals(terminal=None, include_tomorrow=True):
    from bs4 import BeautifulSoup
    from datetime import datetime, timezone, timedelta

    try:
        perth_tz = timezone(timedelta(hours=8))
        perth_now = datetime.now(perth_tz)
        current_hour = perth_now.hour

        all_flights = []
        terminals_found = set()

        urls_to_fetch = [('https://www.airport-perth.com/arrivals.php',
                          'today')]

        if include_tomorrow and current_hour >= 20:
            tomorrow = perth_now + timedelta(days=1)
            tomorrow_str = tomorrow.strftime('%Y-%m-%d')
            urls_to_fetch.append((
                f'https://www.airport-perth.com/arrivals.php?d={tomorrow_str}',
                'tomorrow'))

        for url, day_label in urls_to_fetch:
            try:
                response = requests.get(
                    url,
                    headers={
                        'User-Agent':
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept':
                        'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    },
                    timeout=15)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')

                    flight_rows = soup.find_all('div', class_='flight-row')
                    for row in flight_rows:
                        if 'flight-titol' in row.get('class', []):
                            continue

                        time_elem = row.find('div', class_='flight-col__hour')
                        origin_elem = row.find('div',
                                               class_='flight-col__dest-term')
                        flight_elem = row.find(
                            'a', class_='flight-col__flight--link')
                        status_elem = row.find('div',
                                               class_='flight-col__status')
                        terminal_elem = row.find('div',
                                                 class_='flight-col__terminal')

                        if time_elem:
                            time_str = time_elem.get_text(strip=True)
                            origin = origin_elem.get_text(
                                strip=True) if origin_elem else ''
                            flight_num = flight_elem.get_text(
                                strip=True) if flight_elem else ''
                            status = status_elem.get_text(
                                strip=True) if status_elem else ''
                            term = terminal_elem.get_text(
                                strip=True) if terminal_elem else ''

                            if day_label == 'tomorrow':
                                is_landed = False
                            else:
                                is_landed = 'landed' in status.lower()

                            if term:
                                terminals_found.add(term)

                            if terminal and term != terminal:
                                continue

                            all_flights.append({
                                'time':
                                time_str,
                                'flight':
                                flight_num,
                                'origin':
                                origin,
                                'status':
                                status
                                if day_label == 'today' else 'Scheduled',
                                'terminal':
                                term,
                                'day':
                                day_label,
                                'landed':
                                is_landed
                            })

                    print(
                        f"Scraped {day_label}: {len([f for f in all_flights if f.get('day') == day_label])} flights"
                    )
                else:
                    print(
                        f"Flight API returned status {response.status_code} for {day_label}"
                    )
            except Exception as e:
                print(f"Error fetching {day_label} flights: {e}")

        print(
            f"Total scraped: {len(all_flights)} flights (terminals: {sorted(terminals_found)})"
        )

        class MockResponse:

            def __init__(self, data):
                self._data = data
                self.status_code = 200
                self.text = str(data)

            def json(self):
                return self._data

        return MockResponse({
            'flights': all_flights,
            'source': 'airport-perth.com',
            'terminals': sorted(terminals_found)
        })

    except Exception as e:
        print(f"Flight API request failed: {e}")
        return None


def parseFlightsByHour(response_data):
    from collections import defaultdict
    import re

    hourly_flights = defaultdict(int)
    for hour in range(24):
        hourly_flights[hour] = 0

    try:
        flights = response_data.get('flights', [])

        for flight in flights:
            scheduled_time = flight.get('scheduledTime', '') or flight.get(
                'time', '') or flight.get('arrivalTime', '')

            if not scheduled_time:
                for key, value in flight.items():
                    if isinstance(value, str) and ':' in value:
                        time_match = re.search(r'(\d{1,2}):(\d{2})', value)
                        if time_match:
                            scheduled_time = value
                            break

            if scheduled_time:
                time_match = re.search(r'(\d{1,2}):(\d{2})', scheduled_time)
                if time_match:
                    hour = int(time_match.group(1))
                    if 0 <= hour < 24:
                        hourly_flights[hour] += 1
    except Exception as e:
        print(f"Error parsing flights: {e}")

    result = []
    for hour in range(24):
        result.append({
            'hour': hour,
            'time_label': f"{hour:02d}:00",
            'count': hourly_flights[hour]
        })

    return result


def uberAuth(country_code, phone_number):
    """
    Initiate Uber authentication with phone number.
    Returns dict with success status, session_id, or captcha info.
    """
    import uuid

    device_udid = str(uuid.uuid4()).upper()
    hot_launch_id = str(uuid.uuid4()).upper()
    cold_launch_id = str(uuid.uuid4()).upper()
    request_uuid = str(uuid.uuid4())

    try:
        headers = {
            'Host': 'cn-geo1.uber.com',
            'referer': 'https://auth.uber.com/',
            'user-agent':
            'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15',
            'x-uber-client-version': '4.524.10000',
            'x-uber-client-name': 'driver',
            'origin': 'https://auth.uber.com',
            'x-uber-cold-launch-id': cold_launch_id,
            'x-uber-hot-launch-id': hot_launch_id,
            'accept-language': 'en-AU',
            'x-uber-request-uuid': request_uuid,
            'x-uber-usl-id': device_udid,
            'x-uber-device-udid': device_udid,
            'x-uber-client-id': 'com.ubercab.UberPartner',
            'accept': '*/*',
            'content-type': 'application/json',
            'x-uber-device': 'iphone',
        }

        json_data = {
            'formContainerAnswer': {
                'inAuthSessionID': '',
                'formAnswer': {
                    'flowType':
                    'INITIAL',
                    'standardFlow':
                    True,
                    'accountManagementFlow':
                    False,
                    'daffFlow':
                    False,
                    'productConstraints': {
                        'isEligibleForWebOTPAutofill': False,
                        'isWhatsAppAvailable': False,
                        'isPublicKeyCredentialSupported': True,
                    },
                    'additionalParams': {
                        'isEmailUpdatePostAuth': False,
                    },
                    'deviceData':
                    '',
                    'codeChallenge':
                    'wdxgrpoDP_smdsYGSoKPcPIOXhpUzNXpmkAvr-r8Oxo',
                    'firstPartyClientID':
                    'SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7',
                    'screenAnswers': [
                        {
                            'screenType':
                            'PHONE_NUMBER_INITIAL',
                            'eventType':
                            'TypeInputMobile',
                            'fieldAnswers': [
                                {
                                    'fieldType': 'PHONE_COUNTRY_CODE',
                                    'phoneCountryCode': country_code
                                },
                                {
                                    'fieldType': 'PHONE_NUMBER',
                                    'phoneNumber': phone_number
                                },
                            ],
                        },
                    ],
                },
            },
        }

        response = requests.post(
            'https://cn-geo1.uber.com/rt/silk-screen/submit-form',
            headers=headers,
            json=json_data,
            timeout=15)

        result = response.json()
        print(f"Uber auth response: {result}")

        session_id = result.get('inAuthSessionID', '')

        if session_id:
            next_screen = result.get('form', {}).get('screens', [])
            screen_type = next_screen[0].get('screenType',
                                             '') if next_screen else ''

            if screen_type == 'PHONE_OTP' or 'OTP' in screen_type:
                return {
                    'success': True,
                    'session_id': session_id,
                    'device_udid': device_udid,
                    'headers': dict(headers)
                }

        if 'error' in result or result.get('status') == 'FAILURE':
            error_msg = result.get('error', {}).get('message',
                                                    'Authentication failed')
            return {
                'success':
                False,
                'error':
                error_msg,
                'needs_captcha':
                'captcha' in str(error_msg).lower()
                or 'challenge' in str(error_msg).lower()
            }

        return {
            'success': False,
            'error': 'Unexpected response from Uber',
            'needs_captcha': False
        }

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Request timed out', 'can_request_voice': True, 'session_id': ''}
    except Exception as e:
        print(f"Uber auth error: {e}")
        return {'success': False, 'error': str(e), 'can_request_voice': True, 'session_id': ''}


def uberVoiceOTP(session_id, country_code, phone_number):
    """
    Request voice call OTP as fallback when SMS fails.
    Uses PHONE_VOICE_OTP screen type.
    """
    import uuid

    device_udid = str(uuid.uuid4()).upper()
    hot_launch_id = str(uuid.uuid4()).upper()
    cold_launch_id = str(uuid.uuid4()).upper()
    request_uuid = str(uuid.uuid4())

    try:
        headers = {
            'Host': 'cn-geo1.uber.com',
            'referer': 'https://auth.uber.com/',
            'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15',
            'x-uber-client-version': '4.524.10000',
            'x-uber-client-name': 'driver',
            'origin': 'https://auth.uber.com',
            'x-uber-cold-launch-id': cold_launch_id,
            'x-uber-hot-launch-id': hot_launch_id,
            'accept-language': 'en-AU',
            'x-uber-request-uuid': request_uuid,
            'x-uber-usl-id': device_udid,
            'x-uber-device-udid': device_udid,
            'x-uber-client-id': 'com.ubercab.UberPartner',
            'accept': '*/*',
            'content-type': 'application/json',
            'x-uber-device': 'iphone',
        }

        json_data = {
            'formContainerAnswer': {
                'inAuthSessionID': session_id if session_id else '',
                'formAnswer': {
                    'flowType': 'INITIAL',
                    'standardFlow': True,
                    'accountManagementFlow': False,
                    'daffFlow': False,
                    'productConstraints': {
                        'isEligibleForWebOTPAutofill': False,
                        'isWhatsAppAvailable': False,
                        'isPublicKeyCredentialSupported': True,
                    },
                    'additionalParams': {
                        'isEmailUpdatePostAuth': False,
                    },
                    'deviceData': '',
                    'codeChallenge': 'wdxgrpoDP_smdsYGSoKPcPIOXhpUzNXpmkAvr-r8Oxo',
                    'firstPartyClientID': 'SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7',
                    'screenAnswers': [
                        {
                            'screenType': 'PHONE_VOICE_OTP',
                            'eventType': 'TypeVoiceOTP',
                            'fieldAnswers': [
                                {
                                    'fieldType': 'PHONE_VOICE_OTP',
                                    'phoneCountryCode': country_code,
                                    'phoneNumber': phone_number
                                },
                            ],
                        },
                    ],
                },
            },
        }

        response = requests.post(
            'https://cn-geo1.uber.com/rt/silk-screen/submit-form',
            headers=headers,
            json=json_data,
            timeout=15)

        result = response.json()
        print(f"Uber voice OTP response: {result}")

        new_session_id = result.get('inAuthSessionID', session_id)

        if new_session_id:
            return {
                'success': True,
                'session_id': new_session_id,
                'device_udid': device_udid,
                'headers': dict(headers),
                'message': 'Voice call initiated'
            }

        if 'error' in result:
            error_msg = result.get('error', {}).get('message', 'Voice call failed')
            return {'success': False, 'error': error_msg}

        return {'success': False, 'error': 'Unexpected response from Uber'}

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Request timed out'}
    except Exception as e:
        print(f"Uber voice OTP error: {e}")
        return {'success': False, 'error': str(e)}


def uberVerifyCode(session_id, code):
    """
    Verify SMS code and complete authentication.
    Returns dict with success status, email requirement, or credentials.
    """
    import uuid

    device_udid = str(uuid.uuid4()).upper()

    try:
        headers = {
            'Host': 'cn-geo1.uber.com',
            'referer': 'https://auth.uber.com/',
            'user-agent':
            'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Mobile/15E148 Safari/604.1',
            'x-uber-client-version': '4.524.10000',
            'x-uber-client-name': 'driver',
            'origin': 'https://auth.uber.com',
            'accept-language': 'en-AU',
            'x-uber-device-udid': device_udid,
            'x-uber-client-id': 'com.ubercab.UberPartner',
            'accept': '*/*',
            'content-type': 'application/json',
            'x-uber-device': 'iphone',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
        }

        json_data = {
            'formContainerAnswer': {
                'inAuthSessionID': session_id,
                'formAnswer': {
                    'flowType':
                    'INITIAL',
                    'standardFlow':
                    True,
                    'accountManagementFlow':
                    False,
                    'daffFlow':
                    False,
                    'screenAnswers': [
                        {
                            'screenType':
                            'PHONE_OTP',
                            'eventType':
                            'TypeSMSOTP',
                            'fieldAnswers': [
                                {
                                    'fieldType': 'PHONE_SMS_OTP',
                                    'phoneSMSOTP': code
                                },
                            ],
                        },
                    ],
                },
            },
        }

        response = requests.post(
            'https://cn-geo1.uber.com/rt/silk-screen/submit-form',
            headers=headers,
            json=json_data,
            timeout=15)

        result = response.json()
        print(f"Uber SMS verify response: {result}")

        new_session_id = result.get('inAuthSessionID', session_id)

        next_screen = result.get('form', {}).get('screens', [])
        if next_screen:
            screen_type = next_screen[0].get('screenType', '')

            if screen_type == 'EMAIL_OTP' or 'EMAIL' in screen_type:
                email_hint = ''
                for field in next_screen[0].get('fields', []):
                    if field.get('hintValue'):
                        email_hint = field.get('hintValue', '')
                        break

                return {
                    'success': True,
                    'needs_email_otp': True,
                    'session_id': new_session_id,
                    'email_hint': email_hint
                }

        if 'cookies' in result or 'accessToken' in str(result):
            cookies_from_response = result.get('cookies', {})
            headers_from_response = dict(headers)

            access_token = result.get('accessToken')
            refresh_token = result.get('refreshToken')

            if access_token:
                headers_from_response[
                    'authorization'] = f'Bearer {access_token}'

            return {
                'success': True,
                'needs_email_otp': False,
                'cookies': cookies_from_response,
                'headers': headers_from_response,
                'refresh_token': refresh_token or ''
            }

        if 'error' in result:
            error_msg = result.get('error', {}).get('message',
                                                    'Verification failed')
            return {'success': False, 'error': error_msg}

        return {
            'success': True,
            'needs_email_otp': False,
            'session_id': new_session_id,
            'raw_response': result
        }

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Request timed out'}
    except Exception as e:
        print(f"Uber verify error: {e}")
        return {'success': False, 'error': str(e)}


def uberEmailVerify(session_id, OTP):
    """
    Verify email OTP and complete authentication.
    Returns dict with success status and credentials.
    """
    headers = {
        'Host': 'cn-geo1.uber.com',
        'content-type': 'application/json',
        'x-uber-request-uuid': '10ab7ee6-ed10-49da-94d7-0ec7b6116d0b',
        'accept': '*/*',
        'x-uber-device': 'iphone',
        'x-uber-device-udid': 'E8B68CCC-9E56-4952-B2BC-DA152E3B4965',
        'sec-fetch-site': 'same-site',
        'x-uber-cold-launch-id': 'CFEE8047-DBF5-4291-BAE0-8660964CA5C5',
        'x-uber-client-version': '4.524.10000',
        'x-uber-client-name': 'driver',
        'accept-language': 'en-AU',
        'x-uber-usl-id': 'E8B68CCC-9E56-4952-B2BC-DA152E3B4965',
        'sec-fetch-mode': 'cors',
        'origin': 'https://auth.uber.com',
        'user-agent':
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.2 Mobile/15E148 Safari/604.1',
        'referer': 'https://auth.uber.com/',
        'x-uber-hot-launch-id': '5C4C82CC-0316-45F7-B968-91D16E55DE3F',
        'x-uber-client-id': 'com.ubercab.UberPartner',
        'sec-fetch-dest': 'empty',
        'priority': 'u=3, i',
    }

    json_data = {
        'formContainerAnswer': {
            'inAuthSessionID': session_id,
            'formAnswer': {
                'flowType':
                'SIGN_IN',
                'standardFlow':
                True,
                'accountManagementFlow':
                False,
                'daffFlow':
                False,
                'productConstraints': {
                    'isEligibleForWebOTPAutofill': False,
                    'uslFELibVersion': '',
                    'uslMobileLibVersion': '',
                    'isWhatsAppAvailable': False,
                    'isPublicKeyCredentialSupported': True,
                    'isFacebookAvailable': False,
                    'isRakutenAvailable': False,
                    'isKakaoAvailable': False,
                },
                'additionalParams': {
                    'isEmailUpdatePostAuth': False,
                },
                'deviceData':
                '',
                'codeChallenge':
                'wdxgrpoDP_smdsYGSoKPcPIOXhpUzNXpmkAvr-r8Oxo',
                'uslURL':
                'https://auth.uber.com/v2?x-uber-device=iphone&x-uber-client-name=driver&x-uber-client-version=4.524.10000&x-uber-client-id=com.ubercab.UberPartner&firstPartyClientID=SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7&isiOSCustomTabSessionClose=true&showPasskeys=true&sim_mcc=65535&x-uber-hot-launch-id=5C4C82CC-0316-45F7-B968-91D16E55DE3F&x-uber-cold-launch-id=CFEE8047-DBF5-4291-BAE0-8660964CA5C5&is_root=false&known_user=false&context_usl=true&guest_mode=false&codeChallenge=wdxgrpoDP_smdsYGSoKPcPIOXhpUzNXpmkAvr-r8Oxo&x-uber-device-udid=E8B68CCC-9E56-4952-B2BC-DA152E3B4965&countryCode=AU',
                'firstPartyClientID':
                'SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7',
                'screenAnswers': [
                    {
                        'screenType':
                        'EMAIL_OTP_CODE',
                        'eventType':
                        'TypeEmailOTP',
                        'fieldAnswers': [
                            {
                                'fieldType': 'EMAIL_OTP_CODE',
                                'emailOTPCode': OTP,
                            },
                        ],
                    },
                ],
            },
        },
    }

    try:
        response = requests.post(
            'https://cn-geo1.uber.com/rt/silk-screen/submit-form',
            headers=headers,
            json=json_data,
            timeout=15)
        result = response.json()
        print(f"Uber email verify response: {result}")

        response_cookies = {}
        for cookie in response.cookies:
            response_cookies[cookie.name] = cookie.value

        auth_code = None
        new_session_id = result.get('formContainerResponse',
                                    {}).get('outAuthSessionID', session_id)

        screen_responses = result.get('formContainerResponse',
                                      {}).get('nextScreens', [])
        for screen in screen_responses:
            if screen.get('screenType') == 'SESSION_VERIFICATION':
                for field in screen.get('fieldValues', []):
                    if field.get('fieldType') == 'SESSION_VERIFICATION_CODE':
                        auth_code = field.get('sessionVerificationCode')
                        break

        if auth_code:
            return {
                'success': True,
                'needs_authentication': True,
                'auth_code': auth_code,
                'session_id': new_session_id,
                'cookies': response_cookies,
                'headers': dict(headers)
            }

        if 'cookies' in result:
            cookies_from_response = result.get('cookies', {})
            headers_from_response = dict(headers)

            access_token = result.get('accessToken')
            refresh_token = result.get('refreshToken')

            if access_token:
                headers_from_response[
                    'authorization'] = f'Bearer {access_token}'

            return {
                'success': True,
                'needs_authentication': False,
                'cookies': cookies_from_response,
                'headers': headers_from_response,
                'refresh_token': refresh_token or ''
            }

        if 'error' in result:
            error_msg = result.get('error',
                                   {}).get('message',
                                           'Email verification failed')
            return {'success': False, 'error': error_msg}

        return {
            'success': True,
            'needs_authentication': False,
            'cookies': response_cookies,
            'headers': dict(headers),
            'refresh_token': '',
            'session_id': new_session_id,
            'raw_response': result
        }

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Request timed out'}
    except Exception as e:
        print(f"Uber email verify error: {e}")
        return {'success': False, 'error': str(e)}


def uberAuthention(headers, cookies, session_id, auth_code):
    """
    Complete authentication with session verification code.
    Returns cookies, headers, and refresh_token for user storage.
    """
    json_data = {
        'formContainerAnswer': {
            'formAnswer': {
                'screenAnswers': [
                    {
                        'screenType':
                        'SESSION_VERIFICATION',
                        'fieldAnswers': [
                            {
                                'fieldType': 'SESSION_VERIFICATION_CODE',
                                'sessionVerificationCode': auth_code,
                            },
                            {
                                'fieldType':
                                'CODE_VERIFIER',
                                'codeVerifier':
                                'TavK2-77HDPiA8yXnxn_BI7w3NLpmW3H7fq3xyx7Xb9OSmxUumPWUQ_ARn3zzCzCuDUgOze2D1fXdgwL4iUia1jb',
                            },
                        ],
                        'eventType':
                        'TypeVerifySession',
                    },
                ],
                'firstPartyClientID':
                'SCjGHreCKCVv4tDuhi7KTYA4yLZCKgK7',
                'standardFlow':
                True,
                'flowType':
                'SIGN_IN',
            },
            'inAuthSessionID': session_id,
        },
    }

    try:
        response = requests.post(
            'https://cn-geo1.uber.com/rt/silk-screen/submit-form',
            cookies=cookies,
            headers=headers,
            json=json_data,
            timeout=15)
        result = response.json()
        print(f"Uber authentication response: {result}")

        response_cookies = dict(cookies) if cookies else {}
        for cookie in response.cookies:
            response_cookies[cookie.name] = cookie.value

        if 'cookies' in result:
            for key, value in result.get('cookies', {}).items():
                response_cookies[key] = value

        headers_from_response = dict(headers) if headers else {}

        access_token = result.get('accessToken')
        refresh_token = result.get('refreshToken')

        if access_token:
            headers_from_response['authorization'] = f'Bearer {access_token}'

        if access_token or refresh_token or response_cookies:
            return {
                'success': True,
                'cookies': response_cookies,
                'headers': headers_from_response,
                'refresh_token': refresh_token or '',
                'access_token': access_token or ''
            }

        if 'error' in result:
            error_msg = result.get('error', {}).get('message',
                                                    'Authentication failed')
            return {'success': False, 'error': error_msg}

        return {
            'success': True,
            'cookies': response_cookies,
            'headers': headers_from_response,
            'refresh_token': '',
            'raw_response': result
        }

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Request timed out'}
    except Exception as e:
        print(f"Uber authentication error: {e}")
        return {'success': False, 'error': str(e)}
